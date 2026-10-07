from datetime import date

from app import create_app
from app.services.reporting_clock import summarise_reporting_alerts
from config import TestingConfig


def _sign_in(client):
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"


def test_dashboard_renders_workspace_cards_and_reminder_notice(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 2,
    )
    monkeypatch.setattr(
        "app.services.reporting_clock.get_reporting_alerts",
        lambda: None,
    )

    with app.test_client() as client:
        _sign_in(client)
        response = client.get("/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Good day, Test User" in page
    assert 'aria-label="Available workspaces"' in page
    assert page.count('class="module-card') == 6
    assert 'class="dashboard-reminder"' in page
    assert "2 overdue follow-up reminders" in page
    assert "Regulatory reporting deadlines" not in page


def test_dashboard_shows_overdue_reporting_alert(monkeypatch):
    app = create_app(TestingConfig)
    today = date(2026, 10, 7)
    alerts = summarise_reporting_alerts(
        [
            {
                "case_id": 2,
                "case_number": "APDL-ICSR-26-012",
                "received_date": date(2026, 9, 16),
                "seriousness": True,
                "workflow_status": "New",
                "regulatory_submitted_date": None,
                "country_name": "Malawi",
            },
            {
                "case_id": 5,
                "case_number": "APDL-ICSR-26-020",
                "received_date": date(2026, 9, 23),
                "seriousness": True,
                "workflow_status": "Triage",
                "regulatory_submitted_date": None,
                "country_name": "Uganda",
            },
        ],
        today=today,
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )
    monkeypatch.setattr(
        "app.services.reporting_clock.get_reporting_alerts",
        lambda: alerts,
    )

    with app.test_client() as client:
        _sign_in(client)
        response = client.get("/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "1 case overdue for regulatory reporting" in page
    assert "A further 1 case is due within 3 days." in page
    assert "APDL-ICSR-26-012" in page
    assert "Overdue by 6 days" in page
    assert "Due in 1 day" in page
    assert "deadline=overdue" in page


def test_alert_summary_orders_most_overdue_first():
    cases = [
        {"case_id": 1, "received_date": date(2026, 9, 20), "seriousness": True,
         "workflow_status": "New", "regulatory_submitted_date": None},
        {"case_id": 2, "received_date": date(2026, 9, 1), "seriousness": True,
         "workflow_status": "New", "regulatory_submitted_date": None},
        {"case_id": 3, "received_date": date(2026, 9, 1), "seriousness": False,
         "workflow_status": "New", "regulatory_submitted_date": None},
    ]

    alerts = summarise_reporting_alerts(cases, today=date(2026, 10, 7))

    assert alerts["overdue_count"] == 2
    assert [case["case_id"] for case in alerts["overdue"]] == [2, 1]
    assert alerts["due_soon_count"] == 0
