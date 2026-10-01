from datetime import date
from unittest.mock import Mock

import pytest
from flask import Flask
from flask_wtf import FlaskForm

from app.cases.routes import _populate_extracted_case_form
from app.cases.forms import SafetyCaseForm
from app.services import case_document_extraction


@pytest.fixture
def flask_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    return app


def test_extract_case_fields_discards_unrecognized_model_keys(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "response": (
            '{"product_name":"Medicine X","event_description":"Rash",'
            '"seriousness":true,"causality_assessment":"Certain",'
            '"uncertain_fields":["product_name"],"made_up":"value"}'
        )
    }
    monkeypatch.setattr(
        case_document_extraction.requests,
        "post",
        lambda *args, **kwargs: response,
    )

    result = case_document_extraction.extract_case_fields(
        "Source report text"
    )

    assert result == {
        "product_name": "Medicine X",
        "event_description": "Rash",
        "uncertain_fields": ["product_name"],
    }


def test_extract_case_fields_rejects_invalid_json(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": "No structured content"}
    monkeypatch.setattr(
        case_document_extraction.requests,
        "post",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(ValueError, match="valid structured"):
        case_document_extraction.extract_case_fields("Source report text")


def test_prefill_accepts_only_known_choices_and_iso_dates(flask_app):
    with flask_app.test_request_context():
        form = SafetyCaseForm()
        form.country_id.choices = [(0, "Select"), (12, "Uganda")]
        uncertain = _populate_extracted_case_form(
            form,
            {
                "country": "Uganda",
                "source": "Patient or consumer",
                "report_type": "Initial",
                "patient_sex": "female",
                "received_date": "2026-09-20",
                "product_name": "Medicine X",
                "event_description": "Rash",
            },
            [{"country_id": 12, "country_name": "Uganda"}],
        )

        assert form.country_id.data == 12
        assert form.patient_sex.data == "Female"
        assert form.received_date.data == date(2026, 9, 20)
        assert form.icsr_case_id.data == ""
        assert form.seriousness.data is False
        assert uncertain == []


def test_prefill_marks_invalid_choice_and_unmatched_country_for_review(
    flask_app,
):
    with flask_app.test_request_context():
        form = SafetyCaseForm()
        form.country_id.choices = [(0, "Select")]
        uncertain = _populate_extracted_case_form(
            form,
            {"country": "Unknown region", "source": "Invented category"},
            [],
        )

        assert form.country_id.data == 0
        assert form.source.data == ""
        assert {
            "country",
            "source",
            "received_date",
            "report_type",
            "product_name",
            "event_description",
        } <= set(uncertain)
