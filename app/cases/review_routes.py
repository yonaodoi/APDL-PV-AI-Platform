from datetime import date, datetime
import secrets

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask import current_app

from app.audit import write_audit_log
from app.db import query_all, query_one, transaction
from app.security import login_required
from app.services.case_completeness import refresh_case_completeness
from app.services.follow_up_automation import (
    automation_active,
    email_configured,
    load_case_request,
    previously_sent,
    reminder_status,
    reply_due,
    request_items,
    send_case_request,
)
from app.services.follow_up_request import (
    build_follow_up_request_docx,
    group_tasks_by_case,
    request_filename,
)
from app.services.gmail_oauth import (
    build_authorization_url,
    complete_authorization,
)


bp = Blueprint("case_review", __name__, url_prefix="/cases")


@bp.get("/email/gmail/connect")
@login_required
def connect_gmail():
    state = secrets.token_urlsafe(32)
    session["gmail_oauth_state"] = state
    try:
        authorization_url = build_authorization_url(
            current_app,
            state,
        )
    except RuntimeError as exc:
        flash(f"Gmail authorization failed: {exc}", "error")
        return redirect(url_for("case_review.follow_up_tasks"))
    return redirect(authorization_url)


@bp.get("/email/gmail/callback")
@login_required
def gmail_callback():
    state = session.pop("gmail_oauth_state", None)
    if not state or state != request.args.get("state"):
        flash("Gmail authorization could not be verified.", "error")
        return redirect(url_for("case_review.follow_up_tasks"))
    try:
        complete_authorization(
            current_app,
            request.url,
            state,
        )
    except Exception as exc:
        current_app.logger.exception("Gmail OAuth authorization failed")
        flash(f"Gmail authorization failed: {exc}", "error")
        return redirect(url_for("case_review.follow_up_tasks"))
    flash("Gmail is connected and ready to send follow-up forms.", "success")
    return redirect(url_for("case_review.follow_up_tasks"))


VALID_STATUSES = {
    "New",
    "Triage",
    "Follow-up requested",
    "Medical review",
    "Ready for submission",
    "Submitted",
    "Closed",
}


@bp.post("/<int:case_id>/review")
@login_required
def review_case(case_id):
    case = query_one(
        """
        SELECT case_id, case_number, received_date, regulatory_submitted_date,
               workflow_status
        FROM pv.safety_cases
        WHERE case_id = %s
        """,
        (case_id,),
    )

    if case is None:
        abort(404)

    workflow_status = request.form.get("workflow_status", "")
    causality_assessment = request.form.get(
        "causality_assessment",
        "",
    ).strip()
    review_notes = request.form.get("review_notes", "").strip()
    follow_up_required = request.form.get("follow_up_required") == "on"
    follow_up_due_date = request.form.get("follow_up_due_date", "").strip()

    submitted_date_text = request.form.get(
        "regulatory_submitted_date", ""
    ).strip()

    if workflow_status not in VALID_STATUSES:
        abort(400)

    try:
        submitted_date = (
            datetime.strptime(submitted_date_text, "%Y-%m-%d").date()
            if submitted_date_text
            else case["regulatory_submitted_date"]
        )
    except ValueError:
        flash("Regulatory submission date is not valid.", "error")
        return redirect(url_for("cases.case_detail", case_id=case_id))

    if workflow_status == "Submitted" and submitted_date is None:
        submitted_date = date.today()

    if submitted_date and submitted_date > date.today():
        flash("Regulatory submission date cannot be in the future.", "error")
        return redirect(url_for("cases.case_detail", case_id=case_id))

    if submitted_date and submitted_date < case["received_date"]:
        flash(
            "Regulatory submission date cannot be before the case was received.",
            "error",
        )
        return redirect(url_for("cases.case_detail", case_id=case_id))

    try:
        parsed_follow_up_date = (
            datetime.strptime(follow_up_due_date, "%Y-%m-%d").date()
            if follow_up_due_date
            else None
        )
    except ValueError:
        flash("Follow-up due date is not valid.", "error")
        return redirect(url_for("cases.case_detail", case_id=case_id))

    from app.services.case_workflow import (
        can_override,
        is_designated_qppv,
        load_workflow_inputs,
        needs_override,
        readiness_blockers,
    )

    checks, assessment, _ = load_workflow_inputs(case_id)
    blockers = readiness_blockers(checks, assessment, causality_assessment)
    override_reason = request.form.get("override_reason", "").strip()
    override_note = ""
    if (
        needs_override(workflow_status, blockers)
        and workflow_status != case.get("workflow_status")
    ):
        allowed = can_override(
            session.get("role"), is_designated_qppv(session.get("user_id"))
        )
        if not (allowed and request.form.get("override") == "on" and override_reason):
            flash(
                f"The case is not ready for \"{workflow_status}\": "
                + " ".join(blockers)
                + (
                    " To proceed anyway, tick Override and give a reason."
                    if allowed
                    else " Only a QPPV, Deputy QPPV or Medical Reviewer can override."
                ),
                "error",
            )
            return redirect(url_for("cases.case_detail", case_id=case_id) + "#workflow")
        override_note = (
            f" Readiness check overridden. Reason: {override_reason}. "
            f"Outstanding: {' '.join(blockers)}"
        )

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_cases
            SET workflow_status = %s,
                causality_assessment = %s,
                follow_up_required = %s,
                follow_up_due_date = %s,
                regulatory_submitted_date = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE case_id = %s
            """,
            (
                workflow_status,
                causality_assessment or None,
                follow_up_required,
                parsed_follow_up_date,
                submitted_date,
                case_id,
            ),
        )

        details = (
            f"Status changed to {workflow_status}."
            + (
                f" Regulatory submission date: {submitted_date:%d %b %Y}."
                if submitted_date
                and submitted_date != case["regulatory_submitted_date"]
                else ""
            )
            + (f" Review note: {review_notes}" if review_notes else "")
            + override_note
        )

        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (
                case_id,
                action,
                details,
                performed_by
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                "Case reviewed",
                details,
                session["user_id"],
            ),
        )

    updated_case = query_one(
        "SELECT * FROM pv.safety_cases WHERE case_id = %s",
        (case_id,),
    )
    products = query_all(
        "SELECT * FROM pv.case_products WHERE case_id = %s",
        (case_id,),
    )
    refresh_case_completeness(updated_case, products)

    if override_note:
        write_audit_log(
            record_type="case",
            record_id=case_id,
            action="Readiness check overridden",
            details=(
                f"Status set to {workflow_status} with outstanding items. "
                f"Reason: {override_reason}. Outstanding: {' '.join(blockers)}"
            ),
            actor_user_id=session["user_id"],
        )

    flash(f"Case {case['case_number']} was updated.", "success")
    return redirect(
        url_for("cases.case_detail", case_id=case_id, workflow_saved="review")
        + "#workflow"
    )


@bp.post("/<int:case_id>/mark-submitted")
@login_required
def mark_submitted(case_id):
    case = query_one(
        """
        SELECT case_id, case_number, received_date, workflow_status,
               regulatory_submitted_date
        FROM pv.safety_cases
        WHERE case_id = %s
        """,
        (case_id,),
    )
    if case is None:
        abort(404)
    back = url_for("cases.case_detail", case_id=case_id) + "#workflow"

    try:
        submitted_on = datetime.strptime(
            request.form.get("submitted_on", "").strip(), "%Y-%m-%d"
        ).date()
    except ValueError:
        flash("Enter the date the report was submitted.", "error")
        return redirect(back)
    if submitted_on > date.today():
        flash("The submission date cannot be in the future.", "error")
        return redirect(back)
    if submitted_on < case["received_date"]:
        flash("The submission date cannot be before the case was received.", "error")
        return redirect(back)
    if case["workflow_status"] != "Ready for submission":
        flash(
            "Only a case that is Ready for submission can be marked as submitted.",
            "error",
        )
        return redirect(back)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_cases
            SET workflow_status = 'Submitted',
                regulatory_submitted_date = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE case_id = %s
            """,
            (submitted_on, case_id),
        )
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                "Case reviewed",
                f"Status changed to Submitted. Submitted to the regulator on "
                f"{submitted_on:%d %b %Y}.",
                session["user_id"],
            ),
        )
    write_audit_log(
        record_type="case",
        record_id=case_id,
        action="Case submitted to regulator",
        details=f"{case['case_number']} submitted on {submitted_on:%d %b %Y}.",
        actor_user_id=session["user_id"],
    )
    flash(f"{case['case_number']} marked as submitted on {submitted_on:%d %b %Y}.", "success")
    return redirect(
        url_for("cases.case_detail", case_id=case_id, workflow_saved="submitted")
        + "#workflow"
    )


@bp.post("/<int:case_id>/accept-suggested-status")
@login_required
def accept_suggested_status(case_id):
    from app.services.case_workflow import load_workflow_inputs, suggest_status

    case = query_one(
        "SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,)
    )
    if case is None:
        abort(404)
    checks, assessment, follow_up = load_workflow_inputs(case_id)
    suggestion = suggest_status(
        case,
        checks,
        assessment,
        follow_up.get("open_tasks") or 0,
        follow_up.get("follow_up_sent"),
    )
    if not suggestion or suggestion["same"]:
        flash("The case status is already up to date.", "info")
        return redirect(url_for("cases.case_detail", case_id=case_id) + "#workflow")

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_cases
            SET workflow_status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE case_id = %s
            """,
            (suggestion["status"], case_id),
        )
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                "Case reviewed",
                f"Status changed to {suggestion['status']} (suggested): "
                + " ".join(suggestion["reasons"]),
                session["user_id"],
            ),
        )
    flash(f"Status changed to {suggestion['status']}.", "success")
    return redirect(
        url_for("cases.case_detail", case_id=case_id, workflow_saved="status")
        + "#workflow"
    )


@bp.get("/follow-up-tasks")
@login_required
def follow_up_tasks():
    from app.services.case_follow_up import get_open_follow_up_tasks
    from app.services.case_follow_up_reminders import ensure_overdue_reminders

    ensure_overdue_reminders()
    tasks = get_open_follow_up_tasks()
    cases = group_tasks_by_case(tasks, _last_requests([t["case_id"] for t in tasks]))
    history = _reminder_history()
    failures = _last_failures([group["case_id"] for group in cases])
    for group in cases:
        group["history"] = history.get(group["case_id"])
        group["last_failure"] = failures.get(group["case_id"])
    return render_template(
        "cases/follow_up_tasks.html",
        tasks=tasks,
        cases=cases,
        current_date=date.today(),
        automation={
            "switched_on": current_app.config.get("FOLLOW_UP_AUTO_SEND"),
            "email_ready": email_configured(),
            "active": automation_active(),
            "grace_hours": current_app.config.get("FOLLOW_UP_GRACE_HOURS", 24),
            "reminder_days": current_app.config.get("FOLLOW_UP_REMINDER_DAYS", 7),
            "max_reminders": current_app.config.get("FOLLOW_UP_MAX_REMINDERS", 2),
            "last_run": current_app.config.get("FOLLOW_UP_LAST_RUN"),
        },
    )


def _reminder_history():
    try:
        return reminder_status(
            reminder_days=current_app.config.get("FOLLOW_UP_REMINDER_DAYS", 7),
            max_reminders=current_app.config.get("FOLLOW_UP_MAX_REMINDERS", 2),
        )
    except Exception:
        current_app.logger.warning("Could not read follow-up history", exc_info=True)
        try:
            from app.db import get_db

            get_db().rollback()
        except Exception:
            pass
        return {}


def _last_requests(case_ids):
    """Latest successful follow-up email per case (empty if unreadable)."""
    if not case_ids:
        return {}
    try:
        rows = query_all(
            """
            SELECT case_id, MAX(sent_at) AS sent_at
            FROM pv.case_follow_up_email_deliveries
            WHERE status = 'Sent'
              AND case_id = ANY(%s)
            GROUP BY case_id
            """,
            (list(set(case_ids)),),
        )
    except Exception:
        current_app.logger.warning("Could not read follow-up email deliveries", exc_info=True)
        try:
            from app.db import get_db

            get_db().rollback()
        except Exception:
            pass
        return {}
    return {row["case_id"]: row["sent_at"] for row in rows}


def _last_failures(case_ids):
    """The most recent failed send per case, if nothing was sent after it."""
    if not case_ids:
        return {}
    try:
        rows = query_all(
            """
            SELECT DISTINCT ON (failed.case_id)
                   failed.case_id, failed.sent_at, failed.error_message
            FROM pv.case_follow_up_email_deliveries AS failed
            WHERE failed.status = 'Failed'
              AND failed.case_id = ANY(%s)
              AND NOT EXISTS (
                  SELECT 1 FROM pv.case_follow_up_email_deliveries AS later
                  WHERE later.case_id = failed.case_id
                    AND later.status = 'Sent'
                    AND later.sent_at > failed.sent_at
              )
            ORDER BY failed.case_id, failed.sent_at DESC
            """,
            (list(set(case_ids)),),
        )
    except Exception:
        current_app.logger.warning("Could not read failed follow-up emails", exc_info=True)
        try:
            from app.db import get_db

            get_db().rollback()
        except Exception:
            pass
        return {}
    return {row["case_id"]: row for row in rows}


@bp.get("/follow-up-reminders")
@login_required
def follow_up_reminders():
    from app.services.case_follow_up_reminders import (
        ensure_overdue_reminders,
        get_open_reminders,
    )

    ensure_overdue_reminders()
    return render_template(
        "cases/follow_up_reminders.html",
        reminders=get_open_reminders(),
    )


@bp.post("/follow-up-reminders/<int:reminder_id>/acknowledge")
@login_required
def acknowledge_follow_up_reminder(reminder_id):
    from app.services.case_follow_up_reminders import acknowledge_reminder

    if acknowledge_reminder(reminder_id, session["user_id"]) is None:
        abort(404)
    flash("Reminder acknowledged.", "success")
    return redirect(url_for("case_review.follow_up_reminders"))


@bp.post("/follow-up-tasks/<int:task_id>/complete")
@login_required
def complete_follow_up_task(task_id):
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.case_follow_up_tasks
            SET status = 'Completed',
                completed_at = CURRENT_TIMESTAMP,
                completed_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE task_id = %s
              AND status IN ('Open', 'In progress')
            RETURNING case_id, task_title
            """,
            (session["user_id"], task_id),
        )
        task = cursor.fetchone()
        if task is not None:
            cursor.execute(
                """
                INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    task["case_id"],
                    "Follow-up item marked not available",
                    f"{task['task_title'].replace('Follow up: ', '')}: the reporter "
                    "cannot provide this; it will not be requested again.",
                    session["user_id"],
                ),
            )

    if task is None:
        abort(404)

    from app.services.case_follow_up import sync_follow_up_flags
    from app.services.case_workflow import auto_move_status

    with transaction() as cursor:
        remaining = sync_follow_up_flags(
            cursor, task["case_id"], tasks_just_completed=1
        )
        moved = (
            not remaining
            and auto_move_status(
                cursor,
                task["case_id"],
                ("Follow-up requested",),
                "Medical review",
                "all follow-up tasks are completed.",
                session["user_id"],
            )
        )

    flash("Marked as not available. The reporter will not be asked for it again.", "success")
    if moved:
        flash(
            "All follow-up tasks for this case are done, so it moved to "
            "Medical review.",
            "info",
        )
    return redirect(url_for("case_review.follow_up_tasks"))


@bp.post("/<int:case_id>/duplicate-review")
@login_required
def review_duplicate_candidate(case_id):
    from app.services.case_duplicates import (
        find_possible_duplicates,
        save_duplicate_review,
    )

    case = query_one(
        "SELECT * FROM pv.safety_cases WHERE case_id = %s",
        (case_id,),
    )
    if case is None:
        abort(404)
    try:
        candidate_case_id = int(request.form.get("candidate_case_id", ""))
    except ValueError:
        abort(400)

    products = query_all(
        "SELECT * FROM pv.case_products WHERE case_id = %s",
        (case_id,),
    )
    candidates = find_possible_duplicates(case, products)
    if not any(
        candidate["case_id"] == candidate_case_id
        for candidate in candidates
    ):
        abort(400)

    status = request.form.get("review_status", "")
    try:
        save_duplicate_review(
            case_id,
            candidate_case_id,
            status,
            session["user_id"],
        )
    except ValueError:
        abort(400)

    flash(
        f"Duplicate screening recorded as {status.lower()}. "
        "No cases were merged or changed.",
        "success",
    )
    return redirect(url_for("cases.case_detail", case_id=case_id))


@bp.get("/<int:case_id>/follow-up-request.docx")
@login_required
def download_follow_up_request(case_id):
    loaded = load_case_request(case_id)
    if loaded is None:
        abort(404)
    case, product, checks, tasks = loaded
    items = request_items(case, product, checks)
    if not items:
        flash(
            "This case has nothing to ask the reporter. Remaining tasks are "
            "for the PV team.",
            "info",
        )
        return redirect(url_for("case_review.follow_up_tasks"))
    due = reply_due(tasks, case, days=current_app.config.get("FOLLOW_UP_REMINDER_DAYS", 7))
    return send_file(
        build_follow_up_request_docx(
            case, product, items, due, prepared_by=session.get("full_name")
        ),
        as_attachment=True,
        download_name=request_filename(case),
        mimetype=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )


@bp.post("/<int:case_id>/follow-up-request/send")
@login_required
def send_follow_up_request(case_id):
    back = url_for("case_review.follow_up_tasks") + f"#case-{case_id}"
    # A second email to the same reporter is a reminder: it says so in the
    # subject and text, and counts as the next follow-up.
    kind = "Reminder" if previously_sent(case_id) else "Request"
    result = send_case_request(
        case_id,
        session["user_id"],
        kind=kind,
        prepared_by=session.get("full_name"),
    )
    if not result["sent"]:
        flash(result["message"], "error" if result["items"] else "info")
        return redirect(back)
    flash(result["message"], "success")
    if result["moved"]:
        flash("The case status moved to Follow-up requested.", "info")
    return redirect(back)
