"""Case workflow: suggested next status, readiness before submission, and
automatic status moves driven by follow-up activity."""

from app.db import query_one

STATUSES = (
    "New",
    "Triage",
    "Follow-up requested",
    "Medical review",
    "Ready for submission",
    "Submitted",
    "Closed",
)
GATED_STATUSES = ("Ready for submission", "Submitted")
OVERRIDE_ROLES = (
    "System Administrator",
    "QPPV",
    "Deputy QPPV",
    "Medical Reviewer",
    "Group Head RA & Quality",
)


# --------------------------------------------------------------------------
# Pure rules
# --------------------------------------------------------------------------

def readiness_blockers(checks, assessment, causality):
    """What stops a case being ready for submission (empty when ready)."""
    blockers = []
    to_review = [c for c in checks or [] if c.get("status") != "Pass"]
    if to_review:
        blockers.append(
            f"{len(to_review)} completeness item(s) still need review: "
            + ", ".join(c.get("label", "item") for c in to_review)
            + "."
        )
    if not assessment:
        blockers.append("Listedness has not been assessed.")
    elif assessment.get("listedness_status") == "Insufficient information":
        blockers.append(
            "Listedness could not be assessed (insufficient information)."
        )
    elif assessment.get("assessment_source", "reviewer") == "automatic":
        blockers.append(
            "Listedness is an automatic result; a reviewer must confirm it."
        )
    if not (causality or "").strip():
        blockers.append("No causality assessment is recorded.")
    return blockers


def suggest_status(case, checks, assessment, open_task_count, follow_up_sent):
    """Suggested next status with reasons, or None for closed cases."""
    current = case.get("workflow_status")
    if current == "Closed":
        return None

    submitted = case.get("regulatory_submitted_date")
    to_review = [c for c in checks or [] if c.get("status") != "Pass"]

    if submitted:
        status = "Submitted"
        reasons = [f"Submitted to the regulator on {submitted:%d %b %Y}."]
    elif open_task_count and follow_up_sent:
        status = "Follow-up requested"
        reasons = [
            f"{open_task_count} follow-up task(s) open and a follow-up "
            "request has been sent. Waiting for the reply."
        ]
    elif to_review:
        status = "Triage"
        reasons = [
            f"{len(to_review)} completeness item(s) need review. Correct the "
            "case or send follow-up requests."
        ]
    else:
        blockers = readiness_blockers(checks, assessment, case.get("causality_assessment"))
        if blockers:
            status = "Medical review"
            reasons = blockers
        else:
            status = "Ready for submission"
            reasons = [
                "Checklist complete, listedness confirmed "
                f"({assessment.get('listedness_status')}) and causality "
                f"recorded ({case.get('causality_assessment')})."
            ]

    if current == "Submitted" and status != "Submitted":
        # Never suggest moving a submitted case backwards.
        return {"status": current, "reasons": ["Case has been submitted."], "same": True}
    return {"status": status, "reasons": reasons, "same": status == current}


def draft_review_note(case, checks, assessment, open_tasks, earliest_due, follow_up_sent, suggestion, today=None):
    """A plain-language review note drafted from the case's current state."""
    from datetime import date

    today = today or date.today()
    parts = [f"Review on {today:%d %b %Y}."]

    to_review = [c for c in checks or [] if c.get("status") != "Pass"]
    if to_review:
        parts.append(
            f"{len(to_review)} checklist item(s) need review: "
            + "; ".join(c.get("label", "item") for c in to_review)
            + "."
        )
    else:
        parts.append("Completeness checklist passed.")

    if assessment:
        source = (
            "automatic, awaiting reviewer confirmation"
            if assessment.get("assessment_source") == "automatic"
            else "confirmed by reviewer"
        )
        parts.append(
            f"Listedness: {assessment.get('listedness_status')} / "
            f"{assessment.get('expectedness_status')} ({source})."
        )
    else:
        parts.append("Listedness: not yet assessed.")

    causality = (case.get("causality_assessment") or "").strip()
    parts.append(f"Causality: {causality}." if causality else "Causality: not yet assessed.")

    if open_tasks:
        due = f", earliest due {earliest_due:%d %b %Y}" if earliest_due else ""
        sent = "request sent" if follow_up_sent else "no request sent yet"
        parts.append(f"Follow-up: {open_tasks} open task(s){due}; {sent}.")
    else:
        parts.append("Follow-up: none outstanding.")

    if suggestion and not suggestion.get("same"):
        parts.append(f"Next step: {suggestion['status']}.")
    elif suggestion:
        parts.append(f"Status {suggestion['status']} is appropriate.")
    return " ".join(parts)


def needs_override(new_status, blockers):
    return new_status in GATED_STATUSES and bool(blockers)


def can_override(role, designated_qppv=False):
    return bool(designated_qppv) or role in OVERRIDE_ROLES


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------

def load_workflow_inputs(case_id):
    """Saved checklist, listedness assessment and follow-up activity."""
    from app.db import query_all

    checks = query_all(
        """
        SELECT check_code AS code, check_label AS label, status, message
        FROM pv.case_completeness_checks
        WHERE case_id = %s
        """,
        (case_id,),
    )
    assessment = query_one(
        "SELECT * FROM pv.case_safety_assessments WHERE case_id = %s",
        (case_id,),
    )
    follow_up = dict(
        query_one(
            """
            SELECT
                COUNT(*) FILTER (WHERE status IN ('Open', 'In progress')) AS open_tasks,
                MIN(due_date) FILTER (WHERE status IN ('Open', 'In progress')) AS earliest_due
            FROM pv.case_follow_up_tasks
            WHERE case_id = %s
            """,
            (case_id,),
        )
        or {}
    )
    follow_up["follow_up_sent"] = follow_up_request_sent(case_id)
    return checks, assessment, follow_up


def follow_up_request_sent(case_id):
    """Whether a follow-up email was sent for an open task of this case.

    Kept separate so that a database permission problem on the email log
    only hides this one fact instead of the whole workflow panel.
    """
    from flask import current_app

    from app.db import get_db

    try:
        row = query_one(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pv.case_follow_up_email_deliveries AS deliveries
                JOIN pv.case_follow_up_tasks AS tasks
                    ON tasks.task_id = deliveries.task_id
                WHERE deliveries.case_id = %s
                  AND deliveries.status = 'Sent'
                  AND tasks.status IN ('Open', 'In progress')
            ) AS sent
            """,
            (case_id,),
        )
    except Exception as error:
        current_app.logger.warning(
            "Could not read follow-up email records for case %s: %s",
            case_id,
            error,
        )
        try:
            get_db().rollback()
        except Exception:
            pass
        return False
    return bool(row and row.get("sent"))


def is_designated_qppv(user_id):
    try:
        row = query_one(
            "SELECT is_designated_qppv FROM pv.users WHERE user_id = %s",
            (user_id,),
        )
    except Exception:
        from app.db import get_db

        try:
            get_db().rollback()
        except Exception:
            pass
        return False
    return bool(row and row.get("is_designated_qppv"))


def auto_move_status(cursor, case_id, from_statuses, to_status, reason, actor_user_id):
    """Move a case's status if it is currently one of ``from_statuses``.

    Logs the move in the case history. Returns True when it moved.
    """
    cursor.execute(
        """
        UPDATE pv.safety_cases
        SET workflow_status = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE case_id = %s
          AND workflow_status = ANY(%s)
        RETURNING case_id
        """,
        (to_status, case_id, list(from_statuses)),
    )
    if cursor.fetchone() is None:
        return False
    cursor.execute(
        """
        INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
        VALUES (%s, %s, %s, %s)
        """,
        (
            case_id,
            "Status changed automatically",
            f"Status moved to {to_status}: {reason}",
            actor_user_id,
        ),
    )
    return True
