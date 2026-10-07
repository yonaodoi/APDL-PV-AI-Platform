from datetime import date

import app.services.case_follow_up as follow_up_service
from app import create_app
from config import TestingConfig


def test_follow_up_task_register_preserves_actions_and_due_state(monkeypatch):
    app = create_app(TestingConfig)
    task = {
        "task_id": 12,
        "case_id": 34,
        "task_title": "Request missing information",
        "case_number": "CASE-2026-034",
        "assigned_to_name": "PV Team",
        "due_date": date(2026, 4, 10),
        "task_description": "Confirm the patient outcome.",
        "status": "Open",
    }
    monkeypatch.setattr(
        follow_up_service, "get_open_follow_up_tasks", lambda: [task]
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

    response = client.get("/cases/follow-up-tasks")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Request missing information" in page
    assert "Download Word form" in page
    assert "Download email draft" in page
    assert "Email + form in Word" in page
    assert "Mark completed" in page
    assert 'name="csrf_token"' in page
    assert 'class="task-status overdue"' in page
