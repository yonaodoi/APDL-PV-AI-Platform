"""Approval and locking rules for PSUR records."""

APPROVER_ROLES = ("QPPV", "Deputy QPPV", "Group Head RA & Quality")
APPROVED_STATUSES = ("Approved", "Finalised")
LOCKED_STATUS = "Finalised"


def is_locked(report):
    return (report or {}).get("status") == LOCKED_STATUS


def can_approve(role, designated_qppv=False):
    """QPPV / Deputy QPPV roles, or the user designated as QPPV."""
    return bool(designated_qppv) or role in APPROVER_ROLES


def validate_status_change(
    report, new_status, role, has_uncoded_terms=False, designated_qppv=False
):
    """Return error messages for a requested PSUR status change."""
    current = report.get("status")
    errors = []

    if current == LOCKED_STATUS and new_status == LOCKED_STATUS:
        errors.append(
            "This PSUR is finalised and locked. A QPPV must reopen it "
            "(change the status to Under review) before it can be changed."
        )
        return errors

    if current == LOCKED_STATUS and not can_approve(role, designated_qppv):
        errors.append("Only the QPPV or Deputy QPPV can reopen a finalised PSUR.")

    if new_status in APPROVED_STATUSES and not can_approve(role, designated_qppv):
        errors.append(
            "Only the QPPV or Deputy QPPV can approve or finalise a PSUR."
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


# ICH E2C(R2) / GVP Module VII: submit within 70 calendar days of the data
# lock point for intervals up to 12 months, and within 90 days for longer
# intervals. Confirm each market's national requirement.
SHORT_INTERVAL_DAYS = 70
LONG_INTERVAL_DAYS = 90
DUE_SOON_DAYS = 30


def submission_due_date(report):
    from datetime import timedelta

    dlp = report.get("data_lock_point")
    start = report.get("reporting_period_start")
    end = report.get("reporting_period_end")
    if not dlp:
        return None
    long_interval = bool(start and end and (end - start).days > 366)
    return dlp + timedelta(
        days=LONG_INTERVAL_DAYS if long_interval else SHORT_INTERVAL_DAYS
    )


def submission_status(report, today=None):
    """Deadline state for a PSUR: overdue, due_soon, on_track,
    submitted_on_time or submitted_late."""
    from datetime import date

    today = today or date.today()
    due = submission_due_date(report)
    if due is None:
        return None
    submitted = report.get("submitted_date")
    if submitted:
        late = (submitted - due).days
        return {
            "due_date": due,
            "state": "submitted_late" if late > 0 else "submitted_on_time",
            "label": (
                f"Submitted {late} day{'s' if late != 1 else ''} late"
                if late > 0
                else "Submitted on time"
            ),
        }
    remaining = (due - today).days
    if remaining < 0:
        state = "overdue"
        label = f"Submission overdue by {-remaining} day{'s' if remaining != -1 else ''}"
    elif remaining <= DUE_SOON_DAYS:
        state = "due_soon"
        label = (
            "Submission due today"
            if remaining == 0
            else f"Submission due in {remaining} day{'s' if remaining != 1 else ''}"
        )
    else:
        state = "on_track"
        label = f"Submission due in {remaining} days"
    return {"due_date": due, "state": state, "label": label}


def validate_submission_date(report, submitted, today=None):
    from datetime import date

    today = today or date.today()
    errors = []
    if report.get("status") != LOCKED_STATUS:
        errors.append("Finalise the PSUR before recording its submission.")
    if submitted is None:
        errors.append("Enter the submission date.")
        return errors
    if submitted > today:
        errors.append("The submission date cannot be in the future.")
    dlp = report.get("data_lock_point")
    if dlp and submitted < dlp:
        errors.append("The submission date cannot be before the data lock point.")
    return errors


def get_psur_alerts(today=None):
    """PSURs due within DUE_SOON_DAYS or overdue, for the dashboard."""
    from flask import current_app

    from app.db import get_db, query_all

    try:
        reports = query_all(
            """
            SELECT
                psur_id,
                report_number,
                product_name,
                status,
                reporting_period_start,
                reporting_period_end,
                data_lock_point,
                submitted_date
            FROM pv.psur_reports
            WHERE submitted_date IS NULL
            """
        )
    except Exception:
        current_app.logger.exception("Could not load PSUR alerts")
        try:
            get_db().rollback()
        except Exception:
            pass
        return []
    alerts = []
    for report in reports:
        status = submission_status(report, today)
        if status and status["state"] in ("overdue", "due_soon"):
            alerts.append({**report, "submission": status})
    alerts.sort(key=lambda r: r["submission"]["due_date"])
    return alerts
