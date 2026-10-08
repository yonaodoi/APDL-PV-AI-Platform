from datetime import date

import app.complaints.routes as complaint_routes
from app import create_app
from app.services.complaint_documents import (
    build_complaint_suggestions,
    complaint_document_type_label,
    normalise_complaint_extract,
    typed_complaint_value,
)
from config import TestingConfig


COUNTRIES = [{"country_id": 1, "country_name": "Uganda"}, {"country_id": 2, "country_name": "Kenya"}]
COMPLAINT = {
    "complaint_id": 7,
    "complaint_number": "PQC-2026-007",
    "date_received": date(2026, 9, 20),
    "country_id": 1,
    "reporter_name": None,
    "reporter_contact": None,
    "product_name": "ABPARA",
    "batch_number": None,
    "manufacturing_date": None,
    "expiry_date": None,
    "complaint_category": "Packaging",
    "severity": "Non-serious",
    "complaint_description": "Cracked vial.",
    "linked_case_id": None,
}


def test_normalise_keeps_valid_values_only():
    values = normalise_complaint_extract(
        {
            "country": "kenya",
            "batch_number": " B123 ",
            "expiry_date": "2027-01-31",
            "manufacturing_date": "not a date",
            "severity": "serious",
            "complaint_category": "Mystery",
            "reporter_name": "",
        },
        COUNTRIES,
    )

    assert values == {
        "country_id": "2",
        "batch_number": "B123",
        "expiry_date": "2027-01-31",
        "severity": "Serious",
    }


def test_suggestions_fill_change_and_add():
    values = {
        "batch_number": "B123",
        "country_id": "2",
        "complaint_description": "Second vial also cracked.",
        "product_name": "ABPARA",
    }
    suggestions = build_complaint_suggestions(
        COMPLAINT, values, "reporter follow-up", date(2026, 10, 8), COUNTRIES,
        uncertain=["batch_number"],
    )
    by_field = {s["field"]: s for s in suggestions}

    assert set(by_field) == {"batch_number", "country_id", "complaint_description"}
    assert by_field["batch_number"]["kind"] == "fill"
    assert by_field["batch_number"]["check_reason"]
    assert by_field["country_id"]["kind"] == "change"
    assert by_field["country_id"]["current"] == "Uganda"
    assert by_field["country_id"]["proposed"] == "Kenya"
    assert by_field["complaint_description"]["kind"] == "add"
    assert "[From reporter follow-up, 08 Oct 2026] Second vial" in by_field["complaint_description"]["new_value"]


def test_adverse_event_category_suggestion_is_flagged():
    suggestions = build_complaint_suggestions(
        COMPLAINT, {"complaint_category": "Adverse event"}, "complaint report", date(2026, 10, 8)
    )

    assert "safety case" in suggestions[0]["check_reason"]


def test_typed_values_and_labels():
    assert typed_complaint_value("expiry_date", "2027-01-31") == date(2027, 1, 31)
    assert typed_complaint_value("country_id", "2") == 2
    assert typed_complaint_value("batch_number", "") is None
    assert complaint_document_type_label("complaint_follow_up") == "Reporter follow-up"
    assert complaint_document_type_label(None) == "Other"


def test_apply_refuses_dates_that_do_not_fit(monkeypatch):
    app = create_app(TestingConfig)
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0
    )
    attachment = {
        "attachment_id": 3,
        "original_filename": "follow-up.pdf",
        "document_type": "complaint_follow_up",
        "suggested_updates": [
            {"field": "manufacturing_date", "new_value": "2026-12-01", "label": "Manufacturing date",
             "kind": "fill", "current": "", "proposed": "2026-12-01"},
        ],
    }
    responses = iter([attachment, COMPLAINT])
    monkeypatch.setattr(complaint_routes, "query_one", lambda sql, parameters=(): next(responses))
    applied = []
    monkeypatch.setattr(complaint_routes, "apply_complaint_suggestions", lambda *a: applied.append(a))
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"

    response = client.post(
        "/complaints/7/attachments/3/suggestions",
        data={"action": "apply", "field": "manufacturing_date"},
    )

    assert response.status_code == 302
    assert "/suggestions" in response.headers["Location"]
    assert applied == []
