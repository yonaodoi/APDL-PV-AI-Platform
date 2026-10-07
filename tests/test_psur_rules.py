from datetime import datetime, timezone

from app.services.psur_rules import (
    approval_updates,
    is_locked,
    validate_status_change,
)


NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)


def test_only_qppv_can_approve_or_finalise():
    report = {"status": "Under review"}

    assert validate_status_change(report, "Approved", "Pharmacovigilance Officer")
    assert validate_status_change(report, "Approved", "QPPV") == []
    assert validate_status_change(report, "Approved", "Deputy QPPV") == []


def test_finalising_requires_prior_approval():
    errors = validate_status_change({"status": "Draft"}, "Finalised", "QPPV")

    assert "Approve the PSUR before finalising it." in errors


def test_finalising_blocked_by_uncoded_terms():
    errors = validate_status_change(
        {"status": "Approved"}, "Finalised", "QPPV", has_uncoded_terms=True
    )

    assert any("not coded" in e for e in errors)


def test_finalised_report_is_locked_until_qppv_reopens():
    finalised = {"status": "Finalised"}

    assert is_locked(finalised)
    assert validate_status_change(finalised, "Finalised", "QPPV")
    assert validate_status_change(finalised, "Under review", "Pharmacovigilance Officer")
    assert validate_status_change(finalised, "Under review", "QPPV") == []


def test_approval_records_user_and_time_then_finalisation():
    approved = approval_updates({"status": "Under review"}, "Approved", 7, NOW)

    assert approved["approved_by_user_id"] == 7
    assert approved["approved_at"] == NOW
    assert approved["finalised_at"] is None

    finalised = approval_updates(
        {"status": "Approved", "approved_by_user_id": 7, "approved_at": NOW},
        "Finalised",
        9,
        NOW,
    )
    assert finalised["approved_by_user_id"] == 7
    assert finalised["finalised_by_user_id"] == 9
    assert finalised["finalised_at"] == NOW


def test_reopening_clears_approval():
    updates = approval_updates(
        {"status": "Finalised", "approved_at": NOW, "finalised_at": NOW},
        "Under review",
        7,
        NOW,
    )

    assert updates["approved_at"] is None
    assert updates["finalised_at"] is None
    assert updates["clear_approved_by"] is True


def test_submission_due_70_days_after_dlp_for_short_interval():
    from datetime import date

    from app.services.psur_rules import submission_status

    report = {
        "reporting_period_start": date(2026, 1, 1),
        "reporting_period_end": date(2026, 6, 30),
        "data_lock_point": date(2026, 6, 30),
    }

    status = submission_status(report, today=date(2026, 8, 20))

    assert status["due_date"] == date(2026, 9, 8)
    assert status["state"] == "due_soon"
    assert status["label"] == "Submission due in 19 days"


def test_submission_due_90_days_for_long_interval_and_overdue():
    from datetime import date

    from app.services.psur_rules import submission_status

    report = {
        "reporting_period_start": date(2023, 1, 1),
        "reporting_period_end": date(2025, 12, 31),
        "data_lock_point": date(2025, 12, 31),
    }

    status = submission_status(report, today=date(2026, 4, 5))

    assert status["due_date"] == date(2026, 3, 31)
    assert status["label"] == "Submission overdue by 5 days"


def test_recorded_submission_on_time_or_late():
    from datetime import date

    from app.services.psur_rules import submission_status

    report = {
        "reporting_period_start": date(2026, 1, 1),
        "reporting_period_end": date(2026, 6, 30),
        "data_lock_point": date(2026, 6, 30),
        "submitted_date": date(2026, 9, 10),
    }

    assert submission_status(report)["label"] == "Submitted 2 days late"


def test_submission_date_validation():
    from datetime import date

    from app.services.psur_rules import validate_submission_date

    draft = {"status": "Draft", "data_lock_point": date(2026, 6, 30)}
    final = {"status": "Finalised", "data_lock_point": date(2026, 6, 30)}
    today = date(2026, 10, 7)

    assert "Finalise the PSUR before recording its submission." in (
        validate_submission_date(draft, date(2026, 9, 1), today)
    )
    assert validate_submission_date(final, date(2026, 9, 1), today) == []
    assert validate_submission_date(final, date(2026, 6, 1), today)
    assert validate_submission_date(final, date(2026, 10, 8), today)
