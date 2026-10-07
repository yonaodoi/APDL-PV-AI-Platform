from werkzeug.security import generate_password_hash

import app.auth.routes as auth_routes
from app import create_app
from config import TestingConfig


def test_login_matches_username_case_insensitively(monkeypatch):
    app = create_app(TestingConfig)
    user = {
        "user_id": 42,
        "username": "AdminUser",
        "full_name": "Admin User",
        "password_hash": generate_password_hash("CorrectHorseBatteryStaple!"),
        "is_active": True,
        "failed_login_attempts": 0,
        "locked_until": None,
        "role_name": "System Administrator",
    }

    seen = {}

    class FakeCursor:
        def execute(self, sql, params):
            seen["update_sql"] = sql
            seen["update_params"] = params

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

    with app.test_client() as client:
        response = client.post(
            "/login",
            data={
                "username": "adminuser",
                "password": "CorrectHorseBatteryStaple!",
            },
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/")
    assert "lower(u.username) = lower(%s)" in seen["lookup_sql"].lower()
    assert seen["lookup_params"] == ("adminuser",)
