from datetime import date

import app.signals.routes as signal_routes
from app import create_app
from app.services.signal_workflow import (
    draft_signal_assessment_note,
    evaluation_checks,
    has_draft_placeholders,
    suggest_signal_status,
    validate_signal_evaluation,
)
from config import TestingConfig


SIGNAL = {
    "signal_id": 8,
    "signal_number": "SIG-2026-008",
    "date_detected": date(2026, 9, 20),
    "product_name": "ABPARA",
    "event_term": "Rash",
    "signal_source": "ICSR review",
    "priority": "High",
    "status": "New",
    "owner_name": None,
    "created_by_name": "Reviewer",
    "signal_description": "Cluster of rash reports.",
    "assessment_summary": None,
    "decision_summary": None,
}
CASES = [
    {"case_id": 1, "case_number": "26-001", "received_date": date(2026, 8, 1),
     "seriousness": True, "listedness_status": "Not listed",
     "country_name": "Uganda", "event_description": "Severe rash", "event_outcome": "Recovered"},
    {"case_id": 2, "case_number": "26-004", "received_date": date(2026, 9, 2),
     "seriousness": False, "listedness_status": None,
     "country_name": "Kenya", "event_description": "Mild rash", "event_outcome": "Fatal"},
]


def test_new_signal_suggests_starting_evaluation():
    suggestion = suggest_signal_status(SIGNAL, CASES)

    assert suggestion["status"] == "Under evaluation"
    assert "2 supporting case(s) linked." in suggestion["reasons"]


def test_assessed_signal_offers_validated_or_closed():
    signal = {**SIGNAL, "status": "Under evaluation", "assessment_summary": "Plausible."}

    assert suggest_signal_status(signal, CASES)["choices"] == ("Validated", "Closed")


def test_validated_with_decision_suggests_closing_and_closed_has_none():
    signal = {**SIGNAL, "status": "Validated", "decision_summary": "Update label."}

    assert suggest_signal_status(signal)["status"] == "Closed"
    assert suggest_signal_status({**signal, "status": "Closed"}) is None


def test_closing_needs_owner_assessment_and_decision():
    errors = validate_signal_evaluation("Closed", SIGNAL)

    assert len(errors) == 1
    assert "Signal owner" in errors[0]
    assert "Decision and action" in errors[0]
    ok = {**SIGNAL, "owner_name": "PV", "assessment_summary": "Done.", "decision_summary": "None."}
    assert validate_signal_evaluation("Closed", ok) == []
    assert validate_signal_evaluation("Under evaluation", SIGNAL) == []


def test_drafted_assessment_counts_cases_and_keeps_gaps():
    note = draft_signal_assessment_note(SIGNAL, CASES, today=date(2026, 10, 8))

    assert "Supporting cases: 2 (1 serious, 1 non-serious)" in note
    assert "01 Aug 2026 to 02 Sep 2026" in note
    assert "Not assessed 1" in note and "Not listed 1" in note
    assert "Fatal outcome: 26-004." in note
    assert has_draft_placeholders(note)
    signal = {**SIGNAL, "owner_name": "PV", "assessment_summary": note}
    assert validate_signal_evaluation("Validated", signal)


def test_checks_flag_cases_without_listedness():
    checks = evaluation_checks(SIGNAL, CASES)

    case_check = [c for c in checks if c["label"] == "Supporting cases"][0]
    assert not case_check["ok"]
    assert "1 without a listedness assessment" in case_check["message"]


def _client(monkeypatch, signal, cases=()):
    app = create_app(TestingConfig)
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )
    monkeypatch.setattr(signal_routes, "query_one", lambda sql, parameters=(): signal)
    monkeypatch.setattr(signal_routes, "query_all", lambda sql, parameters=(): list(cases))
    monkeypatch.setattr(signal_routes, "list_record_attachments", lambda *args: [])
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"
    return client


def test_detail_page_shows_suggestion_and_draft(monkeypatch):
    page = _client(monkeypatch, SIGNAL).get("/signals/8").get_data(as_text=True)

    assert "Suggested next status: Under evaluation" in page
    assert "Move to Under evaluation" in page
    assert "Drafted assessment" in page


def test_status_route_refuses_closing_without_decision(monkeypatch):
    client = _client(monkeypatch, {**SIGNAL, "status": "Under evaluation"})
    calls = []
    monkeypatch.setattr(signal_routes, "transaction", lambda: calls.append(1))

    response = client.post("/signals/8/status", data={"status": "Closed"})

    assert response.status_code == 302
    assert "#evaluation-panel" in response.headers["Location"]
    assert calls == []
