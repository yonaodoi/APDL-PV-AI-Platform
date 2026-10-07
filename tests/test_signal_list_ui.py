from datetime import date

import app.signals.routes as signal_routes
from app import create_app
from config import TestingConfig


def _authenticated_client(app):
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"
    return client


def test_signal_register_renders_accessible_table_and_notifications(monkeypatch):
    app = create_app(TestingConfig)
    signal = {
        "signal_id": 7,
        "signal_number": "SIG-2026-007",
        "date_detected": date(2026, 4, 1),
        "product_name": "Example medicine",
        "event_term": "Reported event",
        "signal_source": "Case review",
        "priority": "High",
        "status": "New",
        "owner_name": "PV Team",
    }
    monkeypatch.setattr(
        signal_routes,
        "query_all",
        lambda sql, parameters=(): [signal]
        if "FROM pv.safety_signals" in sql
        and "SELECT DISTINCT" not in sql
        else [],
    )
    monkeypatch.setattr(
        signal_routes, "query_one", lambda sql, parameters=(): {"total": 2}
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    response = _authenticated_client(app).get("/signals/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "2</span>" in page
    assert 'aria-label="Filtered safety signal totals"' in page
    assert 'aria-label="Safety signals"' in page
    assert "SIG-2026-007" in page
    assert "High-priority signals" in page


def test_signal_register_offers_clear_filters_when_no_match(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(signal_routes, "query_all", lambda sql, parameters=(): [])
    monkeypatch.setattr(
        signal_routes, "query_one", lambda sql, parameters=(): {"total": 0}
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    response = _authenticated_client(app).get("/signals/?status=New")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No matching safety signals" in page
    assert "Clear all filters" in page
