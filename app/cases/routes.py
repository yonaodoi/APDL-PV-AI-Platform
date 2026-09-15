from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, session, url_for

from app.db import query_all, query_one, transaction
from app.security import login_required
from .forms import SafetyCaseForm


bp = Blueprint("cases", __name__, url_prefix="/cases")


@bp.get("/")
@login_required
def case_list():
    cases = query_all(
        """
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
        ORDER BY safety_cases.created_at DESC
        """
    )

    return render_template("cases/case_list.html", cases=cases)


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
        audit_log=audit_log,
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

        flash(f"Safety case {case_number} was created.", "success")
        return redirect(url_for("core.dashboard"))

    return render_template("cases/create_case.html", form=form)