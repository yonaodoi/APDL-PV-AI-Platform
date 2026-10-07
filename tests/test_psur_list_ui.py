from datetime import date

import app.psur.routes as psur_routes
from app import create_app
from config import TestingConfig


def test_psur_list_renders_accessible_report_register(monkeypatch):
    app = create_app(TestingConfig)
    report = {
        "psur_id": 3,
        "report_number": "PSUR-2026-003",
        "product_name": "Example medicine",
        "reporting_period_start": date(2025, 10, 1),
        "reporting_period_end": date(2026, 9, 30),
        "data_lock_point": date(2026, 9, 30),
        "status": "Draft",
        "prepared_by": "PV Team",
    }
    monkeypatch.setattr(
        psur_routes,
        "query_all",
        lambda sql, parameters=(): [report]
        if "FROM pv.psur_reports" in sql
        and "SELECT DISTINCT" not in sql
        else [],
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

    response = client.get("/psur/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Draft reports</span>" in page
    assert 'aria-label="Periodic safety reports"' in page
    assert "PSUR-2026-003" in page


def test_psur_list_offers_clear_filters_when_no_reports(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(psur_routes, "query_all", lambda sql, parameters=(): [])
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"

    response = client.get("/psur/?status=Draft")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No matching PSUR records" in page
    assert "Clear all filters" in page
