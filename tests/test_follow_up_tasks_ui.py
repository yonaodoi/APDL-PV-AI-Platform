from datetime import date, datetime, timezone

import app.services.case_follow_up as follow_up_service
from app import create_app
from config import TestingConfig


def test_follow_up_page_groups_tasks_into_one_request_per_case(monkeypatch):
    app = create_app(TestingConfig)
    base = {
        "case_id": 34,
        "case_number": "CASE-2026-034",
        "workflow_status": "Triage",
        "reporter_name": "Dr Okello",
        "reporter_email": "okello@example.com",
        "assigned_to_name": "PV Team",
        "status": "Open",
    }
    tasks = [
        {**base, "task_id": 12, "check_code": "event_outcome", "task_title": "Follow up: Event outcome",
         "task_description": "Record the outcome or select Unknown.", "due_date": date(2026, 4, 10)},
        {**base, "task_id": 13, "check_code": "event_onset_date", "task_title": "Follow up: Event onset date",
         "task_description": "Confirm the event onset date.", "due_date": date(2026, 4, 12)},
        {**base, "task_id": 14, "check_code": "follow_up_due_date", "task_title": "Follow up: Follow-up plan",
         "task_description": "Set a due date when follow-up is required.", "due_date": date(2026, 4, 12)},
    ]
    monkeypatch.setattr(
        follow_up_service, "get_open_follow_up_tasks", lambda: tasks
    )
    sent = datetime(2026, 4, 1, 18, 52, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "app.cases.review_routes._last_requests", lambda case_ids: {34: sent}
    )
    monkeypatch.setattr(
        "app.cases.review_routes._reminder_history",
        lambda: {34: {"requested": sent, "reminders": 1, "last_sent": sent,
                      "due": date(2026, 4, 10), "next_reminder": None, "exhausted": False}},
    )
    monkeypatch.setattr("app.cases.review_routes._last_failures", lambda case_ids: {})
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.ensure_overdue_reminders",
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
    assert page.count('class="task-card request-card"') == 1
    assert "To ask the reporter (2)" in page
    assert "For the PV team" in page
    assert "/cases/34/follow-up-request.docx" in page
    assert "Email again" in page
    assert "Download email draft" not in page
    assert "Email + form in Word" not in page
    assert page.count("Not available</button>") == 2
    assert page.count("Enter information</a>") == 2
    assert "section=event-assessment" in page
    assert "next=/cases/follow-up-tasks%23case-34#event-assessment" in page
    assert "Follow-up 2 of 3" in page
    assert "Request emailed 01 Apr 2026, " in page
    assert "Mark done" not in page
    assert 'class="task-status overdue"' in page


def test_overdue_reminders_page_creates_todays_reminders(monkeypatch):
    app = create_app(TestingConfig)
    calls = []
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.ensure_overdue_reminders",
        lambda: calls.append("created") or 1,
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminders",
        lambda: [],
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

    response = client.get("/cases/follow-up-reminders")

    assert response.status_code == 200
    assert calls == ["created"]


def test_ensure_overdue_reminders_never_breaks_the_page(monkeypatch):
    import app.services.case_follow_up_reminders as reminders

    app = create_app(TestingConfig)

    def fail(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(reminders, "create_overdue_reminders", fail)
    monkeypatch.setattr("app.db.get_db", lambda: type("C", (), {"rollback": lambda self: None})())

    with app.app_context():
        assert reminders.ensure_overdue_reminders() == 0
