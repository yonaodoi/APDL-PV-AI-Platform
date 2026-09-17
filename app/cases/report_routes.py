import json
from flask import (
    Blueprint,
    abort,
    render_template,
    request,
    send_file,
    jsonify,
    session,
)

from app.db import query_all, query_one, transaction
from app.security import login_required
from app.services.adr_report import generate_adr_report
from app.services.safety_case_reporting_docx import (
    build_safety_case_reporting_docx,
)


bp = Blueprint("case_reports", __name__, url_prefix="/cases")


@bp.get("/<int:case_id>/adr-report")
@login_required
def download_adr_report(case_id):
    case = query_one(
        """
        SELECT *
        FROM pv.safety_cases
        WHERE case_id = %s
        """,
        (case_id,),
    )

    if case is None:
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

    if product is None:
        abort(404)

    output_path = generate_adr_report(case, product)

    return send_file(
        output_path,
        as_attachment=True,
        download_name=output_path.name,
    )

def _get_filtered_safety_cases():
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

    report_filters = {
        "product": selected_product,
        "country": selected_country,
        "status": selected_status,
        "priority": selected_priority,
        "start_date": start_date,
        "end_date": end_date,
        "reporting_period": (
            f"{start_date or 'All dates'} to "
            f"{end_date or 'All dates'}"
        ),
    }

    return cases, report_filters


@bp.get("/reporting-summary/preview")
@login_required
def preview_safety_case_reporting_summary():
    cases, report_filters = _get_filtered_safety_cases()
    draft_id = request.args.get("draft_id", type=int)
    editable_content = None

    if draft_id:
        draft = query_one(
            """
            SELECT report_content
            FROM pv.safety_case_reporting_drafts
            WHERE draft_id = %s
              AND created_by = %s
            """,
            (draft_id, session["user_id"]),
        )

        if draft is None:
            abort(404)

        editable_content = draft["report_content"]

    return render_template(
        "cases/safety_case_report_preview.html",
        cases=cases,
        report_filters=report_filters,
    )


@bp.get("/reporting-summary/download")
@login_required
def download_safety_case_reporting_summary():
    cases, report_filters = _get_filtered_safety_cases()

    draft_id = request.args.get("draft_id", type=int)
    editable_content = None

    if draft_id:
        draft = query_one(
            """
            SELECT report_content
            FROM pv.safety_case_reporting_drafts
            WHERE draft_id = %s
              AND created_by = %s
            """,
            (draft_id, session["user_id"]),
        )

        if draft is None:
            abort(404)

        editable_content = draft["report_content"]

    report_file = build_safety_case_reporting_docx(
        cases=cases,
        report_filters=report_filters,
        editable_content=editable_content,
    )

    return send_file(
        report_file,
        as_attachment=True,
        download_name="APDL_safety_case_reporting_summary.docx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )

@bp.post("/reporting-summary/drafts")
@login_required
def save_safety_case_reporting_draft():
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
            INSERT INTO pv.safety_case_reporting_drafts (
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