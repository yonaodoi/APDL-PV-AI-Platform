from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    session,
    url_for,
)

from app.audit import write_audit_log
from app.db import query_all, query_one, transaction
from app.security import login_required
from app.services.ai_case_assessment import generate_case_assessment


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


@bp.post("/cases/<int:case_id>/ai-assessment/generate")
@login_required
def generate_ai_case_assessment(case_id):
    case, product, safety_assessment = get_case_context(case_id)

    documents, apdl_document, innovator_document = (
        rsi_documents_for_product(product)
    )

    if not documents:
        flash(
            "Upload APDL Product Information and innovator RSI for this "
            "product before generating the AI assessment report.",
            "error",
        )
        return redirect(
            url_for(
                "case_ai_reports.view_ai_case_assessment",
                case_id=case_id,
            )
        )

    try:
        report_text = generate_case_assessment(
            case=dict(case),
            product=dict(product or {}),
            apdl_product_information=document_with_reactions(
                apdl_document
            ),
            innovator_rsi=document_with_reactions(
                innovator_document
            ),
            safety_assessment=dict(safety_assessment or {}),
        )
    except Exception:
        flash(
            "The local AI report could not be generated. Confirm that "
            "Ollama is installed and llama3.2:3b is available.",
            "error",
        )
        return redirect(
            url_for(
                "case_ai_reports.view_ai_case_assessment",
                case_id=case_id,
            )
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
                "llama3.2:1b",
                session["user_id"],
            ),
        )

    write_audit_log(
        "case",
        case_id,
        "AI case assessment report generated",
        session["user_id"],
        (
            "Local model: llama3.2:3b; "
            "APDL PI and innovator RSI comparison requested."
        ),
    )

    flash(
        "AI case assessment report generated successfully.",
        "success",
    )
    return redirect(
        url_for(
            "case_ai_reports.view_ai_case_assessment",
            case_id=case_id,
        )
    )