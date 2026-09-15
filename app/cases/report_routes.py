from flask import Blueprint, abort, send_file

from app.db import query_one
from app.security import login_required
from app.services.adr_report import generate_adr_report


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