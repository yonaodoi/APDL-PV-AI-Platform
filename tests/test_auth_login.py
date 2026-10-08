from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

from werkzeug.security import generate_password_hash

import app.auth.routes as auth_routes
from app import create_app
from app.security import safe_next_path
from config import TestingConfig


PASSWORD = "CorrectHorseBatteryStaple!"


def _user(**changes):
    user = {
        "user_id": 42,
        "username": "AdminUser",
        "full_name": "Admin User",
        "password_hash": generate_password_hash(PASSWORD),
        "is_active": True,
        "failed_login_attempts": 0,
        "locked_until": None,
        "must_change_password": False,
        "role_name": "System Administrator",
    }
    user.update(changes)
    return user


def _fake_db(monkeypatch, user):
    seen = {"updates": [], "audit": []}

    class FakeCursor:
        def execute(self, sql, params):
            seen["updates"].append((sql, params))

    class FakeTransaction:
        def __enter__(self):
            return FakeCursor()

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def fake_query_one(sql, params=()):
        seen["lookup_sql"] = sql
        seen["lookup_params"] = params
        return user

    monkeypatch.setattr(auth_routes, "query_one", fake_query_one)
    monkeypatch.setattr(auth_routes, "transaction", lambda: FakeTransaction())
    monkeypatch.setattr(
        auth_routes,
        "write_audit_log",
        lambda **kwargs: seen["audit"].append(kwargs),
    )
    return seen


def test_login_matches_username_case_insensitively(monkeypatch):
    app = create_app(TestingConfig)
    seen = _fake_db(monkeypatch, _user())

    with app.test_client() as client:
        response = client.post(
            "/login",
            data={"username": "adminuser", "password": PASSWORD},
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "lower(u.username) = lower(%s)" in seen["lookup_sql"].lower()
    assert seen["lookup_params"] == ("adminuser",)
    assert seen["audit"][-1]["action"] == "Signed in"
    assert seen["audit"][-1]["record_id"] == 42


def test_login_returns_to_the_requested_page(monkeypatch):
    app = create_app(TestingConfig)
    _fake_db(monkeypatch, _user())

    with app.test_client() as client:
        response = client.post(
            "/login?next=/cases/12?tab=events",
            data={"username": "adminuser", "password": PASSWORD},
        )

    assert response.headers["Location"].endswith("/cases/12?tab=events")


def test_login_ignores_next_links_to_other_websites(monkeypatch):
    app = create_app(TestingConfig)
    _fake_db(monkeypatch, _user())

    with app.test_client() as client:
        response = client.post(
            "/login?next=//evil.example.com/",
            data={"username": "adminuser", "password": PASSWORD},
        )

    assert "evil" not in response.headers["Location"]


def test_wrong_password_is_recorded_in_audit_trail(monkeypatch):
    app = create_app(TestingConfig)
    seen = _fake_db(monkeypatch, _user(failed_login_attempts=1))

    with app.test_client() as client:
        response = client.post(
            "/login",
            data={"username": "adminuser", "password": "wrong-password"},
        )

    assert response.status_code == 200
    event = seen["audit"][-1]
    assert event["action"] == "Failed sign-in attempt"
    assert "attempt 2 of 5" in event["details"]
    assert "wrong-password" not in event["details"]


def test_fifth_wrong_password_locks_account_and_is_recorded(monkeypatch):
    app = create_app(TestingConfig)
    seen = _fake_db(monkeypatch, _user(failed_login_attempts=4))

    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "adminuser", "password": "wrong-password"},
        )

    assert seen["audit"][-1]["action"] == "Account locked"
    assert seen["updates"][-1][1][1] is not None


def test_locked_account_is_refused_and_recorded(monkeypatch):
    app = create_app(TestingConfig)
    locked = datetime.now(timezone.utc) + timedelta(minutes=10)
    seen = _fake_db(monkeypatch, _user(locked_until=locked))

    with app.test_client() as client:
        page = client.post(
            "/login",
            data={"username": "adminuser", "password": PASSWORD},
        ).get_data(as_text=True)

    assert "temporarily locked" in page
    assert seen["audit"][-1]["action"] == "Sign-in refused: account locked"


def test_unknown_username_is_recorded(monkeypatch):
    app = create_app(TestingConfig)
    seen = _fake_db(monkeypatch, None)

    with app.test_client() as client:
        client.post(
            "/login",
            data={"username": "nobody", "password": "whatever"},
        )

    assert seen["audit"][-1]["action"] == "Failed sign-in attempt"
    assert seen["audit"][-1]["record_id"] is None
    assert "nobody" in seen["audit"][-1]["details"]


def test_temporary_password_forces_password_change(monkeypatch):
    app = create_app(TestingConfig)
    _fake_db(monkeypatch, _user(must_change_password=True))
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    with app.test_client() as client:
        response = client.post(
            "/login?next=/cases/",
            data={"username": "adminuser", "password": PASSWORD},
        )
        assert response.headers["Location"].endswith("/change-password")

        # Any other page sends the user back to change the password.
        response = client.get("/cases/")
        assert response.status_code == 302
        assert response.headers["Location"].endswith("/change-password")

        page = client.get("/change-password").get_data(as_text=True)
        assert "temporary password" in page
        assert "cancel-link\" href" not in page


def test_sign_in_audit_failure_does_not_block_sign_in(monkeypatch):
    app = create_app(TestingConfig)
    _fake_db(monkeypatch, _user())

    def broken_audit(**kwargs):
        raise RuntimeError("audit table unavailable")

    monkeypatch.setattr(auth_routes, "write_audit_log", broken_audit)
    monkeypatch.setattr(auth_routes, "get_db", lambda: None)

    with app.test_client() as client:
        response = client.post(
            "/login",
            data={"username": "adminuser", "password": PASSWORD},
        )

    assert response.status_code == 302


def test_protected_page_redirects_to_sign_in_with_next():
    app = create_app(TestingConfig)

    with app.test_client() as client:
        response = client.get("/cases/?status=New")

    location = urlsplit(response.headers["Location"])
    assert location.path == "/login"
    assert parse_qs(location.query)["next"] == ["/cases/?status=New"]


def test_safe_next_path_accepts_only_local_paths():
    assert safe_next_path("/cases/5") == "/cases/5"
    assert safe_next_path("/cases/?") == "/cases/"
    assert safe_next_path("https://evil.example.com/") is None
    assert safe_next_path("//evil.example.com/") is None
    assert safe_next_path("/\\evil.example.com") is None
    assert safe_next_path("cases") is None
    assert safe_next_path(None) is None
