"""Clear error pages, and the completion alerts plumbing."""


def _app():
    from app import create_app
    from config import TestingConfig

    app = create_app(TestingConfig)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    return app


class _PgError(Exception):
    def __init__(self, code, message="db"):
        super().__init__(message)
        self.pgcode = code
        self.diag = type("D", (), {"column_name": "report_number", "constraint_name": "psur_status_check",
                                   "message_detail": "Key (report_number)=(PSUR-1) already exists."})()


def test_database_errors_are_explained_in_plain_words():
    from app.errors import explain_database_error

    assert "date" in explain_database_error(_PgError("22008"))
    assert "duplicate" in explain_database_error(_PgError("23505")) and "PSUR-1" in explain_database_error(_PgError("23505"))
    assert "longer" in explain_database_error(_PgError("22001"))
    assert "migrate.py" in explain_database_error(_PgError("42703"))
    assert explain_database_error(ValueError("x")) is None


def test_a_crash_shows_a_message_not_a_breakdown(monkeypatch):
    app = _app()
    monkeypatch.setattr("app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0)
    monkeypatch.setattr("app.audit.write_audit_log", lambda *a, **k: None)

    @app.post("/boom")
    def boom():
        raise _PgError("22007", "invalid input syntax for type date")

    app.view_functions["boom"].__dict__["csrf_exempt"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    with client.session_transaction() as s:
        s["user_id"], s["full_name"], s["role"] = 1, "Admin", "System Administrator"
    response = client.post("/boom")
    page = response.get_data(as_text=True)
    assert response.status_code == 422
    assert "This could not be saved" in page and "date" in page and "Go back to the form" in page
    assert "Reference for the administrator" in page


def test_failed_logins_say_how_many_attempts_are_left():
    from app.auth.routes import MAX_FAILED_ATTEMPTS

    assert MAX_FAILED_ATTEMPTS >= 3
    page = open("app/templates/auth/login.html").read()
    assert "max_failed_attempts" in page and "lockout_minutes" in page
