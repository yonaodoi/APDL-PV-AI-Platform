"""Case pathway: the narrative step."""

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for

from app.cases.routes import bp
from app.db import query_one
from app.security import login_required
from app.services import narratives


def _case(case_id):
    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    if not case:
        abort(404)
    return case


def _locked(case):
    from app.cases.icsr_routes import _locked as locked

    return locked(case)


def _back(case_id, anchor=""):
    return redirect(url_for("cases.case_narrative", case_id=case_id) + anchor)


@bp.get("/<int:case_id>/narrative")
@login_required
def case_narrative(case_id):
    case = _case(case_id)
    all_versions = narratives.versions(case_id)
    current = next((v for v in all_versions if v["status"] in ("Current", "Reviewed")), None)
    draft = next((v for v in all_versions if v["status"] == "Draft"), None)
    if draft and current and draft["version"] < current["version"]:
        draft = None   # an older draft was not used
    selected = None
    if request.args.get("version", "").isdigit():
        selected = next((v for v in all_versions if v["version"] == int(request.args["version"])), None)
    case_type = narratives.case_type_for(case)
    try:
        template = narratives.approved_template(case_type)
    except Exception:
        current_app.logger.exception("Narrative templates unavailable")
        narratives._rollback()
        template = None
    return render_template(
        "cases/case_narrative.html",
        case=case,
        versions=all_versions,
        current=current,
        draft=draft,
        selected=selected,
        case_type=case_type,
        template=template,
        locked=_locked(case),
        can_review=session.get("role") in narratives.REVIEW_ROLES,
        paragraphs=narratives.PARAGRAPHS,
    )


@bp.post("/<int:case_id>/narrative/generate")
@login_required
def generate_narrative(case_id):
    case = _case(case_id)
    if _locked(case):
        flash("Nothing was changed. The case is in sign-off or already reported.", "error")
        return _back(case_id)
    try:
        _, version, issues = narratives.generate_draft(case_id, session["user_id"])
    except ValueError as error:
        flash(f"No draft was made: {error}", "error")
        return _back(case_id)
    flash(f"Draft version {version} was generated from the recorded facts. The case narrative has not changed: "
          "check the draft, edit it if needed and press Use as the case narrative.", "success")
    if issues:
        flash(f"{len(issues)} point(s) to check were found; they are listed beside the draft.", "warning")
    return _back(case_id, "#draft")


@bp.post("/<int:case_id>/narrative/adopt")
@login_required
def adopt_narrative(case_id):
    case = _case(case_id)
    if _locked(case):
        flash("Nothing was changed. The case is in sign-off or already reported.", "error")
        return _back(case_id)
    from_id = request.form.get("from_narrative_id")
    try:
        version = narratives.adopt(case_id, request.form.get("narrative_text"), session["user_id"],
                                   int(from_id) if (from_id or "").isdigit() else None)
    except ValueError as error:
        flash(f"Not saved: {error}", "error")
        return _back(case_id, "#draft")
    flash(f"Narrative version {version} is now the case narrative. It needs review before final reporting.", "success")
    return _back(case_id)


@bp.post("/<int:case_id>/narrative/review")
@login_required
def review_narrative(case_id):
    case = _case(case_id)
    if session.get("role") not in narratives.REVIEW_ROLES:
        flash("Only a medical or quality reviewer, the QPPV or their deputy can mark the narrative reviewed.", "error")
        return _back(case_id)
    if _locked(case):
        flash("Nothing was changed. The case is in sign-off or already reported.", "error")
        return _back(case_id)
    try:
        from app.services.case_approval import qppv_alone
        from app.services.case_workflow import is_designated_qppv

        version = narratives.mark_reviewed(
            case_id, session["user_id"],
            qppv_alone=qppv_alone(session.get("role"), is_designated_qppv(session["user_id"])),
        )
    except ValueError as error:
        flash(f"Not changed: {error}", "error")
        return _back(case_id)
    flash(f"Narrative version {version} marked as reviewed.", "success")
    return redirect(url_for("cases.case_detail", case_id=case_id) + "#pathway")
