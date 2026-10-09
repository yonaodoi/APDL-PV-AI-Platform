import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    jsonify,
    send_file,
)
from werkzeug.utils import secure_filename
from app.complaints.forms import ComplaintReviewForm, ProductComplaintForm
from app.audit import write_audit_log
from app.services.record_changes import describe_changes
from app.attachments.routes import list_record_attachments
from app.services.complaint_checks import (
    batch_key,
    batch_trends,
    complaint_date_problems,
    normalise_batch,
)
from app.services.complaint_rules import (
    STATUS_COMPLETE,
    describe_complaint_changes,
    validate_complaint_update,
)
from app.services.complaint_documents import (
    COMPLAINT_DOCUMENT_TYPES,
    STATUS_DISMISSED,
    apply_complaint_suggestions,
    complaint_document_type_label,
    describe_applied,
    typed_complaint_value,
)
from app.services.complaint_workflow import (
    closure_blockers,
    closure_checks,
    draft_investigation_note,
)
from app.db import query_all, query_one, transaction
from app.security import login_required
from app.services.product_complaint_reporting_docx import (
    build_product_complaint_reporting_docx,
)
from app.services.complaint_document_extraction import (
    MAX_DOCUMENT_BYTES,
    SUPPORTED_EXTENSIONS,
    extract_complaint_fields,
    extract_document_text,
)
from app.services import company_profile as company_profile_service

bp = Blueprint("complaints", __name__, url_prefix="/complaints")

ADVERSE_EVENT_CATEGORY = "Adverse event"


def _set_complaint_country_choices(form):
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


def _populate_complaint_form(form, extracted, countries):
    uncertain = list(extracted.get("uncertain_fields", []))
    matched_country = next(
        (
            country
            for country in countries
            if extracted.get("country")
            and country["country_name"].strip().casefold()
            == str(extracted["country"]).strip().casefold()
        ),
        None,
    )
    form.country_id.data = (
        matched_country["country_id"] if matched_country else 0
    )
    if extracted.get("country") and not matched_country:
        uncertain.append("country")

    for field_name in ("date_received", "manufacturing_date", "expiry_date"):
        value = extracted.get(field_name)
        try:
            getattr(form, field_name).data = (
                date.fromisoformat(value) if value else None
            )
        except (TypeError, ValueError):
            getattr(form, field_name).data = None
            if value:
                uncertain.append(field_name)

    for field_name in ("complaint_category", "severity"):
        value = extracted.get(field_name)
        choices = {
            choice.casefold(): choice
            for choice, _label in getattr(form, field_name).choices
        }
        choice = choices.get(value.strip().casefold()) if isinstance(value, str) else None
        getattr(form, field_name).data = choice
        if value and not choice:
            uncertain.append(field_name)

    for field_name in (
        "reporter_name",
        "reporter_contact",
        "product_name",
        "batch_number",
        "complaint_description",
    ):
        value = extracted.get(field_name)
        getattr(form, field_name).data = str(value) if value is not None else ""

    required_fields = (
        "date_received",
        "product_name",
        "complaint_category",
        "severity",
        "complaint_description",
    )
    for field_name in required_fields:
        if not getattr(form, field_name).data:
            uncertain.append(field_name)
    if not matched_country:
        uncertain.append("country")
    form.complaint_number.data = ""
    return sorted(set(uncertain))


@bp.get("/")
@login_required
def complaint_list():
    selected_product = request.args.get("product", "").strip()
    selected_country = request.args.get("country", "").strip()
    selected_status = request.args.get("status", "").strip()
    selected_severity = request.args.get("severity", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    filters = []
    parameters = []

    if selected_product:
        filters.append("product_complaints.product_name = %s")
        parameters.append(selected_product)

    if selected_country:
        filters.append("countries.country_name = %s")
        parameters.append(selected_country)

    if selected_status:
        filters.append("product_complaints.status = %s")
        parameters.append(selected_status)

    if selected_severity:
        filters.append("product_complaints.severity = %s")
        parameters.append(selected_severity)

    if start_date:
        filters.append("product_complaints.date_received >= %s")
        parameters.append(start_date)

    if end_date:
        filters.append("product_complaints.date_received <= %s")
        parameters.append(end_date)

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    complaints = query_all(
        f"""
        SELECT
            product_complaints.complaint_id,
            product_complaints.complaint_number,
            product_complaints.date_received,
            product_complaints.product_name,
            product_complaints.batch_number,
            product_complaints.complaint_category,
            product_complaints.severity,
            product_complaints.status,
            product_complaints.linked_case_id,
            countries.country_name
        FROM pv.product_complaints AS product_complaints
        LEFT JOIN pv.countries AS countries
            ON countries.country_id = product_complaints.country_id
        {where_clause}
        ORDER BY
            product_complaints.date_received DESC,
            product_complaints.complaint_id DESC
        """,
        tuple(parameters),
    )

    products = query_all(
        """
        SELECT DISTINCT product_name
        FROM pv.product_complaints
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

    statuses = [
        {"status": "Under investigation"},
        {"status": "Investigation complete"},
    ]

    severities = [
        {"severity": "Non-serious"},
        {"severity": "Serious"},
    ]

    trend_counts = _batch_trend_counts()
    complaints = [
        {
            **complaint,
            "batch_trend_count": trend_counts.get(
                batch_key(complaint.get("product_name"), complaint.get("batch_number"))
            ),
        }
        for complaint in complaints
    ]

    return render_template(
        "complaints/complaint_list.html",
        complaints=complaints,
        products=products,
        countries=countries,
        statuses=statuses,
        severities=severities,
        selected_product=selected_product,
        selected_country=selected_country,
        selected_status=selected_status,
        selected_severity=selected_severity,
        start_date=start_date,
        end_date=end_date,
    )

def _batch_trend_counts():
    """(product, batch) -> number of complaints, for trending batches."""
    rows = query_all(
        """
        SELECT complaint_id, product_name, batch_number
        FROM pv.product_complaints
        WHERE batch_number IS NOT NULL
          AND TRIM(batch_number) <> ''
        """
    )
    return {key: len(items) for key, items in batch_trends(rows).items()}


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_complaint():
    form = ProductComplaintForm()

    _set_complaint_country_choices(form)

    date_errors, date_warnings = complaint_date_problems(
        {
            "date_received": form.date_received.data,
            "manufacturing_date": form.manufacturing_date.data,
            "expiry_date": form.expiry_date.data,
        }
    ) if request.method == "POST" else ([], [])

    if form.validate_on_submit() and date_errors:
        for error in date_errors:
            flash(error, "error")
        return render_template(
            "complaints/create_complaint.html",
            form=form,
        )

    if form.validate_on_submit():
        existing_complaint = query_one(
            """
            SELECT complaint_id
            FROM pv.product_complaints
            WHERE complaint_number = %s
            """,
            (form.complaint_number.data.strip(),),
        )

        if existing_complaint:
            form.complaint_number.errors.append(
                "This Complaint ID already exists."
            )
            return render_template(
                "complaints/create_complaint.html",
                form=form,
            )

        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.product_complaints (
                    complaint_number,
                    date_received,
                    country_id,
                    reporter_name,
                    reporter_contact,
                    product_name,
                    batch_number,
                    manufacturing_date,
                    expiry_date,
                    complaint_category,
                    complaint_description,
                    severity,
                    created_by
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                RETURNING complaint_id
                """,
                (
                    form.complaint_number.data.strip(),
                    form.date_received.data,
                    form.country_id.data,
                    form.reporter_name.data.strip() or None,
                    form.reporter_contact.data.strip() or None,
                    form.product_name.data.strip(),
                    form.batch_number.data.strip() or None,
                    form.manufacturing_date.data,
                    form.expiry_date.data,
                    form.complaint_category.data,
                    form.complaint_description.data.strip(),
                    form.severity.data,
                    session["user_id"],
                ),
            )
            complaint_id = cursor.fetchone()["complaint_id"]

        write_audit_log(
            "complaint",
            complaint_id,
            "Complaint created",
            session["user_id"],
            f"Complaint {form.complaint_number.data.strip()} was created "
            f"({form.complaint_category.data}, {form.severity.data}).",
        )

        flash("Product quality complaint saved successfully.", "success")
        for warning in date_warnings:
            flash(warning, "warning")
        same_batch = _batch_trend_counts().get(
            batch_key(form.product_name.data, form.batch_number.data)
        )
        if same_batch:
            flash(
                f"Batch {form.batch_number.data.strip()} now has {same_batch} "
                "complaints. Review the batch for a quality trend.",
                "warning",
            )
        if form.complaint_category.data == ADVERSE_EVENT_CATEGORY:
            flash(
                "This complaint reports an adverse event. Create a safety "
                "case from it so it is assessed and reported on time.",
                "warning",
            )
            return redirect(
                url_for("complaints.complaint_detail", complaint_id=complaint_id)
            )
        return redirect(url_for("complaints.complaint_list"))

    return render_template(
        "complaints/create_complaint.html",
        form=form,
    )


@bp.route("/new/from-document", methods=["GET", "POST"])
@login_required
def extract_complaint_from_document():
    if request.method == "GET":
        return render_template(
            "complaints/extract_complaint_document.html"
        )

    uploaded_file = request.files.get("complaint_document")
    if not uploaded_file or not uploaded_file.filename:
        flash("Choose a complaint document to extract.", "error")
        return render_template(
            "complaints/extract_complaint_document.html"
        ), 400

    filename = secure_filename(uploaded_file.filename)
    extension = Path(filename).suffix.lower()
    if not filename or extension not in SUPPORTED_EXTENSIONS:
        flash("Upload a PDF, DOCX, or plain text complaint document.", "error")
        return render_template(
            "complaints/extract_complaint_document.html"
        ), 400

    document_bytes = uploaded_file.read(MAX_DOCUMENT_BYTES + 1)
    if len(document_bytes) > MAX_DOCUMENT_BYTES:
        flash("The document exceeds the 20 MB upload limit.", "error")
        return render_template(
            "complaints/extract_complaint_document.html"
        ), 413

    try:
        with TemporaryDirectory() as temp_directory:
            document_path = Path(temp_directory) / f"complaint{extension}"
            document_path.write_bytes(document_bytes)
            document_text = extract_document_text(document_path)
        extracted = extract_complaint_fields(document_text)
    except (ValueError, RuntimeError) as exc:
        current_app.logger.warning(
            "Product complaint document extraction failed: %s",
            exc,
        )
        flash(str(exc), "error")
        return render_template(
            "complaints/extract_complaint_document.html"
        )
    except Exception:
        current_app.logger.exception(
            "Unexpected product complaint extraction error"
        )
        flash(
            "Complaint information could not be extracted. Check that the "
            "document is readable and the local AI service is available.",
            "error",
        )
        return render_template(
            "complaints/extract_complaint_document.html"
        ), 503

    form = ProductComplaintForm()
    countries = _set_complaint_country_choices(form)
    uncertain_fields = _populate_complaint_form(
        form,
        extracted,
        countries,
    )
    return render_template(
        "complaints/create_complaint.html",
        form=form,
        extraction_filename=filename,
        uncertain_fields=uncertain_fields,
    )


@bp.get("/<int:complaint_id>")
@login_required
def complaint_detail(complaint_id):
    return _render_complaint_detail(complaint_id)


def _render_complaint_detail(complaint_id, review_form=None):
    complaint = query_one(
        """
        SELECT
            pc.*,
            c.country_name,
            u.full_name AS created_by_name,
            sc.case_number AS linked_case_number
        FROM pv.product_complaints pc
        LEFT JOIN pv.countries c ON c.country_id = pc.country_id
        LEFT JOIN pv.users u ON u.user_id = pc.created_by
        LEFT JOIN pv.safety_cases sc ON sc.case_id = pc.linked_case_id
        WHERE pc.complaint_id = %s
        """,
        (complaint_id,),
    )

    if not complaint:
        abort(404)

    if review_form is None:
        review_form = ComplaintReviewForm()
        review_form.status.data = complaint["status"]
        review_form.investigation_summary.data = complaint["investigation_summary"]
        review_form.corrective_action.data = complaint["corrective_action"]
        review_form.closure_date.data = complaint["closure_date"]

    history = query_all(
        """
        SELECT
            audit_log.action,
            audit_log.details,
            audit_log.occurred_at,
            users.full_name
        FROM pv.audit_log AS audit_log
        LEFT JOIN pv.users AS users
            ON users.user_id = audit_log.actor_user_id
        WHERE audit_log.record_type = 'complaint'
          AND audit_log.record_id = %s
        ORDER BY audit_log.occurred_at DESC
        """,
        (complaint_id,),
    )

    same_batch = []
    if normalise_batch(complaint.get("batch_number")):
        same_batch = query_all(
            """
            SELECT
                complaint_id,
                complaint_number,
                date_received,
                status,
                severity
            FROM pv.product_complaints
            WHERE complaint_id <> %s
              AND LOWER(TRIM(product_name)) = LOWER(TRIM(%s))
              AND UPPER(REGEXP_REPLACE(batch_number, '\\s', '', 'g')) = %s
            ORDER BY date_received DESC
            """,
            (
                complaint_id,
                complaint["product_name"],
                normalise_batch(complaint["batch_number"]),
            ),
        )
    _, date_warnings = complaint_date_problems(complaint)
    attachments = list_record_attachments("complaint", complaint_id)
    checks = closure_checks(complaint, date_warnings, same_batch)

    return render_template(
        "complaints/complaint_detail.html",
        complaint=complaint,
        review_form=review_form,
        adverse_event_category=ADVERSE_EVENT_CATEGORY,
        history=history,
        same_batch=same_batch,
        date_warnings=date_warnings,
        attachments=attachments,
        closure_checks=checks,
        closure_blockers=closure_blockers(checks),
        drafted_note=draft_investigation_note(
            complaint, same_batch, date_warnings, len(attachments or [])
        ),
        today_iso=date.today().isoformat(),
        is_complete=complaint.get("status") == STATUS_COMPLETE,
        complaint_document_types=COMPLAINT_DOCUMENT_TYPES,
    )


COMPLAINT_EDIT_SECTIONS = {
    "complaint-receipt": "detail-receipt",
    "complaint-product": "detail-product",
}
COMPLAINT_FIELD_LABELS = {
    "complaint_number": "Complaint ID",
    "date_received": "Date received",
    "country_name": "Country",
    "reporter_name": "Reporter",
    "reporter_contact": "Reporter contact",
    "product_name": "Product",
    "batch_number": "Batch number",
    "manufacturing_date": "Manufacturing date",
    "expiry_date": "Expiry date",
    "complaint_category": "Category",
    "complaint_description": "Description",
    "severity": "Severity",
}


def _complaint_page_anchor(section):
    anchor = COMPLAINT_EDIT_SECTIONS.get(section or "")
    return f"#{anchor}" if anchor else ""


@bp.route("/<int:complaint_id>/edit", methods=["GET", "POST"])
@login_required
def edit_complaint(complaint_id):
    complaint = query_one(
        """
        SELECT pc.*, c.country_name
        FROM pv.product_complaints pc
        LEFT JOIN pv.countries c ON c.country_id = pc.country_id
        WHERE pc.complaint_id = %s
        """,
        (complaint_id,),
    )
    if not complaint:
        abort(404)

    form = ProductComplaintForm()
    countries = _set_complaint_country_choices(form)
    section = request.args.get("section") or request.form.get("return_section")
    section = section if section in COMPLAINT_EDIT_SECTIONS else None
    detail_url = url_for(
        "complaints.complaint_detail", complaint_id=complaint_id
    ) + _complaint_page_anchor(section)

    def render_form():
        return render_template(
            "complaints/create_complaint.html",
            form=form,
            editing=True,
            complaint=complaint,
            return_section=section,
            cancel_url=detail_url,
        )

    if request.method == "GET":
        for field in (
            "complaint_number", "date_received", "country_id", "reporter_name",
            "reporter_contact", "product_name", "batch_number",
            "manufacturing_date", "expiry_date", "complaint_category",
            "complaint_description", "severity",
        ):
            getattr(form, field).data = complaint.get(field)
        return render_form()

    if not form.validate_on_submit():
        return render_form()

    date_errors, date_warnings = complaint_date_problems(
        {
            "date_received": form.date_received.data,
            "manufacturing_date": form.manufacturing_date.data,
            "expiry_date": form.expiry_date.data,
        }
    )
    if date_errors:
        for error in date_errors:
            flash(error, "error")
        return render_form()

    complaint_number = form.complaint_number.data.strip()
    clash = query_one(
        """
        SELECT complaint_id
        FROM pv.product_complaints
        WHERE complaint_number = %s
          AND complaint_id <> %s
        """,
        (complaint_number, complaint_id),
    )
    if clash:
        form.complaint_number.errors.append("This Complaint ID already exists.")
        return render_form()

    updated = {
        "complaint_number": complaint_number,
        "date_received": form.date_received.data,
        "country_id": form.country_id.data,
        "reporter_name": (form.reporter_name.data or "").strip() or None,
        "reporter_contact": (form.reporter_contact.data or "").strip() or None,
        "product_name": form.product_name.data.strip(),
        "batch_number": (form.batch_number.data or "").strip() or None,
        "manufacturing_date": form.manufacturing_date.data,
        "expiry_date": form.expiry_date.data,
        "complaint_category": form.complaint_category.data,
        "complaint_description": form.complaint_description.data.strip(),
        "severity": form.severity.data,
    }
    country_names = {c["country_id"]: c["country_name"] for c in countries}
    changes = describe_changes(
        complaint,
        {**updated, "country_name": country_names.get(updated["country_id"])},
        COMPLAINT_FIELD_LABELS,
    )
    if not changes:
        flash("No changes were made.", "info")
        return redirect(detail_url)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.product_complaints
            SET complaint_number = %s,
                date_received = %s,
                country_id = %s,
                reporter_name = %s,
                reporter_contact = %s,
                product_name = %s,
                batch_number = %s,
                manufacturing_date = %s,
                expiry_date = %s,
                complaint_category = %s,
                complaint_description = %s,
                severity = %s,
                updated_at = NOW()
            WHERE complaint_id = %s
            """,
            (*updated.values(), complaint_id),
        )

    write_audit_log(
        "complaint",
        complaint_id,
        "Complaint details edited",
        session["user_id"],
        changes,
    )
    flash("Complaint details updated.", "success")
    for warning in date_warnings:
        flash(warning, "warning")
    if (
        updated["complaint_category"] == ADVERSE_EVENT_CATEGORY
        and complaint.get("complaint_category") != ADVERSE_EVENT_CATEGORY
        and not complaint.get("linked_case_id")
    ):
        flash(
            "This complaint now reports an adverse event. Create a safety "
            "case from it so it is assessed and reported on time.",
            "warning",
        )
    return redirect(detail_url)


@bp.post("/<int:complaint_id>/review")
@login_required
def review_complaint(complaint_id):
    complaint = query_one(
        """
        SELECT
            complaint_id,
            date_received,
            severity,
            status,
            investigation_summary,
            corrective_action,
            closure_date,
            complaint_category,
            linked_case_id
        FROM pv.product_complaints
        WHERE complaint_id = %s
        """,
        (complaint_id,),
    )

    if not complaint:
        abort(404)

    form = ComplaintReviewForm()

    if not form.validate_on_submit():
        flash("Please correct the investigation form and try again.", "error")
        return redirect(
            url_for("complaints.complaint_detail", complaint_id=complaint_id)
        )

    errors = validate_complaint_update(
        complaint,
        form.status.data,
        form.investigation_summary.data,
        form.corrective_action.data,
        form.closure_date.data,
    )
    if errors:
        for error in errors:
            flash(error, "error")
        return _render_complaint_detail(complaint_id, review_form=form), 400

    after = {
        "status": form.status.data,
        "investigation_summary": form.investigation_summary.data,
        "corrective_action": form.corrective_action.data,
        "closure_date": form.closure_date.data,
    }

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.product_complaints
            SET
                status = %s,
                investigation_summary = %s,
                corrective_action = %s,
                closure_date = %s,
                updated_at = NOW()
            WHERE complaint_id = %s
            """,
            (
                form.status.data,
                form.investigation_summary.data.strip() or None,
                form.corrective_action.data.strip() or None,
                form.closure_date.data,
                complaint_id,
            ),
        )

    write_audit_log(
        "complaint",
        complaint_id,
        "Investigation updated",
        session["user_id"],
        describe_complaint_changes(complaint, after),
    )

    return redirect(
        url_for(
            "complaints.complaint_detail",
            complaint_id=complaint_id,
            saved="investigation",
        )
        + "#investigation-panel"
    )


@bp.post("/<int:complaint_id>/close")
@login_required
def close_complaint(complaint_id):
    """Mark the investigation complete from the "Ready to close?" panel."""
    complaint = query_one(
        """
        SELECT
            complaint_id,
            date_received,
            severity,
            status,
            investigation_summary,
            corrective_action,
            closure_date,
            complaint_category,
            linked_case_id
        FROM pv.product_complaints
        WHERE complaint_id = %s
        """,
        (complaint_id,),
    )
    if not complaint:
        abort(404)

    panel_url = (
        url_for("complaints.complaint_detail", complaint_id=complaint_id)
        + "#investigation-panel"
    )
    if complaint["status"] == STATUS_COMPLETE:
        flash("This investigation is already complete.", "info")
        return redirect(panel_url)

    try:
        closure_date = date.fromisoformat(request.form.get("closure_date", ""))
    except ValueError:
        flash("Enter a valid closure date.", "error")
        return redirect(panel_url)

    errors = validate_complaint_update(
        complaint,
        STATUS_COMPLETE,
        complaint["investigation_summary"],
        complaint["corrective_action"],
        closure_date,
    )
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(panel_url)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.product_complaints
            SET status = %s,
                closure_date = %s,
                updated_at = NOW()
            WHERE complaint_id = %s
              AND status <> %s
            """,
            (STATUS_COMPLETE, closure_date, complaint_id, STATUS_COMPLETE),
        )

    write_audit_log(
        "complaint",
        complaint_id,
        "Investigation closed",
        session["user_id"],
        describe_complaint_changes(
            complaint,
            {**complaint, "status": STATUS_COMPLETE, "closure_date": closure_date},
        ),
    )
    return redirect(
        url_for(
            "complaints.complaint_detail",
            complaint_id=complaint_id,
            saved="closed",
        )
        + "#investigation-panel"
    )

def _get_filtered_complaints():
    selected_product = request.args.get("product", "").strip()
    selected_country = request.args.get("country", "").strip()
    selected_status = request.args.get("status", "").strip()
    selected_severity = request.args.get("severity", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    filters = []
    parameters = []

    if selected_product:
        filters.append("product_complaints.product_name = %s")
        parameters.append(selected_product)

    if selected_country:
        filters.append("countries.country_name = %s")
        parameters.append(selected_country)

    if selected_status:
        filters.append("product_complaints.status = %s")
        parameters.append(selected_status)

    if selected_severity:
        filters.append("product_complaints.severity = %s")
        parameters.append(selected_severity)

    if start_date:
        filters.append("product_complaints.date_received >= %s")
        parameters.append(start_date)

    if end_date:
        filters.append("product_complaints.date_received <= %s")
        parameters.append(end_date)

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    complaints = query_all(
        f"""
        SELECT
            product_complaints.complaint_id,
            product_complaints.complaint_number,
            product_complaints.date_received,
            product_complaints.product_name,
            product_complaints.batch_number,
            product_complaints.complaint_category,
            product_complaints.severity,
            product_complaints.status,
            product_complaints.linked_case_id,
            countries.country_name
        FROM pv.product_complaints AS product_complaints
        LEFT JOIN pv.countries AS countries
            ON countries.country_id = product_complaints.country_id
        {where_clause}
        ORDER BY
            product_complaints.date_received DESC,
            product_complaints.complaint_id DESC
        """,
        tuple(parameters),
    )

    report_filters = {
        "product": selected_product,
        "country": selected_country,
        "status": selected_status,
        "severity": selected_severity,
        "start_date": start_date,
        "end_date": end_date,
        "reporting_period": (
            f"{start_date or 'All dates'} to "
            f"{end_date or 'All dates'}"
        ),
    }

    return complaints, report_filters


@bp.get("/reporting-summary/preview")
@login_required
def preview_product_complaint_reporting_summary():
    complaints, report_filters = _get_filtered_complaints()

    return render_template(
        "complaints/product_complaint_report_preview.html",
        complaints=complaints,
        report_filters=report_filters,
    )


@bp.get("/reporting-summary/download")
@login_required
def download_product_complaint_reporting_summary():
    complaints, report_filters = _get_filtered_complaints()

    draft_id = request.args.get("draft_id", type=int)
    editable_content = None

    if draft_id:
        draft = query_one(
            """
            SELECT report_content
            FROM pv.product_complaint_reporting_drafts
            WHERE draft_id = %s
              AND created_by = %s
            """,
            (draft_id, session["user_id"]),
        )

        if draft is None:
            abort(404)

        editable_content = draft["report_content"]

    report_file = None
    if not editable_content:
        # An edited draft is printed as edited; otherwise use the template.
        from app.services.report_fields import complaint_summary_context
        from app.services.report_templates import render_with_active

        report_file, notice = render_with_active(
            "complaint_summary", lambda: complaint_summary_context(complaints, report_filters)
        )
        if notice:
            flash(notice, "warning")
    if report_file is None:
        report_file = build_product_complaint_reporting_docx(
            complaints=complaints,
            report_filters=report_filters,
            editable_content=editable_content,
        )

    return send_file(
        report_file,
        as_attachment=True,
        download_name=f"{company_profile_service.file_prefix()}_product_complaint_reporting_summary.docx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )


@bp.post("/reporting-summary/drafts")
@login_required
def save_product_complaint_reporting_draft():
    try:
        filter_state = json.loads(
            request.form.get("filter_state", "{}")
        )
        report_content = json.loads(
            request.form.get("report_content", "{}")
        )
    except json.JSONDecodeError:
        return jsonify(
            status="error",
            message="Invalid draft data.",
        ), 400

    if not isinstance(filter_state, dict):
        return jsonify(
            status="error",
            message="Invalid filter information.",
        ), 400

    if not isinstance(report_content, dict):
        return jsonify(
            status="error",
            message="Invalid report content.",
        ), 400

    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.product_complaint_reporting_drafts (
                filter_state,
                report_content,
                created_by
            )
            VALUES (%s::jsonb, %s::jsonb, %s)
            RETURNING draft_id, updated_at
            """,
            (
                json.dumps(filter_state),
                json.dumps(report_content),
                session["user_id"],
            ),
        )
        draft = cursor.fetchone()

    return jsonify(
        status="saved",
        draft_id=draft["draft_id"],
        updated_at=draft["updated_at"].isoformat(),
    )


def _complaint_attachment_or_404(complaint_id, attachment_id):
    attachment = query_one(
        """
        SELECT *
        FROM pv.record_attachments
        WHERE attachment_id = %s
          AND record_type = 'complaint'
          AND record_id = %s
        """,
        (attachment_id, complaint_id),
    )
    if not attachment:
        abort(404)
    return attachment


@bp.get("/<int:complaint_id>/attachments/<int:attachment_id>/suggestions")
@login_required
def review_document_suggestions(complaint_id, attachment_id):
    complaint = query_one(
        "SELECT complaint_id, complaint_number FROM pv.product_complaints WHERE complaint_id = %s",
        (complaint_id,),
    )
    if not complaint:
        abort(404)
    attachment = _complaint_attachment_or_404(complaint_id, attachment_id)
    return render_template(
        "cases/document_suggestions.html",
        record_number=complaint["complaint_number"],
        record_noun="complaint",
        back_url=url_for("complaints.complaint_detail", complaint_id=complaint_id)
        + "#attachments",
        attachment=attachment,
        suggestions=_editable_complaint_suggestions(attachment),
        document_type_label=complaint_document_type_label,
    )


def _editable_complaint_suggestions(attachment):
    from app.services.complaint_documents import APPEND_FIELDS, CHOICES, DATE_FIELDS
    from app.services.suggestion_edits import annotate

    return annotate(attachment.get("suggested_updates") or [], DATE_FIELDS, CHOICES, APPEND_FIELDS)


@bp.post("/<int:complaint_id>/attachments/<int:attachment_id>/suggestions")
@login_required
def apply_document_suggestions(complaint_id, attachment_id):
    attachment = _complaint_attachment_or_404(complaint_id, attachment_id)
    back = (
        url_for("complaints.complaint_detail", complaint_id=complaint_id)
        + "#attachments"
    )
    review_url = url_for(
        "complaints.review_document_suggestions",
        complaint_id=complaint_id,
        attachment_id=attachment_id,
    )

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
        write_audit_log(
            "complaint",
            complaint_id,
            "Document suggestions dismissed",
            session["user_id"],
            f"No updates applied from {attachment['original_filename']}.",
        )
        flash("Suggested updates dismissed. The complaint was not changed.", "info")
        return redirect(back)

    selected = set(request.form.getlist("field"))
    if not selected:
        flash("Tick at least one update to apply, or dismiss the suggestions.", "error")
        return redirect(review_url)

    from app.services.complaint_documents import normalise_complaint_extract
    from app.services.suggestion_edits import apply_edits

    country_cache = []

    def check_value(values):
        # Countries are only needed when a reviewer changed a value.
        if not country_cache:
            country_cache.append(query_all("SELECT country_id, country_name FROM pv.countries"))
        return normalise_complaint_extract(values, country_cache[0])

    edited, edit_errors = apply_edits(
        attachment.get("suggested_updates") or [], request.form, check_value, selected,
    )
    if edit_errors:
        for error in edit_errors:
            flash(error, "error")
        flash("Nothing was applied. Correct the value or untick that row.", "error")
        return redirect(review_url)
    attachment = {**attachment, "suggested_updates": edited}

    # Check the dates still make sense with the chosen values applied.
    complaint = query_one(
        "SELECT * FROM pv.product_complaints WHERE complaint_id = %s",
        (complaint_id,),
    )
    if not complaint:
        abort(404)
    merged = dict(complaint)
    try:
        for item in attachment.get("suggested_updates") or []:
            if item["field"] in selected:
                merged[item["field"]] = typed_complaint_value(
                    item["field"], item["new_value"]
                )
    except ValueError as error:
        flash(f"A value was not accepted: {error}. Untick that row and try again.", "error")
        return redirect(review_url)
    date_errors, _ = complaint_date_problems(merged)
    if date_errors:
        for error in date_errors:
            flash(error, "error")
        flash("No updates were applied. Untick the date that does not fit.", "error")
        return redirect(review_url)

    try:
        applied = apply_complaint_suggestions(complaint_id, attachment, selected)
    except Exception as error:
        current_app.logger.exception(
            "Applying document suggestions failed for complaint %s", complaint_id
        )
        flash(
            "The updates could not be applied, so the complaint was not changed. "
            f"A value was not accepted: {error}. Untick that row and try again.",
            "error",
        )
        return redirect(review_url)

    if applied:
        write_audit_log(
            "complaint",
            complaint_id,
            "Complaint updated from attached document",
            session["user_id"],
            f"From {attachment['original_filename']}: {describe_applied(applied)}"[:4000],
        )
        labels = ", ".join(s["label"] for s in applied)
        flash(f"{len(applied)} update(s) applied: {labels}.", "success")
        if (
            merged.get("complaint_category") == ADVERSE_EVENT_CATEGORY
            and not complaint.get("linked_case_id")
        ):
            flash(
                "This complaint now reports an adverse event. Create a safety "
                "case from it so it is assessed and reported on time.",
                "warning",
            )
    return redirect(back)

