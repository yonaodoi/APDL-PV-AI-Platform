from datetime import datetime, timezone
from datetime import date, time
from pathlib import Path
from tempfile import TemporaryDirectory

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    current_app,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

from app.audit import write_audit_log
from app.services.case_documents import (
    DOCUMENT_TYPES,
    STATUS_DISMISSED,
    apply_suggestions,
    document_type_label,
)
from app.services.case_listedness import (
    listedness_message,
    run_automatic_listedness,
)
from app.db import query_all, query_one, transaction
from app.security import login_required
from .forms import SafetyCaseForm
from app.services.signal_detection import (
    screen_case_for_signals,
    summarise_screening,
)
from app.services.case_completeness import (
    refresh_case_completeness,
)
from app.services.reporting_clock import evaluate_reporting_clock
from app.services.event_coding import (
    get_active_terms,
    get_case_event_terms,
    set_manual_coding,
)
from app.services.case_document_extraction import (
    MAX_DOCUMENT_BYTES,
    SUPPORTED_EXTENSIONS,
    extract_case_fields,
    extract_document_text,
)


bp = Blueprint("cases", __name__, url_prefix="/cases")

DEADLINE_FILTERS = {
    "overdue": {"overdue"},
    "due_soon": {"due_soon"},
    "open": {"overdue", "due_soon", "on_track"},
    "late": {"overdue", "submitted_late"},
}


def _set_country_choices(form):
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
    return countries


def _populate_extracted_case_form(form, extracted, countries):
    allowed_values = {
        "source": {value for value, _ in form.source.choices},
        "report_type": {value for value, _ in form.report_type.choices},
        "patient_sex": {value for value, _ in form.patient_sex.choices},
        "patient_pregnancy_status": {
            value for value, _ in form.patient_pregnancy_status.choices
        },
        "action_taken": {value for value, _ in form.action_taken.choices},
        "event_outcome": {value for value, _ in form.event_outcome.choices},
    }
    uncertain = list(extracted.get("uncertain_fields", []))

    country_name = extracted.get("country")
    matched_country = next(
        (
            country
            for country in countries
            if country_name
            and country["country_name"].strip().casefold()
            == str(country_name).strip().casefold()
        ),
        None,
    )
    form.country_id.data = matched_country["country_id"] if matched_country else 0
    if country_name and not matched_country:
        uncertain.append("country")

    date_fields = {
        "received_date",
        "patient_date_of_birth",
        "expiry_date",
        "therapy_start_date",
        "therapy_end_date",
        "event_onset_date",
        "event_end_date",
    }
    for field_name in date_fields:
        value = extracted.get(field_name)
        try:
            setattr(
                getattr(form, field_name),
                "data",
                date.fromisoformat(value) if value else None,
            )
        except (TypeError, ValueError):
            getattr(form, field_name).data = None
            if value:
                uncertain.append(field_name)

    age = extracted.get("patient_age_years")
    try:
        form.patient_age_years.data = int(age) if age is not None else None
    except (TypeError, ValueError):
        form.patient_age_years.data = None
        if age:
            uncertain.append("patient_age_years")

    onset_time = extracted.get("event_onset_time")
    try:
        form.event_onset_time.data = (
            time.fromisoformat(onset_time) if onset_time else None
        )
    except (TypeError, ValueError):
        form.event_onset_time.data = None
        if onset_time:
            uncertain.append("event_onset_time")

    for field_name, values in allowed_values.items():
        value = extracted.get(field_name)
        if value in values:
            getattr(form, field_name).data = value
        elif isinstance(value, str):
            matching_value = next(
                (
                    choice
                    for choice in values
                    if choice.casefold() == value.strip().casefold()
                ),
                None,
            )
            if matching_value is not None:
                getattr(form, field_name).data = matching_value
            else:
                getattr(form, field_name).data = ""
                uncertain.append(field_name)
        elif value:
            getattr(form, field_name).data = ""
            uncertain.append(field_name)

    for field_name, value in extracted.items():
        if field_name in date_fields or field_name in allowed_values \
                or field_name in {
                    "country",
                    "patient_age_years",
                    "event_onset_time",
                    "uncertain_fields",
                }:
            continue
        if field_name in form._fields and isinstance(value, (str, int, float)):
            getattr(form, field_name).data = str(value)

    for required_field in (
        "received_date",
        "source",
        "report_type",
        "product_name",
        "event_description",
    ):
        if not getattr(form, required_field).data:
            uncertain.append(required_field)
    if not matched_country:
        uncertain.append("country")

    form.icsr_case_id.data = ""
    form.seriousness.data = False
    return sorted(set(uncertain))


@bp.get("/")
@login_required
def case_list():
    selected_product = request.args.get("product", "").strip()
    selected_country = request.args.get("country", "").strip()
    selected_status = request.args.get("status", "").strip()
    selected_priority = request.args.get("priority", "").strip()
    selected_deadline = request.args.get("deadline", "").strip()
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
            safety_cases.regulatory_submitted_date,
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

    cases = [
        {**case, "reporting_clock": evaluate_reporting_clock(case)}
        for case in cases
    ]
    overdue_count = len(
        {
            case["case_id"]
            for case in cases
            if case["reporting_clock"]
            and case["reporting_clock"]["state"] == "overdue"
        }
    )
    if selected_deadline in DEADLINE_FILTERS:
        wanted_states = DEADLINE_FILTERS[selected_deadline]
        cases = [
            case
            for case in cases
            if case["reporting_clock"]
            and case["reporting_clock"]["state"] in wanted_states
        ]

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
        selected_deadline=selected_deadline,
        overdue_count=overdue_count,
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
    from app.services.case_duplicates import find_possible_duplicates

    duplicate_candidates = find_possible_duplicates(case, products)

    linked_signals = query_all(
        """
        SELECT
            safety_signals.signal_id,
            safety_signals.signal_number,
            safety_signals.event_term,
            safety_signals.priority,
            safety_signals.status,
            safety_signals.auto_detected
        FROM pv.safety_signal_cases AS safety_signal_cases
        INNER JOIN pv.safety_signals AS safety_signals
            ON safety_signals.signal_id = safety_signal_cases.signal_id
        WHERE safety_signal_cases.case_id = %s
        ORDER BY safety_signals.date_detected DESC, safety_signals.signal_id DESC
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
        completeness_checks=completeness_checks,
        duplicate_candidates=duplicate_candidates,
        audit_log=audit_log,
        reporting_clock=evaluate_reporting_clock(case),
        linked_signals=linked_signals,
        source_complaints=query_all(
            """
            SELECT complaint_id, complaint_number, date_received
            FROM pv.product_complaints
            WHERE linked_case_id = %s
            ORDER BY date_received
            """,
            (case_id,),
        ),
        event_terms=get_case_event_terms(case_id),
        dictionary_terms=get_active_terms(),
        safety_assessment=_load_safety_assessment(case_id),
        document_types=DOCUMENT_TYPES,
        document_type_label=document_type_label,
        attachments=query_all(
            """
            SELECT
                attachments.*,
                users.full_name AS uploaded_by_name
            FROM pv.record_attachments AS attachments
            LEFT JOIN pv.users AS users
                ON users.user_id = attachments.uploaded_by
            WHERE attachments.record_type = 'case'
              AND attachments.record_id = %s
            ORDER BY attachments.uploaded_at DESC
            """,
            (case_id,),
        ),
    )


# Evidence text written by the old automatic check, which reported "Not
# listed" even when no reaction terms had been extracted.
LEGACY_UNRELIABLE_EVIDENCE = (
    "No matching reaction term was found among the automatically "
    "extracted RSI reactions"
)


def needs_rereview(assessment):
    """A reviewer-saved conclusion that rests on the old flawed check."""
    if not assessment:
        return False
    return (
        assessment.get("assessment_source", "reviewer") != "automatic"
        and LEGACY_UNRELIABLE_EVIDENCE in (assessment.get("rsi_evidence") or "")
    )


def _load_safety_assessment(case_id):
    try:
        assessment = query_one(
            """
            SELECT assessments.*, users.full_name AS assessed_by_name
            FROM pv.case_safety_assessments AS assessments
            LEFT JOIN pv.users AS users
                ON users.user_id = assessments.assessed_by
            WHERE assessments.case_id = %s
            """,
            (case_id,),
        )
    except Exception:
        current_app.logger.exception(
            "Could not load the listedness assessment for case %s", case_id
        )
        return None
    if assessment:
        assessment = dict(assessment)
        assessment["needs_rereview"] = needs_rereview(assessment)
    return assessment


def _case_attachment_or_404(case_id, attachment_id):
    attachment = query_one(
        """
        SELECT *
        FROM pv.record_attachments
        WHERE attachment_id = %s
          AND record_type = 'case'
          AND record_id = %s
        """,
        (attachment_id, case_id),
    )
    if not attachment:
        abort(404)
    return attachment


@bp.get("/<int:case_id>/attachments/<int:attachment_id>/suggestions")
@login_required
def review_document_suggestions(case_id, attachment_id):
    case = query_one(
        "SELECT case_id, case_number FROM pv.safety_cases WHERE case_id = %s",
        (case_id,),
    )
    if not case:
        abort(404)
    attachment = _case_attachment_or_404(case_id, attachment_id)
    return render_template(
        "cases/document_suggestions.html",
        case=case,
        attachment=attachment,
        suggestions=attachment.get("suggested_updates") or [],
        document_type_label=document_type_label,
    )


@bp.post("/<int:case_id>/attachments/<int:attachment_id>/suggestions")
@login_required
def apply_document_suggestions(case_id, attachment_id):
    attachment = _case_attachment_or_404(case_id, attachment_id)
    back = url_for("cases.case_detail", case_id=case_id) + "#attachments"

    if request.form.get("action") == "dismiss":
        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.record_attachments
                SET processing_status = %s,
                    processing_note = 'Remaining suggested updates were dismissed.',
                    suggested_updates = '[]'::jsonb
                WHERE attachment_id = %s
                """,
                (STATUS_DISMISSED, attachment_id),
            )
            cursor.execute(
                """
                INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    case_id,
                    "Document suggestions dismissed",
                    f"No updates applied from {attachment['original_filename']}.",
                    session["user_id"],
                ),
            )
        flash("Suggested updates dismissed. The case was not changed.", "info")
        return redirect(back)

    selected = set(request.form.getlist("field"))
    if not selected:
        flash("Tick at least one update to apply, or dismiss the suggestions.", "error")
        return redirect(
            url_for(
                "cases.review_document_suggestions",
                case_id=case_id,
                attachment_id=attachment_id,
            )
        )

    try:
        applied = apply_suggestions(
            case_id, attachment, selected, session["user_id"]
        )
    except Exception as error:
        current_app.logger.exception(
            "Applying document suggestions failed for case %s", case_id
        )
        flash(
            "The updates could not be applied, so the case was not changed. "
            f"A value was not accepted: {error}. Untick that row and try again.",
            "error",
        )
        return redirect(
            url_for(
                "cases.review_document_suggestions",
                case_id=case_id,
                attachment_id=attachment_id,
            )
        )
    write_audit_log(
        record_type="case",
        record_id=case_id,
        action="Case updated from attached document",
        details=(
            f"{len(applied)} field(s) updated from "
            f"{attachment['original_filename']}: {', '.join(applied)}."
        ),
        actor_user_id=session["user_id"],
    )
    flash(f"{len(applied)} update(s) applied: {', '.join(applied)}.", "success")

    listedness_flash = listedness_message(
        run_automatic_listedness(case_id, actor_user_id=session["user_id"])
    )
    if listedness_flash:
        flash(*listedness_flash)
    detected_signals, _ = screen_case_for_signals(
        case_id, actor_user_id=session["user_id"]
    )
    screening_message = summarise_screening(detected_signals)
    if screening_message:
        flash(screening_message, "warning")
    return redirect(back)


@bp.post("/<int:case_id>/listedness/recheck")
@login_required
def recheck_listedness(case_id):
    if not query_one(
        "SELECT case_id FROM pv.safety_cases WHERE case_id = %s", (case_id,)
    ):
        abort(404)

    replace_reviewer = request.form.get("replace_reviewer") == "1"
    previous = _load_safety_assessment(case_id) if replace_reviewer else None
    outcome = run_automatic_listedness(
        case_id,
        actor_user_id=session["user_id"],
        force=replace_reviewer,
    )
    if (
        previous
        and outcome
        and not outcome.get("failed")
        and previous.get("assessment_source", "reviewer") != "automatic"
    ):
        write_audit_log(
            record_type="case",
            record_id=case_id,
            action="Reviewer listedness conclusion replaced by automatic check",
            details=(
                f"Previous conclusion: {previous.get('listedness_status')} / "
                f"{previous.get('expectedness_status')}, saved "
                f"{previous['updated_at']:%d %b %Y}"
                + (
                    f" by {previous['assessed_by_name']}"
                    if previous.get("assessed_by_name")
                    else ""
                )
                + ". New result: "
                + f"{outcome.get('listedness_status')} / "
                + f"{outcome.get('expectedness_status')}."
            )
            if previous.get("updated_at")
            else "Previous reviewer conclusion replaced.",
            actor_user_id=session["user_id"],
        )
    if outcome and outcome.get("kept_reviewer"):
        flash(
            "A reviewer has confirmed this case's listedness, so the "
            "automatic check did not change it.",
            "info",
        )
    elif outcome and outcome.get("unchanged"):
        flash(
            "Listedness re-checked: no change "
            f"({outcome['listedness_status']} / "
            f"{outcome['expectedness_status']}).",
            "info",
        )
    else:
        message = listedness_message(outcome)
        if message:
            flash(*message)

    detected_signals, _ = screen_case_for_signals(
        case_id, actor_user_id=session["user_id"]
    )
    screening_message = summarise_screening(detected_signals)
    if screening_message:
        flash(screening_message, "warning")
    return redirect(url_for("cases.case_detail", case_id=case_id) + "#listedness")


@bp.post("/<int:case_id>/event-terms/<int:case_event_term_id>")
@login_required
def code_event_term(case_id, case_event_term_id):
    try:
        term_id = int(request.form.get("term_id", ""))
    except ValueError:
        flash("Select a preferred term.", "error")
        return redirect(url_for("cases.case_detail", case_id=case_id) + "#event-coding")

    row, synonym_saved = set_manual_coding(
        case_id,
        case_event_term_id,
        term_id,
        actor_user_id=session["user_id"],
        save_synonym=request.form.get("save_synonym") == "on",
    )
    if row is None:
        abort(404)

    details = (
        f'Reported term "{row["verbatim_term"]}" coded to '
        f'{row["preferred_term"]}.'
        + (" Wording saved as a synonym." if synonym_saved else "")
    )
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (case_id, "Event term coded", details, session["user_id"]),
        )

    listedness_outcome = run_automatic_listedness(
        case_id, actor_user_id=session["user_id"]
    )
    listedness_flash = listedness_message(listedness_outcome)
    if listedness_flash:
        flash(*listedness_flash)

    detected_signals, _ = screen_case_for_signals(
        case_id, actor_user_id=session["user_id"]
    )
    flash(details, "success")
    screening_message = summarise_screening(detected_signals)
    if screening_message:
        flash(screening_message, "warning")
    return redirect(url_for("cases.case_detail", case_id=case_id) + "#event-coding")


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


@bp.route("/new/from-document", methods=["GET", "POST"])
@login_required
def extract_case_from_document():
    if request.method == "GET":
        return render_template("cases/extract_case_document.html")

    uploaded_file = request.files.get("case_document")
    if not uploaded_file or not uploaded_file.filename:
        flash("Choose a case document to extract.", "error")
        return render_template("cases/extract_case_document.html"), 400

    filename = secure_filename(uploaded_file.filename)
    extension = Path(filename).suffix.lower()
    if not filename or extension not in SUPPORTED_EXTENSIONS:
        flash("Upload a PDF, DOCX, or plain text case document.", "error")
        return render_template("cases/extract_case_document.html"), 400

    document_bytes = uploaded_file.read(MAX_DOCUMENT_BYTES + 1)
    if len(document_bytes) > MAX_DOCUMENT_BYTES:
        flash("The document exceeds the 20 MB upload limit.", "error")
        return render_template("cases/extract_case_document.html"), 413

    try:
        with TemporaryDirectory() as temp_directory:
            document_path = Path(temp_directory) / f"case-document{extension}"
            document_path.write_bytes(document_bytes)
            document_text = extract_document_text(document_path)
        extracted = extract_case_fields(document_text)
    except (ValueError, RuntimeError) as exc:
        current_app.logger.warning(
            "Case document extraction could not complete: %s",
            exc,
        )
        flash(str(exc), "error")
        return render_template("cases/extract_case_document.html")
    except Exception:
        current_app.logger.exception("Unexpected case document extraction error")
        flash(
            "Case information could not be extracted. Check that the "
            "document is readable and the AI service is available.",
            "error",
        )
        return render_template("cases/extract_case_document.html"), 503

    form = SafetyCaseForm()
    countries = _set_country_choices(form)
    uncertain_fields = _populate_extracted_case_form(
        form,
        extracted,
        countries,
    )
    return render_template(
        "cases/create_case.html",
        form=form,
        editing=False,
        extraction_filename=filename,
        uncertain_fields=uncertain_fields,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_case():
    form = SafetyCaseForm()
    _set_country_choices(form)

    source_complaint = _load_source_complaint(
        request.values.get("from_complaint")
        or request.values.get("from_complaint_id")
    )
    if request.method == "GET" and source_complaint:
        _prefill_case_from_complaint(form, source_complaint)

    def render_case_form():
        return render_template(
            "cases/create_case.html",
            form=form,
            source_complaint=source_complaint,
        )

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
            return render_case_form()
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
                    f"Safety case {case_number} was created."
                    + (
                        " Created from product complaint "
                        f"{source_complaint['complaint_number']}."
                        if source_complaint
                        else ""
                    ),
                    session["user_id"],
                ),
            )

            if source_complaint:
                cursor.execute(
                    """
                    UPDATE pv.product_complaints
                    SET linked_case_id = %s,
                        updated_at = NOW()
                    WHERE complaint_id = %s
                      AND linked_case_id IS NULL
                    """,
                    (case_id, source_complaint["complaint_id"]),
                )

        if source_complaint:
            write_audit_log(
                "complaint",
                source_complaint["complaint_id"],
                "Safety case created",
                session["user_id"],
                f"Safety case {case_number} was created from this complaint.",
            )

        listedness_outcome = run_automatic_listedness(
            case_id, actor_user_id=session["user_id"]
        )
        listedness_flash = listedness_message(listedness_outcome)
        if listedness_flash:
            flash(*listedness_flash)

        detected_signals, screening_failed = screen_case_for_signals(
            case_id,
            actor_user_id=session["user_id"],
        )
        if screening_failed:
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

        from app.services.case_duplicates import find_possible_duplicates

        try:
            duplicate_candidates = find_possible_duplicates(case, products)
        except Exception:
            current_app.logger.exception(
                "Duplicate screening failed for newly created case %s",
                case_number,
            )
            flash(
                "The safety case was saved, but duplicate screening "
                "could not be completed.",
                "warning",
            )
            return redirect(url_for("core.dashboard"))

        screening_message = summarise_screening(detected_signals)
        if screening_message:
            flash(screening_message, "warning")
        flash(f"Safety case {case_number} was created.", "success")
        if duplicate_candidates:
            flash(
                "Possible duplicate cases were found. Review the matches "
                "before proceeding.",
                "warning",
            )
            return redirect(url_for("cases.case_detail", case_id=case_id))
        return redirect(url_for("core.dashboard"))

    return render_case_form()


def _load_source_complaint(value):
    """The adverse-event complaint a new case is being created from."""
    try:
        complaint_id = int(value)
    except (TypeError, ValueError):
        return None
    return query_one(
        """
        SELECT *
        FROM pv.product_complaints
        WHERE complaint_id = %s
          AND linked_case_id IS NULL
        """,
        (complaint_id,),
    )


def _prefill_case_from_complaint(form, complaint):
    contact = (complaint.get("reporter_contact") or "").strip()
    form.received_date.data = complaint["date_received"]
    if complaint.get("country_id"):
        form.country_id.data = complaint["country_id"]
    form.source.data = "Other"
    form.report_type.data = "Initial"
    form.reporter_name.data = complaint.get("reporter_name")
    if "@" in contact:
        form.reporter_email.data = contact
    elif contact:
        form.reporter_phone.data = contact
    form.product_name.data = complaint["product_name"]
    form.batch_number.data = complaint.get("batch_number")
    form.expiry_date.data = complaint.get("expiry_date")
    form.event_description.data = complaint["complaint_description"]
    form.case_narrative.data = (
        f"Reported to APDL as product complaint "
        f"{complaint['complaint_number']} on "
        f"{complaint['date_received']:%d %b %Y}. Complaint description: "
        f"{complaint['complaint_description']}"
    )

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

        listedness_outcome = run_automatic_listedness(
            case_id, actor_user_id=session["user_id"]
        )
        listedness_flash = listedness_message(listedness_outcome)
        if listedness_flash:
            flash(*listedness_flash)

        detected_signals, screening_failed = screen_case_for_signals(
            case_id,
            actor_user_id=session["user_id"],
        )
        screening_message = summarise_screening(detected_signals)
        if screening_message:
            flash(screening_message, "warning")
        elif screening_failed:
            flash(
                "Automated signal screening could not run for this case.",
                "warning",
            )
        return redirect(url_for("cases.case_detail", case_id=case_id))

    populate_case_form(form, case, product)

    return render_template(
        "cases/create_case.html",
        form=form,
        editing=True,
        case=case,
    )