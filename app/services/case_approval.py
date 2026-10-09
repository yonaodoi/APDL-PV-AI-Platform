"""Three-level sign-off for safety cases before regulatory submission.

PV Officer prepares the case and sends it for review
  -> QPPV or Deputy QPPV reviews it (or returns it with a comment)
  -> Group Head RA & Quality approves it (or returns it with a comment)
  -> only an approved case can be marked as submitted.

Each step sets the case status: Assessment -> QPPV review -> Group Head
approval -> Approved; a return puts the case back at Assessment.

The same person cannot send, review and approve the same case. Editing a
case after review sends it back for review. Reviewers and the Group Head get
one summary email a day listing what is waiting for them, plus an immediate
email for a serious case due within 3 days. The PV Officer is emailed when a
case comes back or is approved.
"""

from datetime import datetime, timezone

from flask import current_app, url_for

from app.db import get_db, query_all, query_one, transaction

PENDING_REVIEW = "Pending review"
PENDING_APPROVAL = "Pending approval"
APPROVED = "Approved"
RETURNED = "Returned"

# Each sign-off stage puts the case at the matching status.
STATUS_FOR_STAGE = {
    PENDING_REVIEW: "QPPV review",
    PENDING_APPROVAL: "Case approval",
    APPROVED: "Approved",
    RETURNED: "Assessment",
}

# Defaults; an administrator can change them under Administration ->
# Sign-off settings (see app.services.approval_settings).
REVIEWER_ROLES = {"QPPV", "Deputy QPPV"}
APPROVER_ROLES = {"Group Head RA & Quality"}


class ApprovalError(ValueError):
    """An approval step that is not allowed; the message explains why."""


# --------------------------------------------------------------------------
# Who may do what
# --------------------------------------------------------------------------

def _settings(settings=None):
    if settings is not None:
        return settings
    from app.services.approval_settings import load_settings

    return load_settings()


def can_review(role, designated_qppv=False, settings=None):
    return role in _settings(settings)["reviewer_roles"] or bool(designated_qppv)


def can_approve(role, settings=None):
    return role in _settings(settings)["approver_roles"]


def last_actor(history, step):
    for entry in reversed(history or []):
        if entry["step"] == step:
            return entry.get("decided_by")
    return None


def check_step(stage, action, user_id, role, designated_qppv, history, settings=None):
    """Raise ApprovalError if ``action`` is not allowed now for this user."""
    settings = _settings(settings)
    if action == "send":
        if stage in (PENDING_REVIEW, PENDING_APPROVAL):
            raise ApprovalError("This case is already waiting for sign-off.")
        if stage == APPROVED:
            raise ApprovalError("This case is already approved.")
        return
    if action in ("review", "return_review"):
        if stage != PENDING_REVIEW:
            raise ApprovalError("This case is not waiting for review.")
        if not can_review(role, designated_qppv, settings):
            raise ApprovalError(
                f"Only {' or '.join(settings['reviewer_roles'])} can do the "
                f"{settings['review_title']}."
            )
        if last_actor(history, "Sent for review") == user_id:
            raise ApprovalError(
                "You sent this case for review, so someone else must review it."
            )
        return
    if action in ("approve", "return_approval"):
        if stage != PENDING_APPROVAL:
            raise ApprovalError("This case is not waiting for approval.")
        if not can_approve(role, settings):
            raise ApprovalError(
                f"Only {' or '.join(settings['approver_roles'])} can give the "
                f"{settings['approval_title']}."
            )
        if last_actor(history, "Reviewed") == user_id:
            raise ApprovalError(
                "You reviewed this case, so someone else must approve it."
            )
        return
    raise ApprovalError("Unknown approval step.")


NEXT_STAGE = {
    "send": (PENDING_REVIEW, "Sent for review"),
    "review": (PENDING_APPROVAL, "Reviewed"),
    "return_review": (RETURNED, "Returned"),
    "approve": (APPROVED, "Approved"),
    "return_approval": (RETURNED, "Returned"),
}


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------

def approval_history(case_id):
    return query_all(
        """
        SELECT a.step, a.comment, a.decided_by, a.decided_at,
               u.full_name, r.role_name
        FROM pv.case_approvals AS a
        LEFT JOIN pv.users AS u ON u.user_id = a.decided_by
        LEFT JOIN pv.roles AS r ON r.role_id = u.role_id
        WHERE a.case_id = %s
        ORDER BY a.decided_at, a.approval_id
        """,
        (case_id,),
    )


def take_step(case_id, action, user_id, role, designated_qppv=False, comment="",
              override_reason=""):
    """Record one sign-off step. Returns the new stage. Raises ApprovalError."""
    case = query_one(
        """
        SELECT case_id, case_number, approval_stage, workflow_status,
               regulatory_submitted_date
        FROM pv.safety_cases WHERE case_id = %s
        """,
        (case_id,),
    )
    if case is None:
        raise ApprovalError("Case not found.")
    if case["regulatory_submitted_date"]:
        raise ApprovalError("This case has already been submitted.")
    comment = (comment or "").strip()
    if action.startswith("return") and not comment:
        raise ApprovalError("Say what needs to change before returning the case.")
    override_note = ""
    if action == "send":
        from app.services.case_workflow import (
            LEGACY_STATUSES,
            can_override,
            load_workflow_inputs,
            readiness_blockers,
        )

        status = LEGACY_STATUSES.get(case["workflow_status"], case["workflow_status"])
        if status != "Assessment":
            raise ApprovalError(
                "Only a case at Assessment can be sent for review. "
                f"This case is at {case['workflow_status']}."
            )
        checks, assessment, _ = load_workflow_inputs(case_id)
        full = query_one(
            "SELECT causality_assessment FROM pv.safety_cases WHERE case_id = %s", (case_id,)
        ) or {}
        blockers = readiness_blockers(checks, assessment, full.get("causality_assessment"))
        if blockers:
            reason = (override_reason or "").strip()
            if not (reason and can_override(role, designated_qppv)):
                raise ApprovalError(
                    "Not ready to send for review: " + " ".join(blockers)
                    + (" Tick Override and give a reason to send it anyway."
                       if can_override(role, designated_qppv) else "")
                )
            override_note = (
                f" Readiness check overridden. Reason: {reason}. Outstanding: {' '.join(blockers)}"
            )

    history = approval_history(case_id)
    check_step(case["approval_stage"], action, user_id, role, designated_qppv, history)
    stage, step = NEXT_STAGE[action]

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_cases
            SET approval_stage = %s, approval_updated_at = CURRENT_TIMESTAMP,
                workflow_status = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE case_id = %s
            """,
            (stage, STATUS_FOR_STAGE[stage], case_id),
        )
        cursor.execute(
            """
            INSERT INTO pv.case_approvals (case_id, step, comment, decided_by)
            VALUES (%s, %s, %s, %s)
            """,
            (case_id, step, comment or None, user_id),
        )
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                f"Sign-off: {step}",
                f"Status is now {STATUS_FOR_STAGE[stage]}." + (f" Comment: {comment}" if comment else "")
                + override_note,
                user_id,
            ),
        )
    return stage


def reset_after_edit(cursor, case_id, user_id):
    """An edited case must be reviewed again. Returns True if it was reset."""
    cursor.execute(
        """
        UPDATE pv.safety_cases
        SET approval_stage = %s, approval_updated_at = CURRENT_TIMESTAMP,
            workflow_status = %s
        WHERE case_id = %s AND approval_stage IN (%s, %s)
          AND regulatory_submitted_date IS NULL
        RETURNING case_id
        """,
        (PENDING_REVIEW, STATUS_FOR_STAGE[PENDING_REVIEW], case_id, PENDING_APPROVAL, APPROVED),
    )
    if cursor.fetchone() is None:
        return False
    cursor.execute(
        """
        INSERT INTO pv.case_approvals (case_id, step, comment, decided_by)
        VALUES (%s, 'Reset', %s, %s)
        """,
        (case_id, "The case was edited after review, so it needs review again.", user_id),
    )
    return True


def cases_at_stage(stage, limit=200):
    return query_all(
        """
        SELECT c.case_id, c.case_number, c.seriousness, c.received_date,
               c.approval_updated_at, c.workflow_status,
               p.product_name, co.country_name,
               (SELECT u.full_name FROM pv.case_approvals AS a
                  JOIN pv.users AS u ON u.user_id = a.decided_by
                 WHERE a.case_id = c.case_id
                 ORDER BY a.decided_at DESC, a.approval_id DESC LIMIT 1) AS last_by,
               (SELECT a.comment FROM pv.case_approvals AS a
                 WHERE a.case_id = c.case_id
                 ORDER BY a.decided_at DESC, a.approval_id DESC LIMIT 1) AS last_comment
        FROM pv.safety_cases AS c
        LEFT JOIN LATERAL (
            SELECT product_name FROM pv.case_products
            WHERE case_id = c.case_id ORDER BY case_product_id LIMIT 1
        ) AS p ON TRUE
        LEFT JOIN pv.countries AS co ON co.country_id = c.country_id
        WHERE c.approval_stage = %s
        ORDER BY c.seriousness DESC, c.approval_updated_at
        LIMIT %s
        """,
        (stage, limit),
    )


def recently_decided(step, days=90, limit=200):
    """Cases a step was taken on recently (e.g. 'Reviewed'), newest first."""
    return query_all(
        """
        SELECT DISTINCT ON (c.case_id)
               c.case_id, c.case_number, c.seriousness, c.approval_stage,
               c.regulatory_submitted_date, a.decided_at, u.full_name AS decided_by_name,
               a.comment, p.product_name
        FROM pv.case_approvals AS a
        JOIN pv.safety_cases AS c ON c.case_id = a.case_id
        LEFT JOIN pv.users AS u ON u.user_id = a.decided_by
        LEFT JOIN LATERAL (
            SELECT product_name FROM pv.case_products
            WHERE case_id = c.case_id ORDER BY case_product_id LIMIT 1
        ) AS p ON TRUE
        WHERE a.step = %s
          AND a.decided_at > NOW() - (%s * INTERVAL '1 day')
        ORDER BY c.case_id, a.decided_at DESC
        LIMIT %s
        """,
        (step, days, limit),
    )


def pending_count_for(role, designated_qppv=False, settings=None):
    """How many cases are waiting for this user's stage (for the nav badge)."""
    settings = _settings(settings)
    stages = []
    if can_review(role, designated_qppv, settings):
        stages.append(PENDING_REVIEW)
    if can_approve(role, settings):
        stages.append(PENDING_APPROVAL)
    if not stages:
        return 0
    try:
        row = query_one(
            "SELECT COUNT(*) AS n FROM pv.safety_cases WHERE approval_stage = ANY(%s)",
            (stages,),
        )
    except Exception:
        try:
            get_db().rollback()
        except Exception:
            pass
        return 0
    return row["n"] if row else 0


# --------------------------------------------------------------------------
# Emails
# --------------------------------------------------------------------------

def _recipients(stage, settings):
    """Users holding the stage's roles, plus any extra addresses set by the
    administrator (keyed by negative numbers so their daily record is kept)."""
    if stage == PENDING_REVIEW:
        sql = """
            SELECT u.user_id, u.full_name, u.email
            FROM pv.users AS u JOIN pv.roles AS r ON r.role_id = u.role_id
            WHERE u.is_active AND (r.role_name = ANY(%s) OR u.is_designated_qppv)
        """
        parameters = (list(settings["reviewer_roles"]),)
        extra = settings["review_extra_emails"]
    else:
        sql = """
            SELECT u.user_id, u.full_name, u.email
            FROM pv.users AS u JOIN pv.roles AS r ON r.role_id = u.role_id
            WHERE u.is_active AND r.role_name = ANY(%s)
        """
        parameters = (list(settings["approver_roles"]),)
        extra = settings["approval_extra_emails"]
    people = [r for r in query_all(sql, parameters) if (r.get("email") or "").strip()]
    known = {p["email"].strip().lower() for p in people}
    for address in extra:
        if address not in known:
            people.append({"user_id": None, "full_name": "colleague", "email": address})
    return people


def _sent_key(person, key):
    # Extra addresses have no user record; keep their notices apart by address.
    return key if person.get("user_id") else f"{key}:{person['email']}"


def _owner(person, fallback_user_id):
    return person.get("user_id") or fallback_user_id


def _already_sent(person, kind, key, fallback_user_id):
    return query_one(
        "SELECT 1 AS x FROM pv.approval_notifications WHERE user_id = %s AND kind = %s AND notice_key = %s",
        (_owner(person, fallback_user_id), kind, _sent_key(person, key)),
    ) is not None


def _mark_sent(person, kind, key, fallback_user_id):
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.approval_notifications (user_id, kind, notice_key)
            VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
            """,
            (_owner(person, fallback_user_id), kind, _sent_key(person, key)),
        )


def digest_body(name, stage, cases, link, settings=None):
    settings = _settings(settings)
    action = settings["review_title"] if stage == PENDING_REVIEW else settings["approval_title"]
    lines = [
        f"Dear {name},",
        "",
        f"{len(cases)} safety case(s) are waiting for your {action}:",
        "",
    ]
    for case in cases:
        waiting = case.get("approval_updated_at")
        since = f", waiting since {waiting.astimezone():%d %b %Y}" if waiting else ""
        lines.append(
            f"  - {case['case_number']} ({'Serious' if case.get('seriousness') else 'Non-serious'}"
            f", {case.get('product_name') or 'product not recorded'}{since})"
        )
    lines += [
        "",
        f"Open the approvals page: {link}",
        "",
        "APDL PV platform (automatic message)",
    ]
    return "\n".join(lines)


def _send(app, to, subject, body):
    from app.services.follow_up_email import send_notification_email

    send_notification_email(app, to, subject, body)


def send_approval_notifications(app, now=None):
    """Daily summaries and urgent alerts. Safe to call every hour."""
    from app.services.follow_up_automation import email_configured
    from app.services.reporting_clock import evaluate_reporting_clock

    summary = {"digests": 0, "urgent": 0, "failed": 0}
    if not email_configured(app):
        return summary
    settings = _settings()
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone()
    base = app.config.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")
    link = base + "/cases/approvals"
    # Extra addresses are recorded against the first administrator account.
    admin = query_one(
        """
        SELECT u.user_id FROM pv.users AS u JOIN pv.roles AS r ON r.role_id = u.role_id
        WHERE r.role_name = 'System Administrator' ORDER BY u.user_id LIMIT 1
        """
    )
    fallback = admin["user_id"] if admin else None

    for stage, title in (
        (PENDING_REVIEW, settings["review_title"]),
        (PENDING_APPROVAL, settings["approval_title"]),
    ):
        cases = cases_at_stage(stage)
        if not cases:
            continue
        people = [p for p in _recipients(stage, settings) if p.get("user_id") or fallback]

        # Urgent: serious cases close to their reporting deadline, once per
        # case and stage.
        for case in cases:
            if not case.get("seriousness"):
                continue
            clock = evaluate_reporting_clock(case, local_now.date())
            if (
                not clock
                or clock.get("days_remaining") is None
                or clock["days_remaining"] > settings["urgent_days"]
            ):
                continue
            key = f"{case['case_id']}:{stage}"
            for person in people:
                if _already_sent(person, "urgent", key, fallback):
                    continue
                try:
                    _send(
                        app, person["email"],
                        f"URGENT: {case['case_number']} needs {title} ({clock['label']})",
                        digest_body(person["full_name"], stage, [case], link, settings),
                    )
                    _mark_sent(person, "urgent", key, fallback)
                    summary["urgent"] += 1
                except Exception:
                    app.logger.exception("Urgent approval email failed")
                    summary["failed"] += 1

        # One summary per person per day, after the configured hour.
        if local_now.hour < settings["digest_hour"]:
            continue
        key = f"{local_now:%Y-%m-%d}:{stage}"
        for person in people:
            if _already_sent(person, "digest", key, fallback):
                continue
            try:
                _send(
                    app, person["email"],
                    f"{len(cases)} safety case(s) waiting for {title}",
                    digest_body(person["full_name"], stage, cases, link, settings),
                )
                _mark_sent(person, "digest", key, fallback)
                summary["digests"] += 1
            except Exception:
                app.logger.exception("Daily approval summary failed")
                summary["failed"] += 1
    return summary


def notify_officer(app, case_id, stage, comment, actor_name, settings=None):
    """Tell the PV Officer who sent the case that it came back or was approved,
    plus any extra PV officer addresses the administrator added."""
    from app.services.follow_up_automation import email_configured

    if stage not in (RETURNED, APPROVED) or not email_configured(app):
        return False
    settings = _settings(settings)
    row = query_one(
        """
        SELECT c.case_number, u.full_name, u.email
        FROM pv.safety_cases AS c
        LEFT JOIN LATERAL (
            SELECT a.decided_by FROM pv.case_approvals AS a
            WHERE a.case_id = c.case_id AND a.step = 'Sent for review'
            ORDER BY a.decided_at DESC LIMIT 1
        ) AS sent ON TRUE
        LEFT JOIN pv.users AS u ON u.user_id = sent.decided_by
        WHERE c.case_id = %s
        """,
        (case_id,),
    )
    if not row:
        return False
    recipients = []
    sender_email = (row.get("email") or "").strip().lower()
    if settings.get("notify_officer", True) and sender_email:
        recipients.append((row.get("full_name") or "colleague", sender_email))
    for email in settings.get("officer_extra_emails") or []:
        if email not in [r[1] for r in recipients]:
            recipients.append(("colleague", email))
    if not recipients:
        return False

    base = app.config.get("APP_BASE_URL", "http://localhost:5000").rstrip("/")
    if stage == RETURNED:
        subject = f"{row['case_number']} returned for changes"
        text = f"{actor_name} returned {row['case_number']} with this comment:\n\n  {comment}\n\nPlease make the changes and send it for review again."
    else:
        subject = f"{row['case_number']} approved for submission"
        text = f"{actor_name} approved {row['case_number']}. It can now be submitted to the regulator and marked as submitted."
    sent = False
    for name, email in recipients:
        body = f"Dear {name},\n\n{text}\n\nOpen the case: {base}/cases/{case_id}\n\nAPDL PV platform (automatic message)"
        try:
            _send(app, email, subject, body)
            sent = True
        except Exception:
            app.logger.exception("Officer approval email failed")
    return sent
