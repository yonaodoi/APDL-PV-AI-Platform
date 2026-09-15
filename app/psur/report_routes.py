from flask import Blueprint, abort, send_file

from app.db import query_one
from app.services.psur_report import generate_psur_report
from app.security import login_required

bp = Blueprint("psur_reports", __name__, url_prefix="/psur")


@bp.get("/<int:psur_id>/report")
@login_required
def download_psur_report(psur_id):
    report = query_one(
        """
        SELECT *
        FROM pv.psur_reports
        WHERE psur_id = %s
        """,
        (psur_id,),
    )

    if not report:
        abort(404)

    output_path = generate_psur_report(report)

    return send_file(
        output_path,
        as_attachment=True,
        download_name=output_path.name,
    )