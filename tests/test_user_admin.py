from datetime import datetime, timedelta, timezone

import app.administration.routes as admin_routes
from app import create_app
from config import TestingConfig


USER = {
    "user_id": 7,
    "username": "henry.okedi",
    "full_name": "Henry Okedi",
    "email": "henry@example.com",
    "role_id": 3,
    "is_active": True,
    "is_designated_qppv": False,
    "failed_login_attempts": 5,
    "locked_until": datetime.now(timezone.utc) + timedelta(minutes=10),
    "last_login_at": None,
    "must_change_password": False,
    "role_name": "Regulatory Affairs Officer",
}


def _client(monkeypatch, user=USER, user_id=1):
    seen = {"updates": [], "audit": []}

    class Cursor:
        def execute(self, sql, params):
            seen["updates"].append((" ".join(sql.split()), params))

    class Tx:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *args):
            return False

    def query_one(sql, params=()):
        if "FROM pv.roles WHERE role_id" in " ".join(sql.split()):
            return {"role_id": params[0], "role_name": "PV Officer"}
        return dict(user)

    monkeypatch.setattr(admin_routes, "query_one", query_one)
    monkeypatch.setattr(
        admin_routes,
        "query_all",
        lambda sql, params=(): [
            {"role_id": 3, "role_name": "Regulatory Affairs Officer"},
            {"role_id": 4, "role_name": "PV Officer"},
        ],
    )
    monkeypatch.setattr(admin_routes, "transaction", lambda: Tx())
    monkeypatch.setattr(
        admin_routes,
        "write_audit_log",
        lambda **kwargs: seen["audit"].append(kwargs),
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = user_id
        user_session["full_name"] = "Admin"
        user_session["role"] = "System Administrator"
    return client, seen


def test_manage_user_page_shows_lock_and_tools(monkeypatch):
    client, _ = _client(monkeypatch)

    page = client.get("/administration/users/7").get_data(as_text=True)

    assert "Henry Okedi" in page
    assert "Locked until" in page
    assert "Unlock account" in page
    assert "Save role" in page
    assert "Set temporary password" in page


def test_reset_password_sets_temporary_password_and_audits(monkeypatch):
    client, seen = _client(monkeypatch)

    client.post(
        "/administration/users/7/reset-password",
        data={"new_password": "Temporary-123", "confirm_password": "Temporary-123"},
    )

    sql, params = seen["updates"][-1]
    assert "must_change_password = TRUE" in sql
    assert "locked_until = NULL" in sql
    assert params[1] == 7
    assert params[0] != "Temporary-123"
    assert seen["audit"][-1]["action"] == "Password reset by administrator"
    assert "Temporary-123" not in seen["audit"][-1]["details"]


def test_reset_password_rejects_short_or_mismatched_passwords(monkeypatch):
    client, seen = _client(monkeypatch)

    client.post(
        "/administration/users/7/reset-password",
        data={"new_password": "short", "confirm_password": "short"},
    )
    client.post(
        "/administration/users/7/reset-password",
        data={"new_password": "Temporary-123", "confirm_password": "Different-123"},
    )

    assert seen["updates"] == []
    assert seen["audit"] == []


def test_admin_cannot_reset_own_password_or_change_own_role(monkeypatch):
    client, seen = _client(monkeypatch, user={**USER, "user_id": 1}, user_id=1)

    client.post(
        "/administration/users/1/reset-password",
        data={"new_password": "Temporary-123", "confirm_password": "Temporary-123"},
    )
    client.post("/administration/users/1/role", data={"role_id": "4"})

    assert seen["updates"] == []


def test_change_role_updates_and_audits(monkeypatch):
    client, seen = _client(monkeypatch)

    client.post("/administration/users/7/role", data={"role_id": "4"})

    assert seen["updates"][-1][1] == (4, 7)
    assert "from Regulatory Affairs Officer to PV Officer" in seen["audit"][-1]["details"]


def test_unlock_clears_lock_and_audits(monkeypatch):
    client, seen = _client(monkeypatch)

    client.post("/administration/users/7/unlock")

    assert "locked_until = NULL" in seen["updates"][-1][0]
    assert seen["audit"][-1]["action"] == "Account unlocked"
