from datetime import date

import app.cases.routes as case_routes
import app.complaints.routes as complaint_routes
from app import create_app
from config import TestingConfig


COMPLAINT = {
    "complaint_id": 5,
    "complaint_number": "PQC-2026-005",
    "date_received": date(2026, 9, 20),
    "country_id": 1,
    "country_name": "Uganda",
    "reporter_name": "Pharmacist A",
    "reporter_contact": "pharmacist@example.com",
    "product_name": "ABPARA",
    "batch_number": "B123",
    "manufacturing_date": None,
    "expiry_date": date(2027, 1, 31),
    "complaint_category": "Adverse event",
    "complaint_description": "Patient developed a rash after the infusion.",
    "severity": "Serious",
    "status": "Under investigation",
    "investigation_summary": None,
    "corrective_action": None,
    "closure_date": None,
    "created_by_name": "Test User",
    "linked_case_id": None,
    "linked_case_number": None,
}


def _client(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count",
        lambda: 0,
    )
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"
    return client


def test_adverse_event_complaint_without_case_shows_create_button(monkeypatch):
    monkeypatch.setattr(
        complaint_routes, "query_one", lambda sql, parameters=(): COMPLAINT
    )
    monkeypatch.setattr(
        complaint_routes, "query_all", lambda sql, parameters=(): []
    )
    client = _client(monkeypatch)

    page = client.get("/complaints/5").get_data(as_text=True)

    assert "This complaint reports an adverse event" in page
    assert "/cases/new?from_complaint=5" in page
    assert "20 Sep 2026" in page


def test_linked_adverse_event_complaint_shows_case_link(monkeypatch):
    linked = {**COMPLAINT, "linked_case_id": 9, "linked_case_number": "APDL-ICSR-26-020"}
    monkeypatch.setattr(
        complaint_routes, "query_one", lambda sql, parameters=(): linked
    )
    monkeypatch.setattr(
        complaint_routes, "query_all", lambda sql, parameters=(): []
    )
    client = _client(monkeypatch)

    page = client.get("/complaints/5").get_data(as_text=True)

    assert "Adverse event processed as a safety case" in page
    assert "APDL-ICSR-26-020" in page
    assert "from_complaint=5" not in page


def test_quality_complaint_shows_no_adverse_event_banner(monkeypatch):
    quality = {**COMPLAINT, "complaint_category": "Packaging"}
    monkeypatch.setattr(
        complaint_routes, "query_one", lambda sql, parameters=(): quality
    )
    monkeypatch.setattr(
        complaint_routes, "query_all", lambda sql, parameters=(): []
    )
    client = _client(monkeypatch)

    page = client.get("/complaints/5").get_data(as_text=True)

    assert "reports an adverse event" not in page


def test_new_case_form_is_prefilled_from_complaint(monkeypatch):
    monkeypatch.setattr(
        case_routes,
        "query_all",
        lambda sql, parameters=(): [{"country_id": 1, "country_name": "Uganda"}],
    )
    monkeypatch.setattr(
        case_routes, "query_one", lambda sql, parameters=(): COMPLAINT
    )
    client = _client(monkeypatch)

    page = client.get("/cases/new?from_complaint=5").get_data(as_text=True)

    assert "Creating a safety case from product complaint PQC-2026-005" in page
    assert 'name="from_complaint_id" value="5"' in page
    assert 'value="2026-09-20"' in page
    assert 'value="ABPARA"' in page
    assert 'value="B123"' in page
    assert 'value="pharmacist@example.com"' in page
    assert "Patient developed a rash after the infusion." in page


def test_prefill_helper_puts_phone_contact_in_phone_field():
    class Field:
        data = None

    class Form:
        def __getattr__(self, name):
            field = Field()
            setattr(self, name, field)
            return field

    form = Form()
    case_routes._prefill_case_from_complaint(
        form, {**COMPLAINT, "reporter_contact": "0700 123456"}
    )

    assert form.reporter_phone.data == "0700 123456"
    assert form.reporter_email.data is None
    assert form.received_date.data == date(2026, 9, 20)
    assert form.source.data == "Other"
