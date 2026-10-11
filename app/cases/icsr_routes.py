"""Case pathway pages: minimum validity and the full ICSR form."""

from datetime import date

from flask import abort, current_app, flash, redirect, render_template, request, session, url_for

from app.audit import write_audit_log
from app.cases.routes import bp
from app.db import query_all, query_one, transaction
from app.security import login_required
from app.services import case_pathway, icsr

EDIT_ROLES = None  # every signed-in PV user may edit cases, as on the existing case form


def _countries():
    return query_all("SELECT country_id, country_name FROM pv.countries ORDER BY country_name")


def _locked(case):
    """Signed-off cases are changed only after being returned."""
    return case.get("approval_stage") in ("Pending review", "Pending approval", "Approved") or \
        case.get("workflow_status") in ("Submitted", "Closed")


# --------------------------------------------------------------------------
# Validation (minimum case validity)
# --------------------------------------------------------------------------

@bp.route("/<int:case_id>/validity", methods=["GET", "POST"])
@login_required
def case_validity(case_id):
    record = icsr.load_icsr(case_id)
    if not record:
        abort(404)
    case = record["case"]
    suggestions = case_pathway.suggest_validity(case, record["products"], record["reactions"])

    if request.method == "POST":
        values = {}
        for key, label in case_pathway.VALIDITY_CRITERIA:
            value = request.form.get(key, "")
            if value not in case_pathway.VALIDITY_CHOICES:
                flash(f"Nothing was saved. Choose Present, Absent or Uncertain for “{label}”.", "error")
                return redirect(url_for("cases.case_validity", case_id=case_id))
            values[key] = value
        notes = (request.form.get("validity_notes") or "").strip()[:4000]
        outcome = case_pathway.validity_outcome(values)
        if outcome == "Valid":
            decision = "Valid"
        elif any(v == "Uncertain" for v in values.values()) and request.form.get("decision") != "await":
            flash(
                "Nothing was saved. At least one criterion is Uncertain: check the source and record Present or "
                "Absent, or choose “Request the missing information” if it must be asked for.",
                "error",
            )
            return redirect(url_for("cases.case_validity", case_id=case_id))
        else:
            decision = "Awaiting information"

        first_valid = decision == "Valid" and not case.get("icsr_started_at")
        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.safety_cases
                SET validity_patient = %s, validity_reporter = %s, validity_product = %s,
                    validity_event = %s, validity_decision = %s, validity_notes = %s,
                    validity_decided_by = %s, validity_decided_at = NOW(),
                    icsr_started_at = CASE WHEN %s THEN COALESCE(icsr_started_at, NOW()) ELSE icsr_started_at END,
                    workflow_status = CASE
                        WHEN %s = 'Valid' AND workflow_status IN ('New', 'Triage', 'Follow-up requested') THEN 'Assessment'
                        WHEN %s = 'Awaiting information' AND workflow_status IN ('New', 'Triage') THEN 'Follow-up requested'
                        ELSE workflow_status END,
                    updated_at = NOW()
                WHERE case_id = %s
                """,
                (
                    values["validity_patient"], values["validity_reporter"], values["validity_product"],
                    values["validity_event"], decision, notes or None, session["user_id"],
                    decision == "Valid", decision, decision, case_id,
                ),
            )
            cursor.execute(
                "INSERT INTO pv.case_audit_log (case_id, action, details, performed_by) VALUES (%s, %s, %s, %s)",
                (
                    case_id, "Validity assessed",
                    f"Decision: {decision}. "
                    + "; ".join(f"{label}: {values[key]}" for key, label in case_pathway.VALIDITY_CRITERIA)
                    + (f". Notes: {notes}" if notes else "."),
                    session["user_id"],
                ),
            )

        if decision == "Valid":
            flash(
                "Validity saved: this is a valid case. Next step: complete the ICSR form (supported information is already filled in)."
                if first_valid else "Validity saved: the case remains valid.",
                "success",
            )
            return redirect(url_for("cases.icsr_form", case_id=case_id))

        # Not valid: make sure the follow-up items exist so the request,
        # reminders and due dates run through the usual follow-up process.
        try:
            from app.services.case_completeness import refresh_case_completeness

            fresh = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
            products = query_all("SELECT * FROM pv.case_products WHERE case_id = %s ORDER BY case_product_id", (case_id,))
            refresh_case_completeness(fresh, products)
        except Exception:
            current_app.logger.exception("Could not refresh follow-up items for case %s", case_id)
        flash(
            "Validity saved: the case is in Awaiting information. Prepare the follow-up request below; "
            "reminders and the due date are tracked under Awaiting information.",
            "warning",
        )
        return redirect(url_for("case_review.follow_up_tasks") + f"#case-{case_id}")

    follow_up = _follow_up(case_id)
    return render_template(
        "cases/case_validity.html",
        case=case,
        record=record,
        criteria=case_pathway.VALIDITY_CRITERIA,
        choices=case_pathway.VALIDITY_CHOICES,
        suggestions=suggestions,
        follow_up=follow_up,
    )


def _follow_up(case_id):
    try:
        tasks = query_all(
            """
            SELECT t.*, u.full_name AS assigned_to_name
            FROM pv.case_follow_up_tasks AS t
            LEFT JOIN pv.users AS u ON u.user_id = t.assigned_to
            WHERE t.case_id = %s
            """,
            (case_id,),
        )
        requests = query_all(
            "SELECT status, sent_at, kind FROM pv.case_follow_up_requests WHERE case_id = %s",
            (case_id,),
        )
    except Exception:
        from app.db import get_db

        get_db().rollback()
        tasks, requests = [], []
    return case_pathway.follow_up_summary(tasks, requests)


# --------------------------------------------------------------------------
# ICSR form (sections A–I)
# --------------------------------------------------------------------------

@bp.route("/<int:case_id>/icsr", methods=["GET", "POST"])
@login_required
def icsr_form(case_id):
    record = icsr.load_icsr(case_id)
    if not record:
        abort(404)
    case = record["case"]
    if case.get("validity_decision") == "Awaiting information" and request.method == "GET" and not request.args.get("anyway"):
        flash(
            "This case is not valid yet (awaiting the missing minimum information). You can still record what has "
            "arrived; confirm validity again when the case is complete.",
            "warning",
        )

    errors = {}
    if request.method == "POST":
        if _locked(case):
            flash("Nothing was saved. The case is in sign-off or already submitted; it must be returned before it can be changed.", "error")
            return redirect(url_for("cases.icsr_form", case_id=case_id))
        data, errors = icsr.parse_icsr_form(request.form)
        errors.update(icsr.check_icsr(data))
        if not errors:
            changes = icsr.save_icsr(case_id, data, session["user_id"])
            if "case_narrative" in data["case"]:
                try:
                    from app.services import narratives

                    narratives.record_form_edit(case_id, case.get("case_narrative"), data["case"].get("case_narrative"), session["user_id"])
                except Exception:
                    current_app.logger.exception("Narrative version not recorded for case %s", case_id)
                    from app.services.terminology import _rollback

                    _rollback()
            meaningful = [c for c in changes if not c.endswith("reviewed")]
            flash(
                f"ICSR saved ({len(meaningful)} change{'s' if len(meaningful) != 1 else ''}). "
                "Completeness, coding and listedness were checked again; see the pathway for the next step.",
                "success",
            )
            _after_save(case_id)
            if case.get("validity_decision") == "Awaiting information":
                flash(
                    "This case is still awaiting the minimum information. When it is complete, confirm validity "
                    "again from the pathway to move it forward.",
                    "info",
                )
            target = request.form.get("next") or ""
            if target == "stay":
                return redirect(url_for("cases.icsr_form", case_id=case_id) + (request.form.get("anchor") or ""))
            return redirect(url_for("cases.case_detail", case_id=case_id) + "#pathway")
        flash(
            f"Nothing was saved yet: {len(errors)} item{'s need' if len(errors) != 1 else ' needs'} correcting. "
            "Each one is listed at the top of the form and marked next to the field.",
            "error",
        )
        record = _record_from_submission(record, data)

    return render_template(
        "cases/icsr_form.html",
        record=record,
        case=record["case"],
        errors=errors,
        countries=_countries(),
        locked=_locked(case),
        c=icsr,
        focus=request.args.get("focus") or "",
        coding_slots=_coding_slots(case_id),
        checklist=icsr.field_checks(record, _completeness(case_id)),
    )


def _coding_slots(case_id):
    """Coding status per reported field, shown beside the field in the form."""
    from app.services import coding

    try:
        overview = coding.coding_overview(case_id)
    except Exception:
        current_app.logger.exception("Coding status unavailable for case %s", case_id)
        from app.services.terminology import _rollback

        _rollback()
        return {}
    return {item["focus"]: item for item in overview["items"]}


def _completeness(case_id):
    try:
        return query_all("SELECT * FROM pv.case_completeness_checks WHERE case_id = %s", (case_id,))
    except Exception:
        from app.services.terminology import _rollback

        _rollback()
        return []


def _after_save(case_id):
    """Re-run the automatic checks after a save; failures never block the save."""
    from app.services.case_completeness import refresh_case_completeness
    from app.services.case_listedness import listedness_message, run_automatic_listedness

    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    products = query_all("SELECT * FROM pv.case_products WHERE case_id = %s ORDER BY case_product_id", (case_id,))
    try:
        refresh_case_completeness(case, products)
    except Exception:
        current_app.logger.exception("Completeness refresh failed for case %s", case_id)
    try:
        message = listedness_message(run_automatic_listedness(case_id, actor_user_id=session.get("user_id")))
        if message:
            flash(*message)
    except Exception:
        current_app.logger.exception("Automatic listedness failed for case %s", case_id)
    from app.cases.coding_routes import auto_code_after_save

    for message in auto_code_after_save(case_id, session.get("user_id")):
        flash(*message)


def _record_from_submission(record, data):
    """Show what was typed (not what is stored) when a save is refused."""
    shown = dict(record)
    shown["case"] = {**record["case"], **{k: v for k, v in data["case"].items()}, "null_flavours": data.get("case_nf") or {}}
    shown["parent"] = {**(record.get("parent") or {}), **data.get("parent", {}), "null_flavours": data.get("parent_nf") or {}}
    for prefix, id_col in icsr.ID_COLUMNS.items():
        rows = []
        for row in data.get(prefix, []):
            item = {k: v for k, v in row.items() if not k.startswith("__")}
            item["__key"] = row["__key"]
            if row.get("__id"):
                item[id_col] = int(row["__id"])
            rows.append(item)
        if prefix == "parent_history":
            continue
        if prefix == "parent_past_drugs":
            continue
        shown[prefix] = rows
    shown["history"] = [dict(r, for_parent=False) for r in shown.get("history", [])] + \
        [dict({k: v for k, v in r.items() if not k.startswith("__")}, __key=r["__key"], for_parent=True,
              **({"history_id": int(r["__id"])} if r.get("__id") else {})) for r in data.get("parent_history", [])]
    shown["past_drugs"] = [dict(r, for_parent=False) for r in shown.get("past_drugs", [])] + \
        [dict({k: v for k, v in r.items() if not k.startswith("__")}, __key=r["__key"], for_parent=True,
              **({"past_drug_id": int(r["__id"])} if r.get("__id") else {})) for r in data.get("parent_past_drugs", [])]
    return shown
