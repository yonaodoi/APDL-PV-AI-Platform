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
OVERRIDE_ROLES = ("System Administrator", "QPPV", "Deputy QPPV", "Medical Reviewer")


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
    follow_up = query_one(
        """
        SELECT
            COUNT(*) FILTER (WHERE tasks.status IN ('Open', 'In progress')) AS open_tasks,
            MIN(tasks.due_date) FILTER (WHERE tasks.status IN ('Open', 'In progress')) AS earliest_due,
            EXISTS (
                SELECT 1
                FROM pv.case_follow_up_email_deliveries AS deliveries
                JOIN pv.case_follow_up_tasks AS open_tasks
                    ON open_tasks.task_id = deliveries.task_id
                WHERE deliveries.case_id = %s
                  AND deliveries.status = 'Sent'
                  AND open_tasks.status IN ('Open', 'In progress')
            ) AS follow_up_sent
        FROM pv.case_follow_up_tasks AS tasks
        WHERE tasks.case_id = %s
        """,
        (case_id, case_id),
    ) or {}
    return checks, assessment, follow_up


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
