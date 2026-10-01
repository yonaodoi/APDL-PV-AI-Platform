from datetime import date
from unittest.mock import Mock

import pytest

from app.services import safety_signal_assistance


def test_draft_uses_limited_signal_context_and_filters_response(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "response": (
            '{"draft_assessment":"Two linked reports record the event.",'
            '"evidence_gaps":["Confirm the source data."],'
            '"decision":"Validate the signal",'
            '"priority":"Critical"}'
        )
    }
    request = Mock(return_value=response)
    monkeypatch.setattr(safety_signal_assistance.requests, "post", request)

    result = safety_signal_assistance.draft_safety_signal_assessment(
        {
            "signal_number": "SIG-12",
            "date_detected": date(2026, 2, 1),
            "product_name": "Medicine X",
            "event_term": "Fever",
            "signal_source": "ICSR review",
            "signal_description": "System-detected potential signal.",
        },
        [
            {
                "case_number": "CASE-1",
                "received_date": date(2026, 1, 2),
                "country_name": "Uganda",
                "seriousness": False,
                "event_description": "Fever after use.",
                "patient_name": "Must not be sent",
            }
        ],
    )

    prompt = request.call_args.kwargs["json"]["prompt"]
    assert "Must not be sent" not in prompt
    assert "Do not assess causality" in prompt
    assert result == {
        "draft_assessment": "Two linked reports record the event.",
        "evidence_gaps": ["Confirm the source data."],
    }


@pytest.mark.parametrize(
    "response_text, message",
    [
        ("not JSON", "invalid safety signal draft data"),
        ('{"draft_assessment":"","evidence_gaps":[]}', "did not return"),
        (
            '{"draft_assessment":"Summary","evidence_gaps":"not a list"}',
            "invalid evidence-gap",
        ),
    ],
)
def test_assistance_parser_rejects_invalid_output(response_text, message):
    with pytest.raises(ValueError, match=message):
        safety_signal_assistance._parse_assistance_response(response_text)


def test_assistance_limits_number_and_length_of_supporting_cases(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "response": '{"draft_assessment":"Summary","evidence_gaps":[]}'
    }
    request = Mock(return_value=response)
    monkeypatch.setattr(safety_signal_assistance.requests, "post", request)
    cases = [
        {
            "case_number": f"CASE-{index}",
            "event_description": "e" * 1200,
        }
        for index in range(safety_signal_assistance.MAX_SUPPORTING_CASES + 1)
    ]

    safety_signal_assistance.draft_safety_signal_assessment({}, cases)

    prompt = request.call_args.kwargs["json"]["prompt"]
    assert '"supporting_case_count": 51' in prompt
    assert '"included_case_count": 50' in prompt
    assert prompt.count('"case_number":') == 50
    assert "e" * 801 not in prompt
