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
               workflow_status, approval_stage
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

    if (
        workflow_status == "Submitted"
        and case.get("workflow_status") != "Submitted"
        and case.get("approval_stage") != "Approved"
    ):
        flash(
            "This case needs both sign-offs (review and approval) before it "
            "is marked as submitted. See the Sign-off box on the case.",
            "error",
        )
        return redirect(url_for("cases.case_detail", case_id=case_id) + "#approval")

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
               regulatory_submitted_date, approval_stage
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
    if case.get("approval_stage") != "Approved":
        flash(
            "This case needs both sign-offs (review and approval) before it "
            "is marked as submitted. See the Sign-off box on the case.",
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
    from app.services.follow_up_replies import (
        inbox_configured,
        replies_for_cases,
        unmatched_replies,
    )

    replies = replies_for_cases([group["case_id"] for group in cases])
    attachment_status = _reply_attachment_status(replies)
    for group in cases:
        group["history"] = history.get(group["case_id"])
        group["last_failure"] = failures.get(group["case_id"])
        group["replies"] = replies.get(group["case_id"], [])
        for reply in group["replies"]:
            reply["files"] = [
                attachment_status[a] for a in (reply.get("attachment_ids") or [])
                if a in attachment_status
            ]
        group["reply_waiting"] = any(r["status"] == "New" for r in group["replies"])
    unmatched = unmatched_replies()
    return render_template(
        "cases/follow_up_tasks.html",
        tasks=tasks,
        cases=cases,
        unmatched=unmatched,
        link_choices=_link_choices() if unmatched else [],
        inbox={
            "ready": inbox_configured(),
            "every": current_app.config.get("FOLLOW_UP_REPLY_CHECK_MINUTES", 15),
            "last": current_app.config.get("FOLLOW_UP_LAST_REPLY_CHECK"),
        },
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


def _reply_attachment_status(replies):
    ids = [a for rows in replies.values() for r in rows for a in (r.get("attachment_ids") or [])]
    if not ids:
        return {}
    try:
        rows = query_all(
            """
            SELECT attachment_id, record_id AS case_id, original_filename,
                   processing_status, processing_note
            FROM pv.record_attachments
            WHERE attachment_id = ANY(%s)
            """,
            (ids,),
        )
    except Exception:
        current_app.logger.warning("Could not read reply attachments", exc_info=True)
        return {}
    return {row["attachment_id"]: dict(row) for row in rows}


def _link_choices():
    try:
        return query_all(
            """
            SELECT case_id, case_number, reporter_name, reporter_email
            FROM pv.safety_cases
            WHERE workflow_status NOT IN ('Closed')
            ORDER BY received_date DESC NULLS LAST, case_id DESC
            LIMIT 300
            """
        )
    except Exception:
        return []


@bp.post("/follow-up-replies/check")
@login_required
def check_follow_up_replies():
    from app.services.follow_up_automation import run_reply_check
    from app.services.follow_up_replies import inbox_configured

    back = url_for("case_review.follow_up_tasks")
    if not inbox_configured():
        flash("Reading replies is not set up: email sending must be set up with an App password first.", "error")
        return redirect(back)
    summary = run_reply_check(current_app._get_current_object())
    if summary.get("error"):
        flash(f"The inbox could not be checked: {summary['error']}", "error")
    elif summary.get("skipped"):
        flash(f"Inbox checked. {summary['skipped']}", "info")
    elif summary["matched"] or summary["unmatched"]:
        parts = []
        if summary["matched"]:
            parts.append(f"{summary['matched']} new reply(ies) added to their cases")
        if summary["unmatched"]:
            parts.append(f"{summary['unmatched']} need linking to a case")
        flash("Inbox checked: " + "; ".join(parts) + ".", "success")
    else:
        flash("Inbox checked. No new replies.", "success")
    return redirect(back)


@bp.post("/follow-up-replies/<int:reply_id>/handled")
@login_required
def mark_reply_handled(reply_id):
    from app.services.follow_up_replies import STATUS_HANDLED, set_reply_status

    case_id = set_reply_status(reply_id, STATUS_HANDLED, session["user_id"])
    if case_id:
        write_audit_log(
            record_type="case", record_id=case_id, action="Follow-up reply handled",
            details="Reporter's reply reviewed. Reminders resume if information is still missing.",
            actor_user_id=session["user_id"],
        )
        flash("Reply marked as handled. If anything is still missing, reminders resume on schedule.", "success")
    return redirect(url_for("case_review.follow_up_tasks") + (f"#case-{case_id}" if case_id else ""))


@bp.post("/follow-up-replies/<int:reply_id>/link")
@login_required
def link_follow_up_reply(reply_id):
    from app.services.follow_up_replies import link_reply

    back = url_for("case_review.follow_up_tasks")
    case_id = request.form.get("case_id", type=int)
    if not case_id or not query_one("SELECT 1 AS x FROM pv.safety_cases WHERE case_id = %s", (case_id,)):
        flash("Choose the case this reply belongs to.", "error")
        return redirect(back + "#unmatched-replies")
    if link_reply(current_app._get_current_object(), reply_id, case_id, session["user_id"]):
        write_audit_log(
            record_type="case", record_id=case_id, action="Follow-up reply linked",
            details=f"Email reply {reply_id} linked to the case by hand.",
            actor_user_id=session["user_id"],
        )
        flash("Reply linked to the case. The AI is reading it for suggested updates.", "success")
        return redirect(back + f"#case-{case_id}")
    flash("That reply was already handled.", "info")
    return redirect(back)


@bp.post("/follow-up-replies/<int:reply_id>/ignore")
@login_required
def ignore_follow_up_reply(reply_id):
    from app.services.follow_up_replies import STATUS_IGNORED, set_reply_status

    set_reply_status(reply_id, STATUS_IGNORED, session["user_id"])
    flash("Email ignored. It stays in the mailbox but is no longer listed here.", "success")
    return redirect(url_for("case_review.follow_up_tasks"))


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
            case, product, items, due, prepared_by=session.get("full_name"),
            phone=current_app.config.get("PV_CONTACT_PHONE"),
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


# --------------------------------------------------------------------------
# Sign-off: PV Officer -> QPPV / Deputy QPPV review -> Group Head approval
# --------------------------------------------------------------------------

@bp.post("/<int:case_id>/sign-off")
@login_required
def case_sign_off(case_id):
    from app.services.case_approval import ApprovalError, notify_officer, take_step
    from app.services.case_workflow import is_designated_qppv
    from app.security import safe_next_path

    back = safe_next_path(request.form.get("next")) or (
        url_for("cases.case_detail", case_id=case_id) + "#approval"
    )
    action = request.form.get("action", "")
    comment = request.form.get("comment", "")
    try:
        stage = take_step(
            case_id,
            action,
            session["user_id"],
            session.get("role"),
            is_designated_qppv(session["user_id"]),
            comment,
        )
    except ApprovalError as error:
        flash(str(error), "error")
        return redirect(back)

    from app.services.approval_settings import load_settings

    titles = load_settings()
    messages = {
        "Pending review": f"Sent for {titles['review_title']}.",
        "Pending approval": f"Reviewed. Sent for {titles['approval_title']}.",
        "Approved": "Approved. The case can now be submitted to the regulator.",
        "Returned": "Returned to the PV officer with your comment.",
    }
    flash(messages.get(stage, "Saved."), "success")
    if stage in ("Returned", "Approved"):
        notify_officer(
            current_app._get_current_object(), case_id, stage, comment.strip(),
            session.get("full_name") or "A reviewer",
        )
    return redirect(back)


@bp.get("/approvals")
@login_required
def approvals():
    from app.services.case_approval import (
        APPROVED,
        PENDING_APPROVAL,
        PENDING_REVIEW,
        RETURNED,
        can_approve,
        can_review,
        cases_at_stage,
        recently_decided,
    )
    from app.services.case_workflow import is_designated_qppv

    role = session.get("role")
    reviewer = can_review(role, is_designated_qppv(session["user_id"]))
    approver = can_approve(role)
    try:
        data = {
            "pending_review": cases_at_stage(PENDING_REVIEW),
            "reviewed": recently_decided("Reviewed"),
            "pending_approval": cases_at_stage(PENDING_APPROVAL),
            "approved": recently_decided("Approved"),
            "returned": cases_at_stage(RETURNED),
        }
    except Exception:
        current_app.logger.exception("Could not load approvals")
        try:
            from app.db import get_db

            get_db().rollback()
        except Exception:
            pass
        flash(
            "Approvals could not be loaded. Run the database update "
            "(python scripts\\migrate.py).",
            "error",
        )
        data = {k: [] for k in ("pending_review", "reviewed", "pending_approval", "approved", "returned")}
    default_tab = "approval" if approver else "review"
    return render_template(
        "cases/approvals.html",
        reviewer=reviewer,
        approver=approver,
        tab=request.args.get("tab") or default_tab,
        **data,
    )

