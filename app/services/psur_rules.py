"""Approval and locking rules for PSUR records."""

APPROVER_ROLES = ("QPPV", "Deputy QPPV")
APPROVED_STATUSES = ("Approved", "Finalised")
LOCKED_STATUS = "Finalised"


def is_locked(report):
    return (report or {}).get("status") == LOCKED_STATUS


def can_approve(role):
    return role in APPROVER_ROLES


def validate_status_change(report, new_status, role, has_uncoded_terms=False):
    """Return error messages for a requested PSUR status change."""
    current = report.get("status")
    errors = []

    if current == LOCKED_STATUS and new_status == LOCKED_STATUS:
        errors.append(
            "This PSUR is finalised and locked. A QPPV must reopen it "
            "(change the status to Under review) before it can be changed."
        )
        return errors

    if current == LOCKED_STATUS and not can_approve(role):
        errors.append("Only a QPPV or Deputy QPPV can reopen a finalised PSUR.")

    if new_status in APPROVED_STATUSES and not can_approve(role):
        errors.append(
            "Only a QPPV or Deputy QPPV can approve or finalise a PSUR."
        )

    if new_status == LOCKED_STATUS and current not in APPROVED_STATUSES:
        errors.append("Approve the PSUR before finalising it.")

    if new_status == LOCKED_STATUS and has_uncoded_terms:
        errors.append(
            "Some adverse event terms in this PSUR's tabulation are not "
            "coded. Code them on the safety case pages before finalising."
        )

    return errors


def approval_updates(report, new_status, user_id, now):
    """Column values that record approval and finalisation.

    Moving back to Draft or Under review clears the approval, so the report
    must be approved again.
    """
    current = report.get("status")
    if new_status not in APPROVED_STATUSES:
        return {
            "approved_by_user_id": None,
            "approved_at": None,
            "finalised_by_user_id": None,
            "finalised_at": None,
            "clear_approved_by": True,
        }

    updates = {
        "approved_by_user_id": report.get("approved_by_user_id"),
        "approved_at": report.get("approved_at"),
        "finalised_by_user_id": report.get("finalised_by_user_id"),
        "finalised_at": report.get("finalised_at"),
        "clear_approved_by": False,
    }
    if current not in APPROVED_STATUSES or not report.get("approved_at"):
        updates["approved_by_user_id"] = user_id
        updates["approved_at"] = now
    if new_status == LOCKED_STATUS and current != LOCKED_STATUS:
        updates["finalised_by_user_id"] = user_id
        updates["finalised_at"] = now
    if new_status != LOCKED_STATUS:
        updates["finalised_by_user_id"] = None
        updates["finalised_at"] = None
    return updates
