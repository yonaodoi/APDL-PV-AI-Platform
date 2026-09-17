from flask import Blueprint, abort, flash, redirect, render_template, session, url_for

from app.db import query_all, query_one, transaction
from app.psur.forms import PsurSectionForm
from app.security import login_required
from app.services.psur_evidence import build_psur_evidence_sections

bp = Blueprint("psur_sections", __name__, url_prefix="/psur")

SECTION_TITLES = {
    "executive_summary": "Executive summary",
    "introduction": "Introduction",
    "marketing_authorisation_status": "Worldwide marketing authorisation status",
    "safety_actions": "Actions taken for safety reasons",
    "reference_safety_information": "Changes to reference safety information",
    "exposure": "Estimated exposure and use patterns",
    "signals": "Signal and risk evaluation",
    "benefit_risk": "Integrated benefit-risk analysis",
    "conclusion": "Conclusion and actions",
}

@bp.post("/<int:psur_id>/build-evidence")
@login_required
def build_psur_evidence(psur_id):
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

    evidence_sections = build_psur_evidence_sections(report)

    with transaction() as cursor:
        for section_key, content in evidence_sections.items():
            cursor.execute(
                """
                INSERT INTO pv.psur_section_entries (
                    psur_id,
                    section_key,
                    section_title,
                    content,
                    updated_by,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (psur_id, section_key)
                DO UPDATE SET
                    section_title = EXCLUDED.section_title,
                    content = EXCLUDED.content,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                (
                    psur_id,
                    section_key,
                    SECTION_TITLES[section_key],
                    content,
                    session["user_id"],
                ),
            )

    flash(
        "PSUR evidence draft generated from ADRs, signals and complaints.",
        "success",
    )
    return redirect(
        url_for("psur_sections.manage_psur_sections", psur_id=psur_id)
    )

@bp.route("/<int:psur_id>/sections", methods=["GET", "POST"])
@login_required
def manage_psur_sections(psur_id):
    report = query_one(
        """
        SELECT psur_id, report_number, product_name
        FROM pv.psur_reports
        WHERE psur_id = %s
        """,
        (psur_id,),
    )

    if not report:
        abort(404)

    form = PsurSectionForm()

    if form.validate_on_submit():
        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.psur_section_entries (
                    psur_id,
                    section_key,
                    section_title,
                    content,
                    updated_by,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW())
                ON CONFLICT (psur_id, section_key)
                DO UPDATE SET
                    section_title = EXCLUDED.section_title,
                    content = EXCLUDED.content,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """,
                (
                    psur_id,
                    form.section_key.data,
                    SECTION_TITLES[form.section_key.data],
                    form.content.data.strip(),
                    session["user_id"],
                ),
            )

        flash("PSUR section saved successfully.", "success")
        return redirect(
            url_for("psur_sections.manage_psur_sections", psur_id=psur_id)
        )

    sections = query_all(
        """
        SELECT
            section_key,
            section_title,
            content,
            updated_at
        FROM pv.psur_section_entries
        WHERE psur_id = %s
        ORDER BY updated_at DESC
        """,
        (psur_id,),
    )

    return render_template(
        "psur/psur_sections.html",
        report=report,
        form=form,
        sections=sections,
    )