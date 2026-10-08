"""Automatic follow-up: send requests and reminders without anyone clicking.

Rules
* A case's request goes out once its missing-information tasks are older
  than FOLLOW_UP_GRACE_HOURS (so data entry can finish first) and the case
  has a reporter email address.
* Contradictions between recorded values (dates, age, outcome vs
  seriousness) are often our own typing errors, so they are held for the PV
  team and never sent automatically.
* If the reply date passes, a reminder goes out every FOLLOW_UP_REMINDER_DAYS
  days, at most FOLLOW_UP_MAX_REMINDERS times. After that the case is
  flagged on the dashboard.
* Nothing runs until email sending is set up.
"""

import os
from datetime import date, datetime, timedelta, timezone
from email.utils import parseaddr
from threading import Thread
from time import sleep

from flask import current_app

from app.db import get_db, query_all, query_one, transaction
from app.services.follow_up_request import (
    CHECK_FIRST_CODES,
    INTERNAL_CODES,
    build_follow_up_request_docx,
    build_request_items,
    email_body,
    email_subject,
    request_filename,
)

# Checks that compare recorded values; held for the PV team.
HOLD_CODES = CHECK_FIRST_CODES
NOT_SENT_AUTOMATICALLY = INTERNAL_CODES | HOLD_CODES
LOCK_KEY = 4510045


def email_configured(app=None):
    app = app or current_app
    token = app.config.get("GMAIL_OAUTH_TOKEN_PATH")
    return bool(app.config.get("SMTP_PASSWORD")) or bool(token and token.exists())


def automation_active(app=None):
    app = app or current_app
    return bool(app.config.get("FOLLOW_UP_AUTO_SEND")) and email_configured(app)


def valid_email(value):
    value = (value or "").strip()
    return bool(value) and "@" in value and parseaddr(value)[1] == value


# --------------------------------------------------------------------------
# Loading and sending one case's request
# --------------------------------------------------------------------------

def load_case_request(case_id):
    """(case, product, checks, open_tasks) for one case, or None."""
    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    if case is None:
        return None
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
    return case, product, checks, tasks


def request_items(case, product, checks, automatic=False):
    if automatic:
        checks = [c for c in checks if c.get("code") not in HOLD_CODES]
    return build_request_items(case, product, checks)


def reply_due(tasks, case, today=None, days=7):
    today = today or date.today()
    earliest = min((t["due_date"] for t in tasks), default=None) or case.get("follow_up_due_date")
    floor = today + timedelta(days=days)
    return max(earliest, floor) if earliest else floor


def send_case_request(case_id, actor_user_id, *, automatic=False, kind="Request",
                      prepared_by=None, app=None, today=None):
    """Email one case's follow-up request (or a reminder).

    Returns a dict: sent (bool), message, items, moved (status changed).
    Never raises for expected problems; they come back as a message.
    """
    from app.services.case_follow_up import sync_follow_up_flags
    from app.services.case_workflow import auto_move_status
    from app.services.follow_up_email import FollowUpEmailError, send_follow_up_email

    app = app or current_app._get_current_object()
    today = today or date.today()
    loaded = load_case_request(case_id)
    if loaded is None:
        return {"sent": False, "message": "Case not found.", "items": [], "moved": False}
    case, product, checks, tasks = loaded
    items = request_items(case, product, checks, automatic=automatic)
    if not items:
        return {"sent": False, "message": "This case has nothing to ask the reporter.",
                "items": [], "moved": False}

    recipient = (case.get("reporter_email") or "").strip()
    if not valid_email(recipient):
        return {"sent": False, "items": items, "moved": False,
                "message": "This case has no valid reporter email address. Add it "
                           "on the case, or download the request and send it another way."}

    days = app.config.get("FOLLOW_UP_REMINDER_DAYS", 7)
    due = reply_due(tasks, case, today, days)
    included = {item["code"] for item in items}
    request_tasks = [t for t in tasks if t["check_code"] in included]
    reminder_of = None
    if kind == "Reminder":
        first = query_one(
            """
            SELECT MIN(sent_at) AS sent_at
            FROM pv.case_follow_up_email_deliveries
            WHERE case_id = %s AND status = 'Sent' AND kind = 'Request'
            """,
            (case_id,),
        )
        reminder_of = (first or {}).get("sent_at")

    sent_at = datetime.now(timezone.utc)
    phone = app.config.get("PV_CONTACT_PHONE")
    document = build_follow_up_request_docx(
        case, product, items, due, prepared_by=prepared_by, sent_at=sent_at, phone=phone,
    )
    subject = email_subject(case)
    if kind == "Reminder":
        subject = "Reminder: " + subject
    try:
        send_follow_up_email(
            app, recipient, subject,
            email_body(case, product, items, due, reminder_of=reminder_of,
                       sent_at=sent_at, phone=phone),
            document, request_filename(case),
        )
    except FollowUpEmailError as exc:
        with transaction() as cursor:
            for task in request_tasks:
                cursor.execute(
                    """
                    INSERT INTO pv.case_follow_up_email_deliveries (
                        task_id, case_id, recipient_email, status,
                        error_message, sent_by, kind, automatic
                    )
                    VALUES (%s, %s, %s, 'Failed', %s, %s, %s, %s)
                    """,
                    (task["task_id"], case_id, recipient, str(exc),
                     None if automatic else actor_user_id, kind, automatic),
                )
        return {"sent": False, "message": str(exc), "items": items, "moved": False}

    who = "automatically" if automatic else ""
    with transaction() as cursor:
        for task in request_tasks:
            cursor.execute(
                """
                INSERT INTO pv.case_follow_up_email_deliveries (
                    task_id, case_id, recipient_email, status, sent_by,
                    kind, automatic
                )
                VALUES (%s, %s, %s, 'Sent', %s, %s, %s)
                """,
                (task["task_id"], case_id, recipient,
                 None if automatic else actor_user_id, kind, automatic),
            )
            cursor.execute(
                """
                UPDATE pv.case_follow_up_tasks
                SET status = 'In progress',
                    due_date = GREATEST(due_date, %s),
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_id = %s
                """,
                (due, task["task_id"]),
            )
        sync_follow_up_flags(cursor, case_id)
        action = (
            "Follow-up reminder emailed" if kind == "Reminder" else "Follow-up request emailed"
        )
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                action + (" automatically" if automatic else ""),
                f"{kind} with {len(items)} question(s) sent {who} to {recipient}, "
                f"reply requested by {due:%d %b %Y}: "
                + "; ".join(item["label"] for item in items),
                actor_user_id,
            ),
        )
        moved = auto_move_status(
            cursor, case_id, ("New", "Triage", "Medical review"),
            "Follow-up requested", f"follow-up request sent to {recipient}.",
            actor_user_id,
        )
    return {
        "sent": True,
        "message": f"{kind} for {case['case_number']} sent to {recipient} "
                   f"({len(items)} question(s)).",
        "items": items,
        "moved": moved,
        "case_number": case["case_number"],
    }


# --------------------------------------------------------------------------
# Choosing what to send
# --------------------------------------------------------------------------

def cases_needing_request(grace_hours=24):
    """Cases with reporter questions that have never been requested."""
    return [
        row["case_id"]
        for row in query_all(
            """
            SELECT DISTINCT tasks.case_id
            FROM pv.case_follow_up_tasks AS tasks
            JOIN pv.safety_cases AS cases ON cases.case_id = tasks.case_id
            WHERE tasks.status IN ('Open', 'In progress')
              AND tasks.check_code <> ALL(%s)
              AND cases.workflow_status NOT IN ('Submitted', 'Closed')
              AND COALESCE(TRIM(cases.reporter_email), '') LIKE '%%@%%'
              AND tasks.created_at <= NOW() - (%s * INTERVAL '1 hour')
              AND NOT EXISTS (
                  SELECT 1 FROM pv.case_follow_up_email_deliveries AS d
                  WHERE d.task_id = tasks.task_id
                    AND (d.status = 'Sent'
                         OR (d.status = 'Failed' AND d.sent_at > NOW() - INTERVAL '1 day'))
              )
            ORDER BY tasks.case_id
            """,
            (sorted(NOT_SENT_AUTOMATICALLY), grace_hours),
        )
    ]


def reminder_status(today=None, reminder_days=7, max_reminders=2):
    """Per-case follow-up email history for cases still waiting on a reply.

    Returns {case_id: {"requested": dt, "sends": n, "reminders": n, "last_sent": dt,
    "due": date, "next_reminder": date or None, "exhausted": bool}}.
    """
    today = today or date.today()
    rows = query_all(
        """
        SELECT d.case_id,
               MIN(d.sent_at) FILTER (WHERE d.kind = 'Request') AS requested,
               COUNT(DISTINCT d.sent_at) AS sends,
               MAX(d.sent_at) AS last_sent,
               (SELECT MIN(t.due_date) FROM pv.case_follow_up_tasks AS t
                 WHERE t.case_id = d.case_id
                   AND t.status IN ('Open', 'In progress')
                   AND t.check_code <> ALL(%s)) AS due
        FROM pv.case_follow_up_email_deliveries AS d
        WHERE d.status = 'Sent'
          AND EXISTS (
              SELECT 1 FROM pv.case_follow_up_tasks AS t
              WHERE t.case_id = d.case_id
                AND t.status IN ('Open', 'In progress')
                AND t.check_code <> ALL(%s)
          )
        GROUP BY d.case_id
        """,
        (sorted(NOT_SENT_AUTOMATICALLY), sorted(NOT_SENT_AUTOMATICALLY)),
    )
    status = {}
    for row in rows:
        last = row["last_sent"]
        last_day = last.date() if isinstance(last, datetime) else last
        # Every email counts: the first request, automatic reminders, and
        # any "Email again" sent by hand.
        sends = row["sends"] or 1
        reminders = max(sends - 1, 0)
        exhausted = reminders >= max_reminders
        next_reminder = None
        if not exhausted and row["due"]:
            next_reminder = max(row["due"] + timedelta(days=1), last_day + timedelta(days=reminder_days))
        status[row["case_id"]] = {
            "requested": row["requested"],
            "sends": sends,
            "reminders": reminders,
            "last_sent": last,
            "due": row["due"],
            "next_reminder": next_reminder,
            "exhausted": exhausted and bool(row["due"]) and row["due"] < today,
        }
    return status


def previously_sent(case_id):
    """True when a follow-up email for this case has already gone out."""
    row = query_one(
        """
        SELECT EXISTS (
            SELECT 1 FROM pv.case_follow_up_email_deliveries
            WHERE case_id = %s AND status = 'Sent'
        ) AS sent
        """,
        (case_id,),
    )
    return bool(row and row["sent"])


def cases_needing_reminder(today=None, reminder_days=7, max_reminders=2):
    today = today or date.today()
    return [
        case_id
        for case_id, info in reminder_status(today, reminder_days, max_reminders).items()
        if info["next_reminder"] and info["next_reminder"] <= today
    ]


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

def run_follow_up_automation(app=None, today=None):
    """Send what is due. Returns a summary dict. Safe to call repeatedly."""
    app = app or current_app._get_current_object()
    summary = {"requests": 0, "reminders": 0, "failed": 0, "skipped": None,
               "checked_at": datetime.now(timezone.utc)}
    if not app.config.get("FOLLOW_UP_AUTO_SEND"):
        summary["skipped"] = "Automatic follow-up is switched off."
        return summary
    if not email_configured(app):
        summary["skipped"] = "Email sending is not set up yet."
        return summary

    connection = get_db()
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
        locked = cursor.fetchone()[0]
    connection.commit()
    if not locked:
        summary["skipped"] = "Another run is in progress."
        return summary
    try:
        grace = app.config.get("FOLLOW_UP_GRACE_HOURS", 24)
        days = app.config.get("FOLLOW_UP_REMINDER_DAYS", 7)
        most = app.config.get("FOLLOW_UP_MAX_REMINDERS", 2)
        work = [(case_id, "Request") for case_id in cases_needing_request(grace)]
        work += [(case_id, "Reminder") for case_id in cases_needing_reminder(today, days, most)]
        for case_id, kind in work:
            case = query_one(
                "SELECT created_by FROM pv.safety_cases WHERE case_id = %s", (case_id,)
            )
            if not case:
                continue
            try:
                result = send_case_request(
                    case_id, case["created_by"], automatic=True, kind=kind,
                    prepared_by="APDL PV system", app=app, today=today,
                )
            except Exception:
                app.logger.exception("Automatic follow-up failed for case %s", case_id)
                try:
                    connection.rollback()
                except Exception:
                    pass
                summary["failed"] += 1
                continue
            if result["sent"]:
                summary["requests" if kind == "Request" else "reminders"] += 1
            elif result["items"]:
                summary["failed"] += 1
                app.logger.warning("Automatic follow-up not sent for case %s: %s",
                                   case_id, result["message"])
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        connection.commit()
    return summary


def _loop(app):
    sleep(90)
    while True:
        with app.app_context():
            try:
                summary = run_follow_up_automation(app)
                app.config["FOLLOW_UP_LAST_RUN"] = summary
                if summary["requests"] or summary["reminders"] or summary["failed"]:
                    app.logger.info("Automatic follow-up: %s", summary)
            except Exception:
                app.logger.exception("Automatic follow-up run failed")
            try:
                from app.services.case_approval import send_approval_notifications

                notices = send_approval_notifications(app)
                if any(notices.values()):
                    app.logger.info("Approval notifications: %s", notices)
            except Exception:
                app.logger.exception("Approval notifications failed")
        sleep(max(5, app.config.get("FOLLOW_UP_CHECK_MINUTES", 60)) * 60)


def start_follow_up_scheduler(app):
    """Start the background check, once per server process."""
    if app.config.get("TESTING"):
        return False
    # Under the debug reloader only the child process serves requests.
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true")
    if debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return False
    if app.config.get("_FOLLOW_UP_SCHEDULER_STARTED"):
        return False
    app.config["_FOLLOW_UP_SCHEDULER_STARTED"] = True
    Thread(target=_loop, args=(app,), daemon=True, name="follow-up-automation").start()
    return True
