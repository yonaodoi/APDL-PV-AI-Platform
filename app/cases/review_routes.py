from datetime import date, datetime
from email.utils import parseaddr
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
from app.services.follow_up_request import (
    build_follow_up_request_docx,
    group_tasks_by_case,
    build_request_items,
    email_body,
    email_subject,
    request_filename,
)
from app.services.follow_up_email import (
    FollowUpEmailError,
    send_follow_up_email,
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
    return render_template(
        "cases/follow_up_tasks.html",
        tasks=tasks,
        cases=group_tasks_by_case(tasks, _last_requests([t["case_id"] for t in tasks])),
        current_date=date.today(),
    )


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
            RETURNING case_id
            """,
            (session["user_id"], task_id),
        )
        task = cursor.fetchone()

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

    flash("Follow-up task marked as completed.", "success")
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


def _case_for_request(case_id):
    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
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
    ) or {}
    checks = query_all(
        """
        SELECT check_code AS code, check_label AS label, status, message
        FROM pv.case_completeness_checks
        WHERE case_id = %s
          AND status = 'Review'
        ORDER BY check_id
        """,
        (case_id,),
    )
    tasks = query_all(
        """
        SELECT task_id, check_code, due_date
        FROM pv.case_follow_up_tasks
        WHERE case_id = %s
          AND status IN ('Open', 'In progress')
        ORDER BY due_date
        """,
        (case_id,),
    )
    items = build_request_items(case, product, checks)
    due_date = min((t["due_date"] for t in tasks), default=None) or case.get(
        "follow_up_due_date"
    )
    return case, product, items, tasks, due_date


def _request_document(case, product, items, due_date):
    return build_follow_up_request_docx(
        case,
        product,
        items,
        due_date,
        prepared_by=session.get("full_name"),
    )


@bp.get("/<int:case_id>/follow-up-request.docx")
@login_required
def download_follow_up_request(case_id):
    case, product, items, _, due_date = _case_for_request(case_id)
    if not items:
        flash(
            "This case has nothing to ask the reporter. Remaining tasks are "
            "for the PV team.",
            "info",
        )
        return redirect(url_for("case_review.follow_up_tasks"))
    return send_file(
        _request_document(case, product, items, due_date),
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
    case, product, items, tasks, due_date = _case_for_request(case_id)
    if not items:
        flash("This case has nothing to ask the reporter.", "info")
        return redirect(back)

    recipient = (case.get("reporter_email") or "").strip()
    if not recipient or parseaddr(recipient)[1] != recipient:
        flash(
            "This case has no valid reporter email address. Add it on the "
            "case, or download the request and send it another way.",
            "error",
        )
        return redirect(back)

    included = {item["code"] for item in items}
    request_tasks = [t for t in tasks if t["check_code"] in included] or tasks
    document = _request_document(case, product, items, due_date)
    try:
        send_follow_up_email(
            current_app,
            recipient,
            email_subject(case),
            email_body(case, product, items, due_date),
            document,
            request_filename(case),
        )
    except FollowUpEmailError as exc:
        with transaction() as cursor:
            for task in request_tasks:
                cursor.execute(
                    """
                    INSERT INTO pv.case_follow_up_email_deliveries (
                        task_id, case_id, recipient_email, status,
                        error_message, sent_by
                    )
                    VALUES (%s, %s, %s, 'Failed', %s, %s)
                    """,
                    (task["task_id"], case_id, recipient, str(exc), session["user_id"]),
                )
        flash(str(exc), "error")
        return redirect(back)

    with transaction() as cursor:
        for task in request_tasks:
            cursor.execute(
                """
                INSERT INTO pv.case_follow_up_email_deliveries (
                    task_id, case_id, recipient_email, status, sent_by
                )
                VALUES (%s, %s, %s, 'Sent', %s)
                """,
                (task["task_id"], case_id, recipient, session["user_id"]),
            )
            cursor.execute(
                """
                UPDATE pv.case_follow_up_tasks
                SET status = 'In progress', updated_at = CURRENT_TIMESTAMP
                WHERE task_id = %s AND status = 'Open'
                """,
                (task["task_id"],),
            )
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                "Follow-up request emailed",
                f"Follow-up request with {len(items)} question(s) sent to "
                f"{recipient}: " + "; ".join(item["label"] for item in items),
                session["user_id"],
            ),
        )
        from app.services.case_workflow import auto_move_status

        moved = auto_move_status(
            cursor,
            case_id,
            ("New", "Triage", "Medical review"),
            "Follow-up requested",
            f"follow-up request sent to {recipient}.",
            session["user_id"],
        )

    flash(
        f"Follow-up request for {case['case_number']} sent to {recipient} "
        f"({len(items)} question(s)).",
        "success",
    )
    if moved:
        flash("The case status moved to Follow-up requested.", "info")
    return redirect(back)
