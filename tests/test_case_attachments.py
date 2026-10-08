import app.attachments.routes as attachment_routes
from app import create_app
from config import TestingConfig


def _client(monkeypatch):
    monkeypatch.setattr(
        attachment_routes, "query_one", lambda sql, params=(): {"case_id": 9}
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0
    )
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"
    return client


def test_upload_from_case_page_returns_to_the_case(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/records/case/9/attachments",
        data={"return_to": "/cases/9#attachments"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/cases/9#attachments")


def test_upload_ignores_return_links_to_other_websites(monkeypatch):
    client = _client(monkeypatch)

    response = client.post(
        "/records/case/9/attachments",
        data={"return_to": "//evil.example.com/"},
    )

    assert "evil" not in response.headers["Location"]
    assert response.headers["Location"].endswith("/records/case/9/attachments")


def test_edited_section_returns_to_the_same_section_on_the_case_page():
    import app.cases.routes as case_routes

    assert case_routes._case_page_anchor("report-details") == "#case-receipt"
    assert case_routes._case_page_anchor("event-assessment") == "#case-event"
    assert case_routes._case_page_anchor("javascript:alert(1)") == ""
    assert case_routes._case_page_anchor(None) == ""
