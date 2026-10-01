from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock, patch

from app import create_app
from config import Config


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    DEV_AUTO_LOGIN = False


def create_test_app():
    return create_app(TestConfig)


def sign_in(client):
    with client.session_transaction() as session:
        session["user_id"] = 9
        session["username"] = "reviewer"
        session["full_name"] = "PV Reviewer"
        session["role"] = "QPPV"


def test_manual_signal_entry_form_is_reachable_and_has_voice_control():
    app = create_test_app()

    with app.test_client() as client:
        sign_in(client)
        with patch(
            "app.services.case_follow_up_reminders.get_open_reminder_count",
            return_value=0,
        ):
            response = client.get("/signals/new")

    assert response.status_code == 200
    assert b"Capture safety signal" in response.data
    assert b'data-guided-voice="true"' in response.data
    assert b"name=\"signal_description\"" in response.data


def test_signal_evaluation_does_not_enable_guided_voice_entry():
    app = create_test_app()
    signal = {
        "signal_id": 2,
        "signal_number": "SIG-2",
        "date_detected": date.today(),
        "product_name": "Medicine X",
        "event_term": "Fever",
        "signal_source": "ICSR review",
        "signal_description": "A potential signal.",
        "priority": "Medium",
        "status": "Under evaluation",
        "assessment_summary": None,
        "decision_summary": None,
        "owner_name": None,
        "created_by_name": "PV Reviewer",
    }

    with app.test_client() as client:
        sign_in(client)
        with (
            patch("app.signals.routes.query_one", return_value=signal),
            patch("app.signals.routes.query_all", return_value=[]),
            patch(
                "app.services.case_follow_up_reminders.get_open_reminder_count",
                return_value=0,
            ),
        ):
            response = client.get("/signals/2")

    assert response.status_code == 200
    assert b"assessment_summary" in response.data
    assert b'data-guided-voice="true"' not in response.data


def test_manual_signal_submission_creates_non_automated_signal():
    app = create_test_app()
    cursor = Mock()
    form_data = {
        "signal_number": "APDL-SIG-2026-TEST",
        "date_detected": date.today().isoformat(),
        "product_name": "Medicine X",
        "event_term": "Reported fever",
        "signal_source": "Literature",
        "priority": "Medium",
        "owner_name": "PV Reviewer",
        "signal_description": "Published reports describe reported fever.",
        "submit": "Save safety signal",
    }

    with app.test_client() as client:
        sign_in(client)
        with (
            patch("app.signals.routes.query_one", return_value=None),
            patch(
                "app.signals.routes.transaction",
                return_value=nullcontext(cursor),
            ),
        ):
            response = client.post("/signals/new", data=form_data)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/signals/")
    statement, parameters = cursor.execute.call_args.args
    assert "auto_detected" in statement
    assert "FALSE" in statement
    assert parameters == (
        "APDL-SIG-2026-TEST",
        date.today(),
        "Medicine X",
        "Reported fever",
        "Literature",
        "Published reports describe reported fever.",
        "Medium",
        "PV Reviewer",
        9,
    )


def test_manual_signal_entry_rejects_duplicate_signal_id():
    app = create_test_app()
    form_data = {
        "signal_number": "APDL-SIG-2026-DUP",
        "date_detected": date.today().isoformat(),
        "product_name": "Medicine X",
        "event_term": "Reported fever",
        "signal_source": "Literature",
        "priority": "Medium",
        "signal_description": "Published reports describe reported fever.",
        "submit": "Save safety signal",
    }

    with app.test_client() as client:
        sign_in(client)
        with (
            patch("app.signals.routes.query_one", return_value={"signal_id": 3}),
            patch("app.services.case_follow_up_reminders.get_open_reminder_count",
                  return_value=0),
        ):
            response = client.post("/signals/new", data=form_data)

    assert response.status_code == 200
    assert b"This Signal ID already exists." in response.data
