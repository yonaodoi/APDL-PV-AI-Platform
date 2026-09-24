from datetime import datetime, timezone

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from app.audit import write_audit_log
from app.db import query_all, query_one, transaction
from app.security import login_required
from .forms import SafetyCaseForm
from app.services.signal_detection import (
    detect_potential_signals_for_case,
)
from app.services.case_completeness import (
    refresh_case_completeness,
)


bp = Blueprint("cases", __name__, url_prefix="/cases")


@bp.get("/")
@login_required
def case_list():
    selected_product = request.args.get("product", "").strip()
    selected_country = request.args.get("country", "").strip()
    selected_status = request.args.get("status", "").strip()
    selected_priority = request.args.get("priority", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    filters = []
    parameters = []

    if selected_product:
        filters.append("case_products.product_name = %s")
        parameters.append(selected_product)

    if selected_country:
        filters.append("countries.country_name = %s")
        parameters.append(selected_country)

    if selected_status:
        filters.append("safety_cases.workflow_status = %s")
        parameters.append(selected_status)

    if selected_priority == "Serious":
        filters.append("safety_cases.seriousness = TRUE")

    if selected_priority == "Routine":
        filters.append("safety_cases.seriousness = FALSE")

    if start_date:
        filters.append("safety_cases.received_date >= %s")
        parameters.append(start_date)

    if end_date:
        filters.append("safety_cases.received_date <= %s")
        parameters.append(end_date)

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    cases = query_all(
        f"""
        SELECT
            safety_cases.case_id,
            safety_cases.case_number,
            safety_cases.workflow_status,
            safety_cases.received_date,
            safety_cases.seriousness,
            safety_cases.event_description,
            countries.country_name,
            case_products.product_name
        FROM pv.safety_cases AS safety_cases
        LEFT JOIN pv.countries AS countries
            ON countries.country_id = safety_cases.country_id
        LEFT JOIN pv.case_products AS case_products
            ON case_products.case_id = safety_cases.case_id
        {where_clause}
        ORDER BY safety_cases.created_at DESC
        """,
        tuple(parameters),
    )

    products = query_all(
        """
        SELECT DISTINCT product_name
        FROM pv.case_products
        WHERE product_name IS NOT NULL
          AND product_name <> ''
        ORDER BY product_name
        """
    )

    countries = query_all(
        """
        SELECT country_name
        FROM pv.countries
        ORDER BY country_name
        """
    )

    statuses = query_all(
        """
        SELECT DISTINCT workflow_status
        FROM pv.safety_cases
        WHERE workflow_status IS NOT NULL
        ORDER BY workflow_status
        """
    )

    return render_template(
        "cases/case_list.html",
        cases=cases,
        products=products,
        countries=countries,
        statuses=statuses,
        selected_product=selected_product,
        selected_country=selected_country,
        selected_status=selected_status,
        selected_priority=selected_priority,
        start_date=start_date,
        end_date=end_date,
    )

@bp.get("/<int:case_id>")
@login_required
def case_detail(case_id):
    case = query_one(
        """
        SELECT
            safety_cases.*,
            countries.country_name,
            users.full_name AS created_by_name
        FROM pv.safety_cases AS safety_cases
        LEFT JOIN pv.countries AS countries
            ON countries.country_id = safety_cases.country_id
        LEFT JOIN pv.users AS users
            ON users.user_id = safety_cases.created_by
        WHERE safety_cases.case_id = %s
        """,
        (case_id,),
    )
    if case is None:
        abort(404)

    products = query_all(
        """
        SELECT *
        FROM pv.case_products
        WHERE case_id = %s
        ORDER BY case_product_id
        """,
        (case_id,),
    )
    completeness_checks = refresh_case_completeness(case, products)

    audit_log = query_all(
        """
        SELECT
            case_audit_log.action,
            case_audit_log.details,
            case_audit_log.performed_at,
            users.full_name
        FROM pv.case_audit_log AS case_audit_log
        LEFT JOIN pv.users AS users
            ON users.user_id = case_audit_log.performed_by
        WHERE case_audit_log.case_id = %s
        ORDER BY case_audit_log.performed_at DESC
        """,
        (case_id,),
    )

    return render_template(
        "cases/case_detail.html",
        case=case,
        products=products,
        completeness_checks=completeness_checks,
        audit_log=audit_log,
    )


@bp.get("/review-queue")
@login_required
def review_queue():
    cases = query_all(
        """
        SELECT
            safety_cases.*,
            countries.country_name,
            COUNT(case_products.case_product_id) AS product_count
        FROM pv.safety_cases AS safety_cases
        LEFT JOIN pv.countries AS countries
            ON countries.country_id = safety_cases.country_id
        LEFT JOIN pv.case_products AS case_products
            ON case_products.case_id = safety_cases.case_id
        GROUP BY safety_cases.case_id, countries.country_name
        ORDER BY safety_cases.updated_at DESC
        """
    )

    queue_cases = []
    for case in cases:
        products = query_all(
            """
            SELECT *
            FROM pv.case_products
            WHERE case_id = %s
            ORDER BY case_product_id
            """,
            (case["case_id"],),
        )
        checks = refresh_case_completeness(case, products)
        review_checks = [
            check for check in checks if check["status"] == "Review"
        ]
        if review_checks:
            case["review_checks"] = review_checks
            queue_cases.append(case)

    return render_template(
        "cases/review_queue.html",
        cases=queue_cases,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_case():
    form = SafetyCaseForm()

    countries = query_all(
        """
        SELECT country_id, country_name
        FROM pv.countries
        ORDER BY country_name
        """
    )
    form.country_id.choices = [(0, "Select country")] + [
        (country["country_id"], country["country_name"])
        for country in countries
    ]

    if form.validate_on_submit():
        existing_case = query_one(
            """
            SELECT case_id
            FROM pv.safety_cases
            WHERE case_number = %s
            """,
            (form.icsr_case_id.data.strip(),),
        )

        if existing_case:
            form.icsr_case_id.errors.append(
                "This ICSR Case ID already exists."
            )
            return render_template("cases/create_case.html", form=form)
        with transaction() as cursor:
            cursor.execute(
                "SELECT nextval('pv.safety_cases_case_id_seq') AS case_id"
            )
            case_id = cursor.fetchone()["case_id"]

            case_number = form.icsr_case_id.data.strip()

            cursor.execute(
                """
                INSERT INTO pv.safety_cases (
                    case_id,
                    case_number,
                    received_date,
                    country_id,
                    source,
                    report_type,
                    reporter_name,
                    reporter_profession,
                    reporter_organisation,
                    reporter_phone,
                    reporter_email,
                    patient_initials,
                    patient_date_of_birth,
                    patient_age_years,
                    patient_sex,
                    patient_weight_kg,
                    patient_pregnancy_status,
                    patient_address,
                    patient_phone,
                    medical_history,
                    concomitant_medicines,
                    event_description,
                    treatment_given,
                    event_onset_date,
                    event_onset_time,
                    event_end_date,
                    laboratory_results,
                    event_outcome,
                    seriousness,
                    seriousness_criteria,
                    causality_assessment,
                    case_narrative,
                    follow_up_required,
                    follow_up_due_date,
                    report_title,
                    form_id,
                    created_by
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    case_id,
                    case_number,
                    form.received_date.data,
                    form.country_id.data,
                    form.source.data,
                    form.report_type.data,
                    form.reporter_name.data or None,
                    form.reporter_profession.data or None,
                    form.reporter_organisation.data or None,
                    form.reporter_phone.data or None,
                    form.reporter_email.data or None,
                    form.patient_initials.data or None,
                    form.patient_date_of_birth.data,
                    form.patient_age_years.data,
                    form.patient_sex.data or None,
                    form.patient_weight_kg.data or None,
                    form.patient_pregnancy_status.data or None,
                    form.patient_address.data or None,
                    form.patient_phone.data or None,
                    form.medical_history.data or None,
                    form.concomitant_medicines.data or None,
                    form.event_description.data,
                    form.treatment_given.data or None,
                    form.event_onset_date.data,
                    form.event_onset_time.data,
                    form.event_end_date.data,
                    form.laboratory_results.data or None,
                    form.event_outcome.data or None,
                    form.seriousness.data,
                    form.seriousness_criteria.data or None,
                    form.causality_assessment.data or None,
                    form.case_narrative.data or None,
                    form.follow_up_required.data,
                    form.follow_up_due_date.data,
                    form.report_title.data or None,
                    form.form_id.data or None,
                    session["user_id"],
                ),
            )

            cursor.execute(
                """
                INSERT INTO pv.case_products (
                    case_id,
                    product_name,
                    generic_name,
                    strength,
                    dosage_form,
                    batch_number,
                    expiry_date,
                    dose,
                    route,
                    frequency,
                    indication,
                    therapy_start_date,
                    therapy_end_date,
                    action_taken
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    case_id,
                    form.product_name.data,
                    form.generic_name.data or None,
                    form.strength.data or None,
                    form.dosage_form.data or None,
                    form.batch_number.data or None,
                    form.expiry_date.data,
                    form.dose.data or None,
                    form.route.data or None,
                    form.frequency.data or None,
                    form.indication.data or None,
                    form.therapy_start_date.data,
                    form.therapy_end_date.data,
                    form.action_taken.data or None,
                ),
            )

            cursor.execute(
                """
                INSERT INTO pv.case_audit_log (
                    case_id,
                    action,
                    details,
                    performed_by
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    case_id,
                    "Case created",
                    f"Safety case {case_number} was created.",
                    session["user_id"],
                ),
            )

        try:
            detected_signals = detect_potential_signals_for_case(
                case_id=case_id,
                actor_user_id=session["user_id"],
            )
        except Exception:
            detected_signals = []
            flash(
                "Safety case was saved, but automated signal "
                "screening could not run.",
                "warning",
            )

        case = query_one(
            "SELECT * FROM pv.safety_cases WHERE case_id = %s",
            (case_id,),
        )
        products = query_all(
            "SELECT * FROM pv.case_products WHERE case_id = %s",
            (case_id,),
        )
        refresh_case_completeness(case, products)

        if detected_signals:
            flash(
                "Potential safety signal detected from ADR case "
                "screening. QPPV review is required.",
                "warning",
            )
        flash(f"Safety case {case_number} was created.", "success")
        return redirect(url_for("core.dashboard"))

    return render_template("cases/create_case.html", form=form)

def populate_case_form(form, case, product):
    form.icsr_case_id.data = case["case_number"]
    form.received_date.data = case["received_date"]
    form.country_id.data = case["country_id"]
    form.source.data = case["source"]
    form.report_type.data = case["report_type"]

    form.reporter_name.data = case["reporter_name"]
    form.reporter_profession.data = case["reporter_profession"]
    form.reporter_organisation.data = case["reporter_organisation"]
    form.reporter_phone.data = case["reporter_phone"]
    form.reporter_email.data = case["reporter_email"]

    form.patient_initials.data = case["patient_initials"]
    form.patient_date_of_birth.data = case["patient_date_of_birth"]
    form.patient_age_years.data = case["patient_age_years"]
    form.patient_sex.data = case["patient_sex"]
    form.patient_weight_kg.data = case["patient_weight_kg"]
    form.patient_pregnancy_status.data = case["patient_pregnancy_status"]
    form.patient_address.data = case["patient_address"]
    form.patient_phone.data = case["patient_phone"]
    form.medical_history.data = case["medical_history"]
    form.concomitant_medicines.data = case["concomitant_medicines"]

    form.product_name.data = product["product_name"]
    form.generic_name.data = product["generic_name"]
    form.strength.data = product["strength"]
    form.dosage_form.data = product["dosage_form"]
    form.batch_number.data = product["batch_number"]
    form.expiry_date.data = product["expiry_date"]
    form.dose.data = product["dose"]
    form.route.data = product["route"]
    form.frequency.data = product["frequency"]
    form.indication.data = product["indication"]
    form.therapy_start_date.data = product["therapy_start_date"]
    form.therapy_end_date.data = product["therapy_end_date"]
    form.action_taken.data = product["action_taken"]

    form.event_description.data = case["event_description"]
    form.treatment_given.data = case["treatment_given"]
    form.event_onset_date.data = case["event_onset_date"]
    form.event_onset_time.data = case["event_onset_time"]
    form.event_end_date.data = case["event_end_date"]
    form.laboratory_results.data = case["laboratory_results"]
    form.event_outcome.data = case["event_outcome"]
    form.seriousness.data = case["seriousness"]
    form.seriousness_criteria.data = case["seriousness_criteria"]
    form.causality_assessment.data = case["causality_assessment"]
    form.case_narrative.data = case["case_narrative"]
    form.follow_up_required.data = case["follow_up_required"]
    form.follow_up_due_date.data = case["follow_up_due_date"]
    form.report_title.data = case["report_title"]
    form.form_id.data = case["form_id"]


@bp.route("/<int:case_id>/edit", methods=["GET", "POST"])
@login_required
def edit_case(case_id):
    case = query_one(
        """
        SELECT *
        FROM pv.safety_cases
        WHERE case_id = %s
        """,
        (case_id,),
    )

    if not case:
        abort(404)

    product = query_one(
        """
        SELECT *
        FROM pv.case_products
        WHERE case_id = %s
        ORDER BY case_product_id
        LIMIT 1
        """,
        (case_id,),
    )

    if not product:
        abort(404)

    form = SafetyCaseForm()

    countries = query_all(
        """
        SELECT country_id, country_name
        FROM pv.countries
        ORDER BY country_name
        """
    )
    form.country_id.choices = [(0, "Select country")] + [
        (country["country_id"], country["country_name"])
        for country in countries
    ]

    if form.validate_on_submit():
        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.safety_cases
                SET
                    received_date = %s,
                    country_id = %s,
                    source = %s,
                    report_type = %s,
                    reporter_name = %s,
                    reporter_profession = %s,
                    reporter_organisation = %s,
                    reporter_phone = %s,
                    reporter_email = %s,
                    patient_initials = %s,
                    patient_date_of_birth = %s,
                    patient_age_years = %s,
                    patient_sex = %s,
                    patient_weight_kg = %s,
                    patient_pregnancy_status = %s,
                    patient_address = %s,
                    patient_phone = %s,
                    medical_history = %s,
                    concomitant_medicines = %s,
                    event_description = %s,
                    treatment_given = %s,
                    event_onset_date = %s,
                    event_onset_time = %s,
                    event_end_date = %s,
                    laboratory_results = %s,
                    event_outcome = %s,
                    seriousness = %s,
                    seriousness_criteria = %s,
                    causality_assessment = %s,
                    case_narrative = %s,
                    follow_up_required = %s,
                    follow_up_due_date = %s,
                    report_title = %s,
                    form_id = %s
                WHERE case_id = %s
                """,
                (
                    form.received_date.data,
                    form.country_id.data,
                    form.source.data,
                    form.report_type.data,
                    form.reporter_name.data or None,
                    form.reporter_profession.data or None,
                    form.reporter_organisation.data or None,
                    form.reporter_phone.data or None,
                    form.reporter_email.data or None,
                    form.patient_initials.data or None,
                    form.patient_date_of_birth.data,
                    form.patient_age_years.data,
                    form.patient_sex.data or None,
                    form.patient_weight_kg.data or None,
                    form.patient_pregnancy_status.data or None,
                    form.patient_address.data or None,
                    form.patient_phone.data or None,
                    form.medical_history.data or None,
                    form.concomitant_medicines.data or None,
                    form.event_description.data,
                    form.treatment_given.data or None,
                    form.event_onset_date.data,
                    form.event_onset_time.data,
                    form.event_end_date.data,
                    form.laboratory_results.data or None,
                    form.event_outcome.data or None,
                    form.seriousness.data,
                    form.seriousness_criteria.data or None,
                    form.causality_assessment.data or None,
                    form.case_narrative.data or None,
                    form.follow_up_required.data,
                    form.follow_up_due_date.data,
                    form.report_title.data or None,
                    form.form_id.data or None,
                    case_id,
                ),
            )

            cursor.execute(
                """
                UPDATE pv.case_products
                SET
                    product_name = %s,
                    generic_name = %s,
                    strength = %s,
                    dosage_form = %s,
                    batch_number = %s,
                    expiry_date = %s,
                    dose = %s,
                    route = %s,
                    frequency = %s,
                    indication = %s,
                    therapy_start_date = %s,
                    therapy_end_date = %s,
                    action_taken = %s
                WHERE case_id = %s
                """,
                (
                    form.product_name.data,
                    form.generic_name.data or None,
                    form.strength.data or None,
                    form.dosage_form.data or None,
                    form.batch_number.data or None,
                    form.expiry_date.data,
                    form.dose.data or None,
                    form.route.data or None,
                    form.frequency.data or None,
                    form.indication.data or None,
                    form.therapy_start_date.data,
                    form.therapy_end_date.data,
                    form.action_taken.data or None,
                    case_id,
                ),
            )

            cursor.execute(
                """
                INSERT INTO pv.case_audit_log (
                    case_id,
                    action,
                    details,
                    performed_by
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    case_id,
                    "Case updated",
                    "Safety case information was edited.",
                    session["user_id"],
                ),
            )

        write_audit_log(
            record_type="case",
            record_id=case_id,
            action="Safety case updated",
            details=f"Safety case {case['case_number']} was edited.",
            actor_user_id=session["user_id"],
        )

        flash("Safety case updated successfully.", "success")
        return redirect(url_for("cases.case_detail", case_id=case_id))

    populate_case_form(form, case, product)

    return render_template(
        "cases/create_case.html",
        form=form,
        editing=True,
        case=case,
    )