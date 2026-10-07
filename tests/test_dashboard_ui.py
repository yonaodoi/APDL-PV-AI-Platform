from app import create_app
from config import TestingConfig


def test_dashboard_renders_workspace_cards_and_reminder_notice(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 2,
    )

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session["user_id"] = 1
            user_session["full_name"] = "Test User"
            user_session["role"] = "System Administrator"

        response = client.get("/")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Good day, Test User" in page
    assert 'aria-label="Available workspaces"' in page
    assert page.count('class="module-card') == 6
    assert 'class="dashboard-reminder"' in page
    assert "2 overdue follow-up reminders" in page
