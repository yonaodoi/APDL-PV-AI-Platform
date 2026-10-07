import app.cases.routes as case_routes
from app import create_app
from config import TestingConfig


def test_review_queue_renders_cases_and_reviewer_actions(monkeypatch):
    app = create_app(TestingConfig)
    case = {
        "case_id": 12,
        "case_number": "CASE-2026-012",
        "product_count": 1,
        "workflow_status": "Triage",
        "country_name": "Uganda",
    }
    monkeypatch.setattr(
        case_routes,
        "query_all",
        lambda sql, parameters=(): [case]
        if "FROM pv.safety_cases" in sql
        else [{"case_product_id": 9}],
    )
    monkeypatch.setattr(
        case_routes,
        "refresh_case_completeness",
        lambda case, products: [
            {
                "status": "Review",
                "label": "Event onset date",
                "message": "Confirm the date or document why it is unavailable.",
            },
            {"status": "Pass", "label": "Reporter", "message": "Recorded."},
        ],
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"

    response = client.get("/cases/review-queue")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'aria-label="Cases requiring completeness review"' in page
    assert "CASE-2026-012" in page
    assert "1 item(s) to review" in page
    assert "Confirm the date or document why it is unavailable." in page
    assert "Follow-up tasks" in page


def test_review_queue_has_empty_state_when_no_cases_need_review(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(case_routes, "query_all", lambda sql, parameters=(): [])
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"

    response = client.get("/cases/review-queue")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "No cases need completeness review" in page
    assert "Return to safety cases" in page
