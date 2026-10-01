from unittest.mock import patch

from app import create_app
from config import Config


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    DEV_AUTO_LOGIN = False


def sign_in(client):
    with client.session_transaction() as session:
        session["user_id"] = 9
        session["username"] = "reviewer"
        session["full_name"] = "PV Reviewer"
        session["role"] = "QPPV"


def test_case_and_complaint_create_forms_enable_guided_voice():
    app = create_app(TestConfig)
    countries = [{"country_id": 1, "country_name": "Uganda"}]

    with app.test_client() as client:
        sign_in(client)
        with (
            patch("app.cases.routes.query_all", return_value=countries),
            patch("app.complaints.routes.query_all", return_value=countries),
            patch(
                "app.services.case_follow_up_reminders.get_open_reminder_count",
                return_value=0,
            ),
        ):
            case_response = client.get("/cases/new")
            complaint_response = client.get("/complaints/new")

    assert case_response.status_code == 200
    assert b'data-guided-voice="true"' in case_response.data
    assert complaint_response.status_code == 200
    assert b'data-guided-voice="true"' in complaint_response.data
    assert b"data-voice-entry" not in case_response.data
    assert b"data-voice-entry" not in complaint_response.data
