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
