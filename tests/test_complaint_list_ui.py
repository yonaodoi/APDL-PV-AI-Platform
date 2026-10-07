from datetime import date

import app.complaints.routes as complaint_routes
from app import create_app
from config import TestingConfig


def _authenticated_client(app):
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"
    return client


def test_complaint_list_shows_valid_workflow_totals(monkeypatch):
    app = create_app(TestingConfig)
    complaint = {
        "complaint_id": 5,
        "complaint_number": "PQC-2026-005",
        "date_received": date(2026, 10, 2),
        "product_name": "Example product",
        "batch_number": "BATCH-5",
        "complaint_category": "Packaging",
        "severity": "Serious",
        "status": "Under investigation",
        "country_name": "Uganda",
    }
    monkeypatch.setattr(
        complaint_routes,
        "query_all",
        lambda sql, parameters=(): (
            [complaint]
            if "FROM pv.product_complaints AS product_complaints" in sql
            else []
        ),
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    response = _authenticated_client(app).get("/complaints/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Under investigation</span>" in page
    assert "Serious complaints</span>" in page
    assert "critical complaint(s)" not in page.lower()
    assert 'aria-label="Product quality complaints"' in page


def test_complaint_list_offers_clear_filters_when_no_results(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(
        complaint_routes,
        "query_all",
        lambda sql, parameters=(): [],
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    response = _authenticated_client(app).get(
        "/complaints/?status=Investigation+complete"
    )

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "No matching complaints" in page
    assert "Clear all filters" in page
