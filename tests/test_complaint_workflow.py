from datetime import date

import app.complaints.routes as complaint_routes
from app import create_app
from app.services.complaint_rules import validate_complaint_update
from app.services.complaint_workflow import (
    closure_blockers,
    closure_checks,
    draft_investigation_note,
    has_draft_placeholders,
)
from config import TestingConfig


COMPLAINT = {
    "complaint_id": 7,
    "complaint_number": "PQC-2026-007",
    "date_received": date(2026, 9, 20),
    "country_id": 1,
    "country_name": "Uganda",
    "reporter_name": "Pharmacist A",
    "reporter_contact": "pharmacist@example.com",
    "product_name": "ABPARA",
    "batch_number": "B123",
    "manufacturing_date": None,
    "expiry_date": date(2027, 1, 31),
    "complaint_category": "Packaging",
    "complaint_description": "Cracked vial found in the carton.",
    "severity": "Non-serious",
    "status": "Under investigation",
    "investigation_summary": None,
    "corrective_action": None,
    "closure_date": None,
    "created_by_name": "Test User",
    "linked_case_id": None,
    "linked_case_number": None,
}


def _codes(checks, ok=None):
    return [c["code"] for c in checks if ok is None or c["ok"] == ok]


def test_missing_summary_blocks_closure():
    checks = closure_checks(COMPLAINT)

    assert closure_blockers(checks) == [
        "Investigation summary: Record the findings and root cause."
    ]


def test_non_serious_with_summary_is_ready():
    checks = closure_checks({**COMPLAINT, "investigation_summary": "Supplier crack."})

    assert closure_blockers(checks) == []
    assert "capa" in _codes(checks, ok=True)


def test_serious_needs_capa_and_adverse_event_needs_case():
    complaint = {
        **COMPLAINT,
        "severity": "Serious",
        "complaint_category": "Adverse event",
        "investigation_summary": "Rash after infusion; batch tested within spec.",
    }
    blockers = closure_blockers(closure_checks(complaint))

    assert any(b.startswith("Corrective action") for b in blockers)
    assert any(b.startswith("Safety case") for b in blockers)

    linked = {**complaint, "corrective_action": "No action: batch in spec.",
              "linked_case_id": 3, "linked_case_number": "APDL-ICSR-26-003"}
    assert closure_blockers(closure_checks(linked)) == []


def test_date_warnings_and_open_batch_are_notes_not_blockers():
    checks = closure_checks(
        {**COMPLAINT, "investigation_summary": "Done."},
        date_warnings=["Expired before receipt."],
        same_batch=[{"complaint_number": "PQC-1", "status": "Under investigation"}],
    )

    assert set(_codes(checks, ok=False)) == {"dates", "batch"}
    assert closure_blockers(checks) == []


def test_drafted_note_states_record_facts_and_leaves_gaps():
    note = draft_investigation_note(
        COMPLAINT,
        same_batch=[{"complaint_number": "PQC-2026-001"}],
        attachment_count=2,
        today=date(2026, 10, 8),
    )

    assert "PQC-2026-007 received 20 Sep 2026" in note
    assert "batch B123" in note
    assert "PQC-2026-001" in note
    assert "2 attached" in note
    assert has_draft_placeholders(note)


def test_unedited_draft_cannot_close_the_investigation():
    note = draft_investigation_note(COMPLAINT, today=date(2026, 10, 8))

    assert "summary" in _codes(closure_checks({**COMPLAINT, "investigation_summary": note}), ok=False)
    errors = validate_complaint_update(
        COMPLAINT, "Investigation complete", note, "", date(2026, 10, 1),
        today=date(2026, 10, 8),
    )
    assert any("bracketed" in e for e in errors)


def test_adverse_event_complaint_cannot_close_without_case():
    errors = validate_complaint_update(
        {**COMPLAINT, "complaint_category": "Adverse event"},
        "Investigation complete", "Reviewed.", "", date(2026, 10, 1),
        today=date(2026, 10, 8),
    )

    assert any("safety case" in e for e in errors)


def _client(monkeypatch, complaint):
    app = create_app(TestingConfig)
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )
    monkeypatch.setattr(
        complaint_routes, "query_one", lambda sql, parameters=(): complaint
    )
    monkeypatch.setattr(
        complaint_routes, "query_all", lambda sql, parameters=(): []
    )
    monkeypatch.setattr(
        complaint_routes, "list_record_attachments", lambda *args: []
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"
    return client


def test_detail_page_shows_ready_to_close_panel(monkeypatch):
    ready = {**COMPLAINT, "investigation_summary": "Supplier crack confirmed."}
    page = _client(monkeypatch, ready).get("/complaints/7").get_data(as_text=True)

    assert "Ready to close" in page
    assert "Mark investigation complete" in page
    assert "Drafted investigation note" in page


def test_detail_page_hides_close_button_when_blocked(monkeypatch):
    page = _client(monkeypatch, COMPLAINT).get("/complaints/7").get_data(as_text=True)

    assert "Ready to close? Not yet" in page
    assert "Mark investigation complete" not in page


def test_close_route_refuses_when_rules_fail(monkeypatch):
    client = _client(monkeypatch, COMPLAINT)
    calls = []
    monkeypatch.setattr(complaint_routes, "transaction", lambda: calls.append(1))

    response = client.post("/complaints/7/close", data={"closure_date": "2026-10-01"})

    assert response.status_code == 302
    assert "#investigation-panel" in response.headers["Location"]
    assert calls == []
