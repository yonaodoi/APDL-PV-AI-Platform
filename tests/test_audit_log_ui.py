from datetime import datetime, timezone

import app.administration.routes as admin_routes
from app import create_app
from config import TestingConfig


def test_audit_trail_shows_record_numbers_actions_and_links(monkeypatch):
    entries = [
        {
            "record_type": "safety_case",
            "record_id": 6,
            "action": "Case reviewed",
            "details": "Completeness review saved.",
            "occurred_at": datetime(2026, 9, 22, 21, 16, tzinfo=timezone.utc),
            "full_name": "Ezekiel Kamugisha",
            "record_reference": "APDL-ICSR-26-016",
        },
        {
            "record_type": "case_intake",
            "record_id": 2,
            "action": "Intake saved",
            "details": None,
            "occurred_at": datetime(2026, 9, 22, 20, 12, tzinfo=timezone.utc),
            "full_name": None,
            "record_reference": None,
        },
    ]
    monkeypatch.setattr(
        admin_routes, "query_all", lambda sql, parameters=(): entries
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )
    app = create_app(TestingConfig)
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"

    page = client.get("/administration/audit-log").get_data(as_text=True)

    assert "Safety case" in page
    assert "APDL-ICSR-26-016" in page
    assert 'href="/cases/6"' in page
    assert "Case reviewed" in page
    assert "Completeness review saved." in page
    assert "Case intake" in page
    assert "#2" in page
    assert "nth-child(5)" not in page


def test_audit_record_label_falls_back_to_readable_text():
    assert admin_routes.audit_record_label("psur") == "PSUR"
    assert admin_routes.audit_record_label("batch_record") == "Batch record"
