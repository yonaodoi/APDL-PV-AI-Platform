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

bp = Blueprint("complaints", __name__, url_prefix="/complaints")


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

@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_complaint():
    form = ProductComplaintForm()

    _set_complaint_country_choices(form)

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

        flash("Product quality complaint saved successfully.", "success")
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
    complaint = query_one(
        """
        SELECT
            pc.*,
            c.country_name,
            u.full_name AS created_by_name
        FROM pv.product_complaints pc
        LEFT JOIN pv.countries c ON c.country_id = pc.country_id
        LEFT JOIN pv.users u ON u.user_id = pc.created_by
        WHERE pc.complaint_id = %s
        """,
        (complaint_id,),
    )

    if not complaint:
        abort(404)

    review_form = ComplaintReviewForm()
    review_form.status.data = complaint["status"]
    review_form.investigation_summary.data = complaint["investigation_summary"]
    review_form.corrective_action.data = complaint["corrective_action"]
    review_form.closure_date.data = complaint["closure_date"]

    return render_template(
        "complaints/complaint_detail.html",
        complaint=complaint,
        review_form=review_form,
    )


@bp.post("/<int:complaint_id>/review")
@login_required
def review_complaint(complaint_id):
    complaint = query_one(
        """
        SELECT complaint_id
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

    flash("Complaint investigation update saved.", "success")
    return redirect(
        url_for("complaints.complaint_detail", complaint_id=complaint_id)
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

    report_file = build_product_complaint_reporting_docx(
        complaints=complaints,
        report_filters=report_filters,
        editable_content=editable_content,
    )

    return send_file(
        report_file,
        as_attachment=True,
        download_name="APDL_product_complaint_reporting_summary.docx",
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