from threading import Thread
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    send_file,
    url_for,
    current_app,
    jsonify,
)

from app.audit import write_audit_log
from app.db import query_all, query_one, transaction
from app.security import login_required, roles_required
from app.services.ai_case_assessment import (
    OLLAMA_MODEL,
    generate_case_assessment,
)
from app.services.rsi_lookup import automatic_dailymed_assessment
from app.services.ai_case_assessment_docx import (
    build_ai_case_assessment_docx,
)


bp = Blueprint("case_ai_reports", __name__)


def get_case_context(case_id):
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
        LIMIT 1
        """,
        (case_id,),
    )

    safety_assessment = query_one(
        """
        SELECT *
        FROM pv.case_safety_assessments
        WHERE case_id = %s
        """,
        (case_id,),
    )

    return case, product, safety_assessment


def rsi_documents_for_product(product):
    if not product:
        return [], None, None

    product_name = product.get("product_name") or ""
    generic_name = product.get("generic_name") or ""

    documents = query_all(
        """
        SELECT *
        FROM pv.reference_safety_information
        WHERE is_current = TRUE
          AND (
              LOWER(product_name) = LOWER(%s)
              OR LOWER(product_name) = LOWER(%s)
              OR LOWER(COALESCE(active_substance, '')) = LOWER(%s)
              OR LOWER(COALESCE(active_substance, '')) = LOWER(%s)
          )
        ORDER BY rsi_id DESC
        """,
        (
            product_name,
            generic_name,
            product_name,
            generic_name,
        ),
    )

    apdl_document = None
    innovator_document = None

    for document in documents:
        document_type = (
            document.get("document_type") or ""
        ).lower()

        if (
            "apdl" in document_type
            or "local product information" in document_type
        ):
            apdl_document = document
        elif (
            document.get("reference_product_name")
            or "innovator" in document_type
            or "reference safety" in document_type
            or "rsi" in document_type
        ):
            innovator_document = document

    return documents, apdl_document, innovator_document


def document_with_reactions(document):
    if not document:
        return {}

    reactions = query_all(
        """
        SELECT
            reaction_term,
            source_excerpt,
            review_status
        FROM pv.rsi_reactions
        WHERE rsi_id = %s
          AND review_status IN ('Proposed', 'Verified')
        ORDER BY
            CASE WHEN review_status = 'Verified' THEN 0 ELSE 1 END,
            reaction_term
        """,
        (document["rsi_id"],),
    )

    return {
        "document": dict(document),
        "extracted_reactions": [dict(row) for row in reactions],
    }

def update_ai_assessment_generation_job(
    job_id,
    status,
    current_stage,
    error_message=None,
    completed=False,
):
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.case_ai_assessment_generation_jobs
            SET
                status = %s,
                current_stage = %s,
                error_message = %s,
                started_at = COALESCE(
                    started_at,
                    CURRENT_TIMESTAMP
                ),
                completed_at = CASE
                    WHEN %s THEN CURRENT_TIMESTAMP
                    ELSE completed_at
                END
            WHERE job_id = %s
            """,
            (
                status,
                current_stage,
                error_message,
                completed,
                job_id,
            ),
        )

@bp.get("/cases/<int:case_id>/ai-assessment")
@login_required
def view_ai_case_assessment(case_id):
    case, product, safety_assessment = get_case_context(case_id)

    report = query_one(
        """
        SELECT
            r.*,
            u.full_name AS generated_by_name,
            a.full_name AS approved_by_name
        FROM pv.case_ai_assessment_reports AS r
        LEFT JOIN pv.users AS u ON u.user_id = r.generated_by
        LEFT JOIN pv.users AS a ON a.user_id = r.approved_by
        WHERE r.case_id = %s
        ORDER BY r.created_at DESC
        LIMIT 1
        """,
        (case_id,),
    )

    return render_template(
        "cases/ai_case_assessment.html",
        case=case,
        product=product,
        safety_assessment=safety_assessment,
        report=report,
    )

def run_ai_case_assessment_generation(
    app,
    job_id,
    case_id,
    user_id,
):
    with app.app_context():
        try:
            update_ai_assessment_generation_job(
                job_id,
                "Processing",
                "Retrieving case information and product details.",
            )

            case, product, safety_assessment = get_case_context(case_id)

            update_ai_assessment_generation_job(
                job_id,
                "Processing",
                "Case information and product details retrieved.",
            )

            rsi_documents_for_product(product)

            update_ai_assessment_generation_job(
                job_id,
                "Processing",
                "Reference safety information retrieved.",
            )

            update_ai_assessment_generation_job(
                job_id,
                "Processing",
                "AI assessment narrative is being generated.",
            )

            report_text = generate_case_assessment(
                case=dict(case),
                product=dict(product or {}),
                apdl_product_information={},
                innovator_rsi={},
                safety_assessment=dict(safety_assessment or {}),
            )

            update_ai_assessment_generation_job(
                job_id,
                "Processing",
                "Assessment narrative is being saved.",
            )

            with transaction() as cursor:
                cursor.execute(
                    """
                    INSERT INTO pv.case_ai_assessment_reports (
                        case_id,
                        report_text,
                        model_name,
                        generation_status,
                        generated_by
                    )
                    VALUES (%s, %s, %s, 'Generated', %s)
                    """,
                    (
                        case_id,
                        report_text,
                        OLLAMA_MODEL,
                        user_id,
                    ),
                )

            write_audit_log(
                "case",
                case_id,
                "AI case assessment report generated",
                user_id,
                (
                    f"Local model: {OLLAMA_MODEL}; "
                    "APDL PI and innovator RSI comparison requested."
                ),
            )

            update_ai_assessment_generation_job(
                job_id,
                "Processing",
                "Finalising assessment.",
            )

            update_ai_assessment_generation_job(
                job_id,
                "Completed",
                "Assessment completed.",
                completed=True,
            )

        except Exception as error:
            update_ai_assessment_generation_job(
                job_id,
                "Failed",
                "Assessment generation could not be completed.",
                error_message=str(error),
                completed=True,
            )


@bp.post("/cases/<int:case_id>/ai-assessment/generate")
@login_required
def generate_ai_case_assessment(case_id):
    get_case_context(case_id)

    existing_job = query_one(
        """
        SELECT job_id
        FROM pv.case_ai_assessment_generation_jobs
        WHERE case_id = %s
          AND requested_by = %s
          AND status IN ('Queued', 'Processing')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (case_id, session["user_id"]),
    )

    if existing_job:
        return redirect(
            url_for(
                "case_ai_reports.generating_ai_case_assessment",
                case_id=case_id,
                job_id=existing_job["job_id"],
            )
        )

    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.case_ai_assessment_generation_jobs (
                case_id,
                requested_by,
                status,
                current_stage
            )
            VALUES (%s, %s, 'Queued', %s)
            RETURNING job_id
            """,
            (
                case_id,
                session["user_id"],
                "Assessment request received.",
            ),
        )
        job_id = cursor.fetchone()["job_id"]

    app = current_app._get_current_object()

    Thread(
        target=run_ai_case_assessment_generation,
        args=(
            app,
            job_id,
            case_id,
            session["user_id"],
        ),
        daemon=True,
    ).start()

    return redirect(
        url_for(
            "case_ai_reports.generating_ai_case_assessment",
            case_id=case_id,
            job_id=job_id,
        )
    )


@bp.get(
    "/cases/<int:case_id>/ai-assessment/generating/<int:job_id>"
)
@login_required
def generating_ai_case_assessment(case_id, job_id):
    case, _, _ = get_case_context(case_id)

    job = query_one(
        """
        SELECT
            job_id,
            status,
            current_stage,
            error_message
        FROM pv.case_ai_assessment_generation_jobs
        WHERE job_id = %s
          AND case_id = %s
          AND requested_by = %s
        """,
        (
            job_id,
            case_id,
            session["user_id"],
        ),
    )

    if not job:
        abort(404)

    return render_template(
        "cases/ai_case_assessment_generating.html",
        case=case,
        job=job,
    )


@bp.get(
    "/cases/<int:case_id>/ai-assessment/jobs/<int:job_id>/status"
)
@login_required
def ai_case_assessment_generation_status(case_id, job_id):
    job = query_one(
        """
        SELECT
            status,
            current_stage,
            error_message
        FROM pv.case_ai_assessment_generation_jobs
        WHERE job_id = %s
          AND case_id = %s
          AND requested_by = %s
        """,
        (
            job_id,
            case_id,
            session["user_id"],
        ),
    )

    if not job:
        abort(404)

    return jsonify(
        status=job["status"],
        current_stage=job["current_stage"],
        error_message=job["error_message"],
    )

@bp.post("/cases/<int:case_id>/ai-assessment/edit")
@login_required
def save_ai_case_assessment(case_id):
    report = query_one(
        """
        SELECT ai_report_id
        FROM pv.case_ai_assessment_reports
        WHERE case_id = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (case_id,),
    )

    if not report:
        abort(404)

    report_text = request.form.get("report_text", "").strip()

    if not report_text:
        flash("The assessment report cannot be empty.", "error")
        return redirect(
            url_for(
                "case_ai_reports.view_ai_case_assessment",
                case_id=case_id,
            )
        )

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.case_ai_assessment_reports
            SET
                report_text = %s,
                generation_status = 'Generated',
                approved_by = NULL,
                approved_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE ai_report_id = %s
            """,
            (report_text, report["ai_report_id"]),
        )

    write_audit_log(
        "case",
        case_id,
        "AI case assessment report edited",
        session["user_id"],
        "Manual amendment saved; QPPV approval reset.",
    )

    flash("AI case assessment report changes saved.", "success")
    return redirect(
        url_for(
            "case_ai_reports.view_ai_case_assessment",
            case_id=case_id,
        )
    )

@bp.get("/cases/<int:case_id>/ai-assessment/download")
@login_required
def download_ai_case_assessment(case_id):
    case, product, _ = get_case_context(case_id)

    report = query_one(
        """
        SELECT
            r.*,
            u.full_name AS generated_by_name
        FROM pv.case_ai_assessment_reports AS r
        LEFT JOIN pv.users AS u ON u.user_id = r.generated_by
        WHERE r.case_id = %s
        ORDER BY r.created_at DESC
        LIMIT 1
        """,
        (case_id,),
    )

    if not report:
        abort(404)

    output = build_ai_case_assessment_docx(
        dict(case),
        dict(product or {}),
        dict(report),
    )

    write_audit_log(
        "case",
        case_id,
        "AI case assessment report downloaded",
        session["user_id"],
        "APDL-formatted Word report generated for download.",
    )

    return send_file(
        output,
        as_attachment=True,
        download_name=(
            f"AI_CASE_ASSESSMENT_{case['case_number']}.docx"
        ),
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )

@bp.get("/cases/<int:case_id>/ai-assessment/preview")
@login_required
def preview_ai_case_assessment(case_id):
    case, product, _ = get_case_context(case_id)

    report = query_one(
        """
        SELECT
            r.*,
            u.full_name AS generated_by_name
        FROM pv.case_ai_assessment_reports AS r
        LEFT JOIN pv.users AS u ON u.user_id = r.generated_by
        WHERE r.case_id = %s
        ORDER BY r.created_at DESC
        LIMIT 1
        """,
        (case_id,),
    )

    if not report:
        abort(404)

    return render_template(
        "cases/ai_case_assessment_preview.html",
        case=case,
        product=product,
        report=report,
    )

@bp.post("/cases/<int:case_id>/ai-assessment/approve")
@roles_required("QPPV", "System Administrator")
def approve_ai_case_assessment(case_id):
    report = query_one(
        """
        SELECT ai_report_id
        FROM pv.case_ai_assessment_reports
        WHERE case_id = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (case_id,),
    )

    if not report:
        abort(404)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.case_ai_assessment_reports
            SET
                generation_status = 'Approved',
                approved_by = %s,
                approved_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE ai_report_id = %s
            """,
            (session["user_id"], report["ai_report_id"]),
        )

    write_audit_log(
        "case",
        case_id,
        "AI case assessment approved",
        session["user_id"],
        "QPPV or System Administrator approval recorded.",
    )

    flash("AI case assessment approved successfully.", "success")
    return redirect(
        url_for(
            "case_ai_reports.preview_ai_case_assessment",
            case_id=case_id,
        )
    )

@bp.post("/cases/<int:case_id>/ai-assessment/signatories")
@login_required
def save_ai_case_assessment_signatories(case_id):
    report = query_one(
        """
        SELECT ai_report_id, report_text
        FROM pv.case_ai_assessment_reports
        WHERE case_id = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (case_id,),
    )

    if not report:
        abort(404)

    report_text = request.form.get("report_text", "").replace("*", "").strip()

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.case_ai_assessment_reports
            SET
                report_text = %s,
                prepared_by_name = %s,
                prepared_by_designation = %s,
                reviewed_by_name = %s,
                reviewed_by_designation = %s,
                authorised_by_name = %s,
                authorised_by_designation = %s,
                generation_status = 'Generated',
                approved_by = NULL,
                approved_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE ai_report_id = %s
            """,
            (
                report_text or report["report_text"],
                request.form.get("prepared_by_name", "").strip() or None,
                request.form.get(
                    "prepared_by_designation", ""
                ).strip() or None,
                request.form.get("reviewed_by_name", "").strip() or None,
                request.form.get(
                    "reviewed_by_designation", ""
                ).strip() or None,
                request.form.get("authorised_by_name", "").strip() or None,
                request.form.get(
                    "authorised_by_designation", ""
                ).strip() or None,
                report["ai_report_id"],
            ),
        )

    write_audit_log(
        "case",
        case_id,
        "AI case assessment and signatories saved",
        session["user_id"],
        "Report text or signatory details updated; approval reset.",
    )

    flash("AI assessment report and signatories saved.", "success")
    return redirect(
        url_for(
            "case_ai_reports.preview_ai_case_assessment",
            case_id=case_id,
        )
    )