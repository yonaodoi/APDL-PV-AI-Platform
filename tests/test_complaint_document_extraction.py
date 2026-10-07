from datetime import date
from io import BytesIO
from unittest.mock import Mock, patch

import pytest
from flask import Flask

from app import create_app
from app.complaints.forms import ProductComplaintForm
from app.complaints.routes import _populate_complaint_form
from app.services import complaint_document_extraction
from config import Config


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    DEV_AUTO_LOGIN = False


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    return app


def test_extract_complaint_fields_filters_unknown_keys(monkeypatch):
    model_reply = (
        '{"product_name":"Medicine X","severity":"Serious",'
        '"root_cause":"Manufacturing failure",'
        '"uncertain_fields":["severity","root_cause"],'
        '"complaint_number":"invented-id"}'
    )
    monkeypatch.setattr(
        complaint_document_extraction,
        "generate_text",
        lambda *args, **kwargs: model_reply,
    )

    extracted = complaint_document_extraction.extract_complaint_fields(
        "Source complaint report"
    )

    assert extracted == {
        "product_name": "Medicine X",
        "severity": "Serious",
        "uncertain_fields": ["severity"],
    }


def test_extract_complaint_fields_rejects_invalid_response(monkeypatch):
    model_reply = "not JSON"
    monkeypatch.setattr(
        complaint_document_extraction,
        "generate_text",
        lambda *args, **kwargs: model_reply,
    )

    with pytest.raises(ValueError, match="invalid complaint extraction data"):
        complaint_document_extraction.extract_complaint_fields(
            "Source complaint report"
        )


def test_prefill_maps_country_choices_and_iso_dates(flask_app):
    with flask_app.test_request_context():
        form = ProductComplaintForm()
        countries = [{"country_id": 12, "country_name": "Uganda"}]
        form.country_id.choices = [(0, "Select country"), (12, "Uganda")]

        uncertain = _populate_complaint_form(
            form,
            {
                "country": "uganda",
                "date_received": "2026-09-20",
                "manufacturing_date": "2025-02-01",
                "expiry_date": "2027-02-01",
                "complaint_category": "product quality",
                "severity": "Serious",
                "product_name": "Medicine X",
                "complaint_description": "The bottle was leaking.",
            },
            countries,
        )

        assert form.country_id.data == 12
        assert form.date_received.data == date(2026, 9, 20)
        assert form.manufacturing_date.data == date(2025, 2, 1)
        assert form.expiry_date.data == date(2027, 2, 1)
        assert form.complaint_category.data == "Product quality"
        assert form.severity.data == "Serious"
        assert form.complaint_number.data == ""
        assert uncertain == []


def test_prefill_marks_invalid_and_missing_required_fields(flask_app):
    with flask_app.test_request_context():
        form = ProductComplaintForm()
        form.country_id.choices = [(0, "Select country")]

        uncertain = _populate_complaint_form(
            form,
            {
                "country": "Unknown region",
                "date_received": "20 September 2026",
                "complaint_category": "Other-ish",
                "severity": "Critical",
            },
            [],
        )

        assert form.country_id.data == 0
        assert form.date_received.data is None
        assert form.severity.data is None
        assert form.complaint_number.data == ""
        assert {
            "country",
            "date_received",
            "product_name",
            "complaint_category",
            "severity",
            "complaint_description",
        } <= set(uncertain)


def test_uploading_document_returns_unsaved_product_quality_complaint_draft():
    app = create_app(TestConfig)
    countries = [{"country_id": 1, "country_name": "Uganda"}]
    extracted = {
        "country": "Uganda",
        "date_received": "2026-09-20",
        "product_name": "Medicine X",
        "complaint_category": "Product quality",
        "severity": None,
        "complaint_description": "The bottle was leaking.",
        "uncertain_fields": ["severity"],
    }

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 9
            session["username"] = "reviewer"
            session["full_name"] = "PV Reviewer"
            session["role"] = "QPPV"

        with (
            patch("app.complaints.routes.query_all", return_value=countries),
            patch(
                "app.complaints.routes.extract_document_text",
                return_value="Complaint source document",
            ),
            patch(
                "app.complaints.routes.extract_complaint_fields",
                return_value=extracted,
            ),
            patch(
                "app.services.case_follow_up_reminders.get_open_reminder_count",
                return_value=0,
            ),
        ):
            response = client.post(
                "/complaints/new/from-document",
                data={
                    "complaint_document": (
                        BytesIO(b"complaint report"),
                        "quality-complaint.txt",
                    )
                },
                content_type="multipart/form-data",
            )

    assert response.status_code == 200
    assert b"AI-extracted product quality complaint draft" in response.data
    assert b'value=""' in response.data
    assert b"Medicine X" in response.data
    assert b"The bottle was leaking." in response.data
    assert b"severity" in response.data
    assert b"Save product quality complaint" in response.data


def test_product_quality_complaint_document_intake_is_linked_and_available():
    app = create_app(TestConfig)

    with app.test_client() as client:
        with client.session_transaction() as session:
            session["user_id"] = 9
            session["username"] = "reviewer"
            session["full_name"] = "PV Reviewer"
            session["role"] = "QPPV"

        with (
            patch("app.complaints.routes.query_all", return_value=[]),
            patch(
                "app.services.case_follow_up_reminders.get_open_reminder_count",
                return_value=0,
            ),
        ):
            register_response = client.get("/complaints/")
            form_response = client.get("/complaints/new")
            document_response = client.get("/complaints/new/from-document")

    assert register_response.status_code == 200
    assert b"Product Quality Complaints" in register_response.data
    assert b"Create product quality complaint from document" in register_response.data
    assert form_response.status_code == 200
    assert b"Create product quality complaint from document" in form_response.data
    assert document_response.status_code == 200
    assert b"Start with the report. Finish with a reviewed complaint." in document_response.data
    assert b'name="complaint_document"' in document_response.data
