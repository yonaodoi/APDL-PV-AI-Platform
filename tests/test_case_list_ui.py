import app.cases.routes as case_routes
from app import create_app
from config import TestingConfig


def test_case_list_renders_clearable_no_match_state(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(case_routes, "query_all", lambda sql, parameters=(): [])
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session["user_id"] = 1
            user_session["full_name"] = "Test User"
            user_session["role"] = "System Administrator"

        response = client.get("/cases/?product=Missing+product")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'class="case-list-page"' in page
    assert "No matching safety cases" in page
    assert "Clear all filters" in page


def test_seriousness_filter_uses_serious_and_non_serious(monkeypatch):
    app = create_app(TestingConfig)
    queries = []

    def fake_query_all(sql, parameters=()):
        queries.append(sql)
        return []

    monkeypatch.setattr(case_routes, "query_all", fake_query_all)
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session["user_id"] = 1
            user_session["full_name"] = "Test User"
            user_session["role"] = "System Administrator"

        page = client.get("/cases/?priority=Non-serious").get_data(as_text=True)
        old_link = client.get("/cases/?priority=Routine").get_data(as_text=True)

    assert '<label for="priority">Seriousness</label>' in page
    assert '<option value="Non-serious" selected>Non-serious</option>' in page
    assert "Routine" not in page
    assert '<option value="Non-serious" selected>Non-serious</option>' in old_link
    assert any("seriousness = FALSE" in sql for sql in queries)


def test_case_register_folds_open_from_its_heading(monkeypatch):
    app = create_app(TestingConfig)
    rows = [{
        "case_id": 6, "case_number": "APDL-ICSR-26-016", "product_name": "ABPARA",
        "country_name": "Uganda", "event_description": "Rash", "workflow_status": "New",
        "received_date": __import__("datetime").date(2026, 9, 8), "seriousness": False,
    }]
    monkeypatch.setattr(case_routes, "query_all", lambda sql, parameters=(): rows)
    monkeypatch.setattr(case_routes, "query_one", lambda sql, parameters=(): {"count": 0, "overdue_count": 0})
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )

    with app.test_client() as client:
        with client.session_transaction() as user_session:
            user_session["user_id"] = 1
            user_session["full_name"] = "Test User"
            user_session["role"] = "System Administrator"

        plain = client.get("/cases/").get_data(as_text=True)
        filtered = client.get("/cases/?priority=Serious").get_data(as_text=True)

    assert '<details class="case-register fold-card register-fold" id="all-cases" >' in plain
    assert '<summary class="register-toolbar">' in plain
    assert 'id="all-cases" open>' in filtered
