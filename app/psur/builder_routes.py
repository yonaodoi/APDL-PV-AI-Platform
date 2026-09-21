import json
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_file,
)

from app.db import query_all, query_one, transaction
from app.security import login_required
from app.psur.section_definitions import PSUR_SECTION_TITLES
from app.services.psur_builder_ai import (
    propose_psur_section_content,
)
from app.services.psur_builder_docx import (
    build_psur_builder_docx,
)
bp = Blueprint("psur_builder", __name__, url_prefix="/psur")


def get_or_create_psur_builder(psur_id):
    report = query_one(
        """
        SELECT *
        FROM pv.psur_reports
        WHERE psur_id = %s
        """,
        (psur_id,),
    )

    if report is None:
        abort(404)

    builder = query_one(
        """
        SELECT psur_builder_id
        FROM pv.psur_builders
        WHERE psur_id = %s
        """,
        (psur_id,),
    )

    if builder is None:
        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.psur_builders (
                    psur_id,
                    created_by
                )
                VALUES (%s, %s)
                RETURNING psur_builder_id
                """,
                (psur_id, session["user_id"]),
            )
            psur_builder_id = cursor.fetchone()["psur_builder_id"]

            for section_order, (
                section_key,
                section_title,
            ) in enumerate(
                PSUR_SECTION_TITLES.items(),
                start=1,
            ):
                cursor.execute(
                    """
                    INSERT INTO pv.psur_builder_sections (
                        psur_builder_id,
                        section_key,
                        section_title,
                        section_order
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        psur_builder_id,
                        section_key,
                        section_title,
                        section_order,
                    ),
                )
    else:
        psur_builder_id = builder["psur_builder_id"]
    existing_sections = query_all(
        """
        SELECT section_key
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
        """,
        (psur_builder_id,),
    )

    existing_section_keys = {
        section["section_key"]
        for section in existing_sections
    }

    missing_sections = [
        (section_order, section_key, section_title)
        for section_order, (
            section_key,
            section_title,
        ) in enumerate(
            PSUR_SECTION_TITLES.items(),
            start=1,
        )
        if section_key not in existing_section_keys
    ]

    if missing_sections:
        with transaction() as cursor:
            for section_order, section_key, section_title in missing_sections:
                cursor.execute(
                    """
                    INSERT INTO pv.psur_builder_sections (
                        psur_builder_id,
                        section_key,
                        section_title,
                        section_order
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        psur_builder_id,
                        section_key,
                        section_title,
                        section_order,
                    ),
                )

    return report, psur_builder_id


@bp.get("/<int:psur_id>/builder")
@login_required
def psur_builder_home(psur_id):
    report, psur_builder_id = get_or_create_psur_builder(psur_id)

    selected_view = request.args.get("view", "").strip()

    if selected_view not in ("completed", "incomplete"):
        selected_view = ""

    sections = query_all(
        """
        SELECT
            psur_builder_section_id,
            section_key,
            section_title,
            section_order,
            final_content,
            updated_at
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
        ORDER BY section_order
        """,
        (psur_builder_id,),
    )

    next_section = next(
        (
            section
            for section in sections
            if not section["final_content"]
        ),
        sections[0] if sections else None,
    )

    all_sections_complete = bool(sections) and all(
        section["final_content"]
        for section in sections
    )

    return render_template(
        "psur/psur_builder_home.html",
        report=report,
        sections=sections,
        next_section=next_section,
        all_sections_complete=all_sections_complete,
        selected_view=selected_view,
    )

@bp.get("/<int:psur_id>/builder/sections/<section_key>")
@login_required
def psur_builder_section(psur_id, section_key):
    report, psur_builder_id = get_or_create_psur_builder(psur_id)
    navigation_group = request.args.get("group", "").strip()

    if navigation_group not in ("completed", "incomplete"):
        navigation_group = ""

    section = query_one(
        """
        SELECT
            psur_builder_section_id,
            section_key,
            section_title,
            section_order,
            evidence_snapshot,
            generated_content,
            final_content,
            updated_at
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
          AND section_key = %s
        """,
        (psur_builder_id, section_key),
    )

    if section is None:
        abort(404)

    navigation_sections = query_all(
        """
        SELECT
            section_key,
            section_title,
            section_order,
            final_content
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
        ORDER BY section_order
        """,
        (psur_builder_id,),
    )

    if navigation_group == "completed":
        navigation_sections = [
            nav_section
            for nav_section in navigation_sections
            if nav_section["final_content"]
        ]
    elif navigation_group == "incomplete":
        navigation_sections = [
            nav_section
            for nav_section in navigation_sections
            if not nav_section["final_content"]
        ]
    current_index = next(
        (
            index
            for index, nav_section in enumerate(navigation_sections)
            if nav_section["section_key"] == section_key
        ),
        None,
    )

    previous_section = None
    next_section = None

    if current_index is not None:
        if current_index > 0:
            previous_section = navigation_sections[
                current_index - 1
            ]

        if current_index < len(navigation_sections) - 1:
            next_section = navigation_sections[
                current_index + 1
            ]

    proposals = query_all(
        """
        SELECT
            proposal_id,
            proposal_type,
            proposed_content,
            evidence_used,
            created_at
        FROM pv.psur_builder_ai_proposals
        WHERE psur_builder_section_id = %s
          AND decision = 'pending'
        ORDER BY created_at DESC
        """,
        (section["psur_builder_section_id"],),
    )

    return render_template(
        "psur/psur_builder_section.html",
        report=report,
        section=section,
        previous_section=previous_section,
        next_section=next_section,
        total_sections=len(navigation_sections),
        navigation_group=navigation_group,
        group_position=(current_index + 1) if current_index is not None else 1,
        proposals=proposals,
    )
@bp.post("/<int:psur_id>/builder/sections/<section_key>/save")
@login_required
def save_psur_builder_section(psur_id, section_key):
    report, psur_builder_id = get_or_create_psur_builder(psur_id)

    section = query_one(
        """
        SELECT psur_builder_section_id
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
          AND section_key = %s
        """,
        (psur_builder_id, section_key),
    )

    if section is None:
        abort(404)

    final_content = request.form.get(
        "final_content",
        "",
    ).strip()

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.psur_builder_sections
            SET
                final_content = %s,
                updated_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE psur_builder_section_id = %s
            """,
            (
                final_content or None,
                session["user_id"],
                section["psur_builder_section_id"],
            ),
        )

    flash("PSUR section content saved.", "success")
    navigation_group = request.form.get(
        "navigation_group",
        "",
    ).strip()

    if navigation_group not in ("completed", "incomplete"):
        navigation_group = ""

    next_section_key = request.form.get(
        "next_section_key",
        "",
    ).strip()

    if next_section_key:
        next_section = query_one(
            """
            SELECT section_key
            FROM pv.psur_builder_sections
            WHERE psur_builder_id = %s
              AND section_key = %s
            """,
            (psur_builder_id, next_section_key),
        )

        if next_section:
            return redirect(
                url_for(
                    "psur_builder.psur_builder_section",
                    psur_id=report["psur_id"],
                    section_key=next_section["section_key"],
                    group=navigation_group,
                )
            )

    return redirect(
        url_for(
            "psur_builder.psur_builder_home",
            psur_id=report["psur_id"],
            view=navigation_group,
        )
    )

@bp.post("/<int:psur_id>/builder/sections/<section_key>/propose")
@login_required
def propose_psur_builder_content(psur_id, section_key):
    report, psur_builder_id = get_or_create_psur_builder(psur_id)

    section = query_one(
        """
        SELECT
            psur_builder_section_id,
            section_title,
            final_content
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
          AND section_key = %s
        """,
        (psur_builder_id, section_key),
    )

    if section is None:
        abort(404)
    if not section["final_content"]:
        flash(
            "Save a section response before requesting an AI improvement.",
            "error",
        )
        return redirect(
            url_for(
                "psur_builder.psur_builder_section",
                psur_id=psur_id,
                section_key=section_key,
            )
        )


    try:
        proposal = propose_psur_section_content(
            report=report,
            section_key=section_key,
            section_title=section["section_title"],
            saved_content=section["final_content"],
        )
    except Exception as error:
        flash(
            f"The AI proposal could not be generated: {error}",
        )
        return redirect(
            url_for(
                "psur_builder.psur_builder_section",
                psur_id=psur_id,
                section_key=section_key,
            )
        )

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.psur_builder_sections
            SET
                generated_content = %s,
                evidence_snapshot = %s::jsonb,
                updated_at = CURRENT_TIMESTAMP
            WHERE psur_builder_section_id = %s
            """,
            (
                proposal["proposed_content"],
                json.dumps(
                    proposal["evidence_used"],
                    default=str,
                ),
                section["psur_builder_section_id"],
            ),
        )

        cursor.execute(
            """
            INSERT INTO pv.psur_builder_ai_proposals (
                psur_builder_section_id,
                proposal_type,
                proposed_content,
                evidence_used,
                created_by
            )
            VALUES (%s, %s, %s, %s::jsonb, %s)
            """,
            (
                section["psur_builder_section_id"],
                "suggestion",
                proposal["proposed_content"],
                json.dumps(
                    proposal["evidence_used"],
                    default=str,
                ),
                session["user_id"],
            ),
        )

    flash("A proposed section narrative is ready for review.", "success")

    return redirect(
        url_for(
            "psur_builder.psur_builder_section",
            psur_id=psur_id,
            section_key=section_key,
        )
    )


@bp.post(
    "/<int:psur_id>/builder/sections/<section_key>/"
    "proposals/<int:proposal_id>/<decision>"
)
@login_required
def decide_psur_builder_proposal(
    psur_id,
    section_key,
    proposal_id,
    decision,
):
    if decision not in ("accepted", "rejected"):
        abort(404)

    report, psur_builder_id = get_or_create_psur_builder(psur_id)

    incomplete_sections = query_all(
        """
        SELECT section_title
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
          AND (
              final_content IS NULL
              OR BTRIM(final_content) = ''
          )
        ORDER BY section_order
        """,
        (psur_builder_id,),
    )

    if incomplete_sections:
        flash(
            f"The PSUR has {len(incomplete_sections)} section(s) still "
            "to complete before final preview or Word download.",
            "error",
        )
        return redirect(
            url_for(
                "psur_builder.psur_builder_home",
                psur_id=psur_id,
                view="incomplete",
            )
        )
    selected_view = request.args.get("view", "").strip()

    if selected_view not in ("completed", "incomplete"):
        selected_view = ""

    proposal = query_one(
        """
        SELECT
            proposals.proposal_id,
            proposals.proposed_content,
            sections.psur_builder_section_id
        FROM pv.psur_builder_ai_proposals AS proposals
        INNER JOIN pv.psur_builder_sections AS sections
            ON sections.psur_builder_section_id =
                proposals.psur_builder_section_id
        WHERE proposals.proposal_id = %s
          AND sections.psur_builder_id = %s
          AND sections.section_key = %s
          AND proposals.decision = 'pending'
        """,
        (proposal_id, psur_builder_id, section_key),
    )

    if proposal is None:
        abort(404)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.psur_builder_ai_proposals
            SET
                decision = %s,
                decided_by = %s,
                decided_at = CURRENT_TIMESTAMP
            WHERE proposal_id = %s
            """,
            (
                decision,
                session["user_id"],
                proposal_id,
            ),
        )

        if decision == "accepted":
            cursor.execute(
                """
                UPDATE pv.psur_builder_sections
                SET
                    final_content = %s,
                    updated_by = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE psur_builder_section_id = %s
                """,
                (
                    proposal["proposed_content"],
                    session["user_id"],
                    proposal["psur_builder_section_id"],
                ),
            )

    message = (
        "Proposed content was accepted and placed in the section."
        if decision == "accepted"
        else "Proposed content was rejected."
    )
    flash(message, "success")

    return redirect(
        url_for(
            "psur_builder.psur_builder_section",
            psur_id=report["psur_id"],
            section_key=section_key,
        )
    )

@bp.get("/<int:psur_id>/builder/report")
@login_required
def download_psur_builder_report(psur_id):
    report, psur_builder_id = get_or_create_psur_builder(psur_id)
    incomplete_sections = query_all(
        """
        SELECT section_title
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
          AND (
              final_content IS NULL
              OR BTRIM(final_content) = ''
          )
        ORDER BY section_order
        """,
        (psur_builder_id,),
    )

    if incomplete_sections:
        flash(
            f"The PSUR has {len(incomplete_sections)} section(s) still "
            "to complete before final preview or Word download.",
            "error",
        )
        return redirect(
            url_for(
                "psur_builder.psur_builder_home",
                psur_id=psur_id,
                view="incomplete",
            )
        )

    sections = query_all(
        """
        SELECT
            section_key,
            section_title,
            section_order,
            final_content
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
          AND final_content IS NOT NULL
          AND BTRIM(final_content) <> ''
        ORDER BY section_order
        """,
        (psur_builder_id,),
    )

    report_file = build_psur_builder_docx(
        report=report,
        sections=sections,
    )

    filename = (
        "APDL_PBRER_"
        f"{report['report_number']}.docx"
    )

    return send_file(
        report_file,
        as_attachment=True,
        download_name=filename,
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )

@bp.get("/<int:psur_id>/builder/preview")
@login_required
def preview_psur_builder_report(psur_id):
    report, psur_builder_id = get_or_create_psur_builder(psur_id)
    navigation_group = request.args.get("group", "").strip()

    if navigation_group not in ("completed", "incomplete"):
        navigation_group = ""

    sections = query_all(
        """
        SELECT
            section_key,
            section_title,
            section_order,
            final_content
        FROM pv.psur_builder_sections
        WHERE psur_builder_id = %s
          AND final_content IS NOT NULL
          AND BTRIM(final_content) <> ''
        ORDER BY section_order
        """,
        (psur_builder_id,),
    )

    return render_template(
        "psur/psur_builder_preview.html",
        report=report,
        sections=sections,
    )