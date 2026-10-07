import app.cases.routes as case_routes
from app import create_app
from config import TestingConfig


def test_create_case_form_has_section_navigation_and_associated_labels(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(
        case_routes,
        "query_all",
        lambda sql, parameters=(): [
            {"country_id": 1, "country_name": "Uganda"}
        ],
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session["user_id"] = 1
            user_session["full_name"] = "Test User"
            user_session["role"] = "System Administrator"

        response = client.get("/cases/new")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<nav class="progress" aria-label="Form sections">' in page
    assert 'href="#report-details"' in page
    assert 'id="event-assessment"' in page
    assert '<label for="icsr_case_id">' in page
    assert 'id="icsr_case_id" name="icsr_case_id"' in page
    assert "Official ADR form guide" in page
