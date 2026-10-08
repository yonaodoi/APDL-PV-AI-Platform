from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import app.services.follow_up_automation as automation
from app import create_app
from config import TestingConfig


TODAY = date(2026, 10, 20)
CASE = {
    "case_id": 9,
    "case_number": "APDL-ICSR-26-009",
    "received_date": date(2026, 9, 12),
    "reporter_name": "Dr Okello",
    "reporter_email": "okello@example.com",
    "event_description": "Urticaria",
    "follow_up_due_date": None,
    "created_by": 3,
}
CHECKS = [
    {"code": "event_outcome", "label": "Event outcome", "status": "Review", "message": "Record the outcome."},
    {"code": "patient_consistency", "label": "Patient details consistency", "status": "Review",
     "message": "Recorded age (15 years) does not match the date of birth."},
    {"code": "event_coding", "label": "Event coding", "status": "Review", "message": "Code it."},
]
TASKS = [
    {"task_id": 1, "check_code": "event_outcome", "due_date": date(2026, 10, 1)},
    {"task_id": 2, "check_code": "patient_consistency", "due_date": date(2026, 10, 1)},
    {"task_id": 3, "check_code": "event_coding", "due_date": date(2026, 10, 1)},
]


def _app(**config):
    class Config(TestingConfig):
        pass

    for key, value in config.items():
        setattr(Config, key, value)
    return create_app(Config)


def test_email_is_ready_only_with_a_password_or_gmail_connection(tmp_path):
    app = _app(SMTP_PASSWORD=None, GMAIL_OAUTH_TOKEN_PATH=tmp_path / "token.json")
    with app.app_context():
        assert not automation.email_configured()
        app.config["SMTP_PASSWORD"] = "set"
        assert automation.email_configured()
        app.config["SMTP_PASSWORD"] = None
        (tmp_path / "token.json").write_text("{}")
        assert automation.email_configured()


def test_automatic_requests_hold_back_contradictions():
    items = automation.request_items(CASE, {"product_name": "ABPARA"}, CHECKS, automatic=True)
    manual = automation.request_items(CASE, {"product_name": "ABPARA"}, CHECKS)

    assert [i["code"] for i in items] == ["event_outcome"]
    assert [i["code"] for i in manual] == ["event_outcome", "patient_consistency"]


def test_reply_date_is_never_in_the_past():
    assert automation.reply_due(TASKS, CASE, TODAY, 7) == TODAY + timedelta(days=7)
    later = [{"due_date": date(2026, 11, 30)}]
    assert automation.reply_due(later, CASE, TODAY, 7) == date(2026, 11, 30)


def test_reminder_schedule_and_running_out(monkeypatch):
    sent = datetime(2026, 10, 5, 9, tzinfo=timezone.utc)
    rows = [
        {"case_id": 1, "requested": sent, "reminders": 0, "last_sent": sent, "due": date(2026, 10, 12)},
        {"case_id": 2, "requested": sent, "reminders": 1, "last_sent": datetime(2026, 10, 18, tzinfo=timezone.utc),
         "due": date(2026, 10, 19)},
        {"case_id": 3, "requested": sent, "reminders": 2, "last_sent": sent, "due": date(2026, 10, 12)},
        {"case_id": 4, "requested": sent, "reminders": 0, "last_sent": sent, "due": date(2026, 10, 25)},
    ]
    monkeypatch.setattr(automation, "query_all", lambda sql, parameters=(): rows)

    status = automation.reminder_status(TODAY, reminder_days=7, max_reminders=2)

    assert status[1]["next_reminder"] == date(2026, 10, 13)
    assert status[2]["next_reminder"] == date(2026, 10, 25)
    assert status[3]["exhausted"] and status[3]["next_reminder"] is None
    assert status[4]["next_reminder"] == date(2026, 10, 26)
    assert automation.cases_needing_reminder(TODAY, 7, 2) == [1]


def test_run_does_nothing_until_email_is_set_up(tmp_path):
    app = _app(FOLLOW_UP_AUTO_SEND=True, SMTP_PASSWORD=None,
               GMAIL_OAUTH_TOKEN_PATH=tmp_path / "none.json")
    with app.app_context():
        summary = automation.run_follow_up_automation(app, TODAY)

    assert summary["skipped"] == "Email sending is not set up yet."
    assert summary["requests"] == summary["reminders"] == 0


class _Cursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, parameters=()):
        self.log.append((" ".join(sql.split()), parameters))

    def fetchone(self):
        return (True,)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Connection:
    def __init__(self, log):
        self.log = log

    def cursor(self, *args, **kwargs):
        return _Cursor(self.log)

    def commit(self):
        pass

    def rollback(self):
        pass


def test_run_sends_requests_and_reminders_automatically(monkeypatch):
    app = _app(FOLLOW_UP_AUTO_SEND=True, SMTP_PASSWORD="set")
    log, calls = [], []
    monkeypatch.setattr(automation, "get_db", lambda: _Connection(log))
    monkeypatch.setattr(automation, "cases_needing_request", lambda grace: [9])
    monkeypatch.setattr(automation, "cases_needing_reminder", lambda today, days, most: [12])
    monkeypatch.setattr(automation, "query_one", lambda sql, parameters=(): {"created_by": 3})
    monkeypatch.setattr(
        automation, "send_case_request",
        lambda case_id, actor, **kw: calls.append((case_id, actor, kw["kind"], kw["automatic"]))
        or {"sent": True, "items": [1], "message": "ok", "moved": False},
    )

    with app.app_context():
        summary = automation.run_follow_up_automation(app, TODAY)

    assert calls == [(9, 3, "Request", True), (12, 3, "Reminder", True)]
    assert summary["requests"] == 1 and summary["reminders"] == 1
    assert any("pg_advisory_unlock" in sql for sql, _ in log)


def test_send_records_an_automatic_reminder(monkeypatch):
    app = _app(SMTP_PASSWORD="set")
    log, emails = [], []

    class Tx:
        def __enter__(self):
            return _Cursor(log)

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(automation, "load_case_request",
                        lambda case_id: (CASE, {"product_name": "ABPARA"}, CHECKS, TASKS))
    monkeypatch.setattr(automation, "query_one",
                        lambda sql, parameters=(): {"sent_at": datetime(2026, 10, 5, tzinfo=timezone.utc)})
    monkeypatch.setattr(automation, "transaction", lambda: Tx())
    monkeypatch.setattr("app.services.follow_up_email.send_follow_up_email",
                        lambda app, to, subject, body, attachment, filename: emails.append((to, subject, body)))
    monkeypatch.setattr("app.services.case_follow_up.sync_follow_up_flags", lambda cursor, case_id: 1)
    monkeypatch.setattr("app.services.case_workflow.auto_move_status", lambda *a: False)

    with app.app_context():
        result = automation.send_case_request(9, 3, automatic=True, kind="Reminder", today=TODAY)

    assert result["sent"]
    to, subject, body = emails[0]
    assert to == "okello@example.com"
    assert subject.startswith("Reminder: Follow-up request")
    assert "We wrote to you on 05 Oct 2026" in body
    assert "Patient details" not in body
    deliveries = [p for sql, p in log if "INSERT INTO pv.case_follow_up_email_deliveries" in sql]
    assert deliveries == [(1, 9, "okello@example.com", None, "Reminder", True)]
    due_updates = [p for sql, p in log if "UPDATE pv.case_follow_up_tasks" in sql]
    assert due_updates == [(TODAY + timedelta(days=7), 1)]


def test_scheduler_never_starts_in_tests():
    app = _app(FOLLOW_UP_AUTO_SEND=True)
    assert automation.start_follow_up_scheduler(app) is False
