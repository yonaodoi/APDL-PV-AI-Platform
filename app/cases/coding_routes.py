"""Case pathway: the coding step (MedDRA, WHODrug and the local terminology)."""

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from app.cases.routes import bp
from app.db import query_one
from app.security import login_required
from app.services import coding, terminology


def _case(case_id):
    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    if not case:
        abort(404)
    return case


def _locked(case):
    from app.cases.icsr_routes import _locked as locked

    return locked(case)


def _back(case_id, entity=None, entity_id=None):
    anchor = f"#item-{entity}-{entity_id}" if entity else ""
    return redirect(url_for("cases.case_coding", case_id=case_id) + anchor)


def _audit(case_id, action, details):
    from app.db import transaction

    with transaction() as cursor:
        cursor.execute(
            "INSERT INTO pv.case_audit_log (case_id, action, details, performed_by) VALUES (%s, %s, %s, %s)",
            (case_id, action, details[:2000], session["user_id"]),
        )


@bp.get("/<int:case_id>/coding")
@login_required
def case_coding(case_id):
    case = _case(case_id)
    overview = coding.coding_overview(case_id)
    return render_template(
        "cases/case_coding.html",
        case=case,
        overview=overview,
        dictionaries={k: terminology.dictionary_status(k) for k in ("MedDRA", "WHODrug")},
        messages=coding.MESSAGES,
        rules=coding.load_rules(),
        history=coding.history(case_id)[:30],
        locked=_locked(case),
        can_review_terms=session.get("role") in TERM_REVIEW_ROLES,
    )


TERM_REVIEW_ROLES = ("System Administrator", "QPPV", "Deputy QPPV", "Group Head RA & Quality")


@bp.post("/<int:case_id>/coding/run")
@login_required
def run_coding(case_id):
    case = _case(case_id)
    if _locked(case):
        flash("Nothing was changed. The case is in sign-off or already reported.", "error")
        return _back(case_id)
    redo = request.form.get("redo") == "1"
    coded, review, messages = coding.code_case(case_id, session["user_id"], only_missing=not redo)
    for message in messages:
        flash(message, "warning")
    if coded + review == 0:
        flash("Nothing new to code: every reported item already has its coding.", "info")
    else:
        flash(f"Automatic coding finished: {coded} item(s) coded, {review} waiting for your choice.",
              "success" if not review else "warning")
    _audit(case_id, "Automatic coding", f"{coded} coded, {review} for review.")
    return _back(case_id)


def _item_args():
    return request.form.get("entity", ""), request.form.get("entity_id", "0")


@bp.post("/<int:case_id>/coding/choose")
@login_required
def choose_coding(case_id):
    case = _case(case_id)
    entity, entity_id = _item_args()
    if _locked(case):
        flash("Nothing was changed. The case is in sign-off or already reported.", "error")
        return _back(case_id, entity, entity_id)
    try:
        result = coding.choose(case_id, entity, entity_id, request.form.get("dictionary", ""), request.form.get("code", ""),
                               session["user_id"], remember=request.form.get("remember") == "1")
    except ValueError as error:
        flash(f"Coding not saved: {error}", "error")
        return _back(case_id, entity, entity_id)
    label = f"{result['term']} ({result['dictionary']} {result['code']}{', ' + result['version'] if result.get('version') and result['version'] != 'local' else ''})"
    _audit(case_id, "Coding chosen", f"{coding.ENTITY_SPECS.get(entity, {}).get('label', entity)} coded to {label}.")
    flash(f"Coded to {label}." + (" " + result["message"] if result.get("message") else ""),
          "success" if result["status"] == "Coded" else "warning")
    if request.form.get("remember") == "1" and result["dictionary"] in ("MedDRA", "WHODrug"):
        flash("This wording will be coded the same way automatically next time.", "info")
    return _back(case_id, entity, entity_id)


@bp.post("/<int:case_id>/coding/not-coded")
@login_required
def not_coded(case_id):
    case = _case(case_id)
    entity, entity_id = _item_args()
    if _locked(case):
        flash("Nothing was changed. The case is in sign-off or already reported.", "error")
        return _back(case_id, entity, entity_id)
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("Give a reason why this item is not coded.", "error")
        return _back(case_id, entity, entity_id)
    try:
        coding.mark_not_coded(case_id, entity, entity_id, reason, session["user_id"])
    except ValueError as error:
        flash(f"Not saved: {error}", "error")
        return _back(case_id, entity, entity_id)
    _audit(case_id, "Marked as not coded", f"{entity} {entity_id}: {reason}")
    flash("Recorded as not coded.", "success")
    return _back(case_id, entity, entity_id)


@bp.post("/<int:case_id>/coding/propose")
@login_required
def propose_local_term(case_id):
    case = _case(case_id)
    entity, entity_id = _item_args()
    if _locked(case):
        flash("Nothing was changed. The case is in sign-off or already reported.", "error")
        return _back(case_id, entity, entity_id)
    try:
        result = coding.propose_local(case_id, entity, entity_id, request.form.get("name", ""), session["user_id"])
    except ValueError as error:
        flash(f"Not saved: {error}", "error")
        return _back(case_id, entity, entity_id)
    _audit(case_id, "Local term proposed", f"{result['term']} ({result['code']}) for {entity} {entity_id}.")
    flash(f"Local term {result['code']} “{result['term']}” proposed for review. "
          + ("It is approved already, so the item is coded." if result["status"] == "Coded"
             else "The item counts as coded once the term is approved (Administration › Terminology › Local terms)."),
          "success" if result["status"] == "Coded" else "warning")
    return _back(case_id, entity, entity_id)


@bp.get("/<int:case_id>/coding/search")
@login_required
def coding_search(case_id):
    _case(case_id)
    kind = "drug" if request.args.get("kind") == "drug" else "event"
    text = (request.args.get("q") or "").strip()
    if len(text) < 2:
        return jsonify({"results": [], "message": "Type at least two characters."})
    try:
        results = coding.search(kind, text)
    except Exception:
        current_app.logger.exception("Coding search failed")
        terminology._rollback()
        return jsonify({"results": [], "message": "Search failed; try again."}), 500
    dictionary = "MedDRA" if kind == "event" else "WHODrug"
    message = None
    if not terminology.active_version(dictionary):
        message = coding.MESSAGES["meddra_off" if kind == "event" else "whodrug_off"] + " Showing local terms only."
    keep = ("dictionary", "code", "term", "pt_code", "pt_name", "soc_code", "soc_name", "version", "method", "score", "status")
    return jsonify({"results": [{k: r.get(k) for k in keep} for r in results], "message": message})


def auto_code_after_save(case_id, user_id):
    """Run automatic coding after the ICSR is saved; returns flash messages."""
    try:
        coded, review, messages = coding.code_case(case_id, user_id)
    except Exception:
        current_app.logger.exception("Automatic coding failed for case %s", case_id)
        terminology._rollback()
        return [("Automatic coding could not run; open the coding step to code the items.", "warning")]
    out = [(m, "warning") for m in messages]
    if review:
        out.append((f"Coding: {review} item(s) need a coding choice. Open the coding step from the pathway.", "warning"))
    elif coded:
        out.append((f"Coding: {coded} item(s) coded automatically.", "info"))
    return out
