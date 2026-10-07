from flask import render_template

from app import create_app
from config import TestingConfig


def test_shared_layout_exposes_accessible_navigation(monkeypatch):
    app = create_app(TestingConfig)
    app.add_url_rule(
        "/_layout-test",
        endpoint="layout_test",
        view_func=lambda: render_template("base.html"),
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 4,
    )

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session["user_id"] = 1
            user_session["full_name"] = "Test User"
            user_session["role"] = "System Administrator"

        response = client.get("/_layout-test")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<a class="skip-link" href="#main-content">' in page
    assert '<nav class="primary-nav" aria-label="Primary navigation">' in page
    assert '<main class="content" id="main-content" tabindex="-1">' in page
    assert 'aria-label="4 open reminders"' in page
