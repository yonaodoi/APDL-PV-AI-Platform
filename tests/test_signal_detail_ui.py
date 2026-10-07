from datetime import date

import app.signals.routes as signal_routes
from app import create_app
from config import TestingConfig


def test_signal_detail_renders_accessible_evaluation_and_supporting_cases(
    monkeypatch,
):
    app = create_app(TestingConfig)
    signal = {
        "signal_id": 8,
        "signal_number": "SIG-2026-008",
        "date_detected": date(2026, 9, 20),
        "product_name": "Example medicine",
        "event_term": "Reported chills",
        "signal_source": "ICSR review",
        "priority": "High",
        "status": "Under evaluation",
        "owner_name": "PV Team",
        "created_by_name": "Reviewer",
        "signal_description": "Review reports from linked cases.",
        "assessment_summary": "",
        "decision_summary": "",
    }
    supporting_case = {
        "case_id": 14,
        "case_number": "CASE-2026-014",
        "country_name": "Uganda",
        "received_date": date(2026, 9, 18),
        "event_description": "Reported chills after treatment.",
        "seriousness": True,
    }
    monkeypatch.setattr(signal_routes, "query_one", lambda sql, parameters=(): signal)
    monkeypatch.setattr(
        signal_routes, "query_all", lambda sql, parameters=(): [supporting_case]
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"

    response = client.get("/signals/8")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'class="signal-detail-page"' in page
    assert "Signal identification" in page
    assert 'for="status"' in page
    assert 'aria-label="Supporting ADR cases"' in page
    assert 'scope="col">Case number' in page
    assert "CASE-2026-014" in page
    assert "Generate AI review draft" in page
