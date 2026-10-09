from flask import Blueprint, abort, flash, render_template, send_file

from app.db import query_all, query_one
from app.services.psur_report import generate_psur_report
from app.security import login_required

bp = Blueprint("psur_reports", __name__, url_prefix="/psur")

@bp.get("/<int:psur_id>/preview")
@login_required
def preview_psur_report(psur_id):
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

    sections = query_all(
        """
        SELECT
            section_key,
            section_title,
            content,
            updated_at
        FROM pv.psur_section_entries
        WHERE psur_id = %s
        ORDER BY CASE section_key
            WHEN 'executive_summary' THEN 1
            WHEN 'introduction' THEN 2
            WHEN 'marketing_authorisation_status' THEN 3
            WHEN 'safety_actions' THEN 4
            WHEN 'reference_safety_information' THEN 5
            WHEN 'exposure' THEN 6
            WHEN 'signals' THEN 7
            WHEN 'benefit_risk' THEN 8
            WHEN 'conclusion' THEN 9
            ELSE 10
        END
        """,
        (psur_id,),
    )

    return render_template(
        "psur/psur_report_preview.html",
        report=report,
        sections=sections,
    )


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

    def psur_template_context():
        from app.services.psur_tabulations import build_report_tabulation
        from app.services.report_fields import psur_context

        sections = query_all(
            """
            SELECT section_key AS section_title, content
            FROM pv.psur_section_entries
            WHERE psur_id = %s AND content IS NOT NULL AND BTRIM(content) <> ''
            ORDER BY section_key
            """,
            (psur_id,),
        )
        try:
            tabulation = build_report_tabulation(report)
        except Exception:
            tabulation = None
        return psur_context(report, sections, tabulation)

    from app.services.report_templates import render_with_active

    templated, notice = render_with_active("psur", psur_template_context)
    if notice:
        flash(notice, "warning")
    if templated is not None:
        return send_file(
            templated,
            as_attachment=True,
            download_name=f"PSUR_{report.get('report_number') or psur_id}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    output_path = generate_psur_report(report)

    return send_file(
        output_path,
        as_attachment=True,
        download_name=output_path.name,
    )