from unittest.mock import Mock

import pytest

from app.services import ai_case_assessment, llm


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in (
        "AI_PROVIDER",
        "AI_EXTRACTION_PROVIDER",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "OLLAMA_MODEL",
        "OLLAMA_URL",
        "OLLAMA_NUM_CTX",
        "OLLAMA_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_provider_defaults_to_anthropic_only_when_key_is_set(monkeypatch):
    assert llm.provider_for() == "ollama"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    assert llm.provider_for() == "anthropic"
    assert llm.describe_model() == "Claude API: claude-sonnet-5-5"


def test_extraction_can_stay_local_while_assessments_use_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("AI_EXTRACTION_PROVIDER", "ollama")

    assert llm.provider_for(llm.PURPOSE_ASSESSMENT) == "anthropic"
    assert llm.provider_for(llm.PURPOSE_EXTRACTION) == "ollama"


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "other")

    with pytest.raises(llm.AIUnavailableError, match="Unknown AI provider"):
        llm.provider_for()


def test_anthropic_request_and_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-test-model")
    response = Mock(status_code=200)
    response.json.return_value = {
        "content": [{"type": "text", "text": "  Assessment text.  "}]
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(llm.requests, "post", post)

    text = llm.generate_text(
        "Assess this case.",
        system="You are a PV physician.",
        max_tokens=1234,
        json_output=True,
    )

    assert text == "Assessment text."
    url = post.call_args.args[0]
    headers = post.call_args.kwargs["headers"]
    body = post.call_args.kwargs["json"]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "test-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert body["model"] == "claude-test-model"
    assert body["max_tokens"] == 1234
    assert body["system"] == "You are a PV physician."
    assert body["messages"][0]["content"].startswith("Assess this case.")
    assert "single JSON object" in body["messages"][0]["content"]


def test_anthropic_error_is_reported(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "bad-key")
    response = Mock(status_code=401)
    response.json.return_value = {"error": {"message": "invalid x-api-key"}}
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=response))

    with pytest.raises(llm.AIUnavailableError, match="401: invalid x-api-key"):
        llm.generate_text("Prompt")


def test_missing_api_key_is_reported(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "anthropic")

    with pytest.raises(llm.AIUnavailableError, match="ANTHROPIC_API_KEY"):
        llm.generate_text("Prompt")


def test_ollama_request_and_response(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"response": "Local text"}
    post = Mock(return_value=response)
    monkeypatch.setattr(llm.requests, "post", post)

    assert llm.generate_text("Prompt", json_output=True) == "Local text"
    body = post.call_args.kwargs["json"]
    assert body["format"] == "json"
    assert body["model"] == "qwen2.5:7b"
    assert body["options"]["num_ctx"] == 16384
    assert post.call_args.kwargs["timeout"] == 900


def test_case_assessment_sends_no_patient_or_reporter_identifiers(monkeypatch):
    sent = {}

    def fake_generate(prompt, **kwargs):
        sent["prompt"] = prompt
        return "1. Case identification and validity\n\nText."

    monkeypatch.setattr(ai_case_assessment, "generate_text", fake_generate)

    ai_case_assessment.generate_case_assessment(
        {
            "case_number": "APDL-ICSR-26-012",
            "patient_initials": "FY",
            "patient_phone": "0700123456",
            "patient_address": "Plot 4, Kampala Road",
            "reporter_name": "JUSTINE",
            "reporter_email": "jc@example.com",
            "event_description": "Urticaria",
        },
        {"product_name": "ABPARA"},
        None,
        None,
        None,
    )

    prompt = sent["prompt"]
    assert "APDL-ICSR-26-012" in prompt
    assert "Urticaria" in prompt
    for identifier in (
        "FY",
        "0700123456",
        "Kampala Road",
        "JUSTINE",
        "jc@example.com",
    ):
        assert identifier not in prompt


def test_streamed_local_reply_reports_progress(monkeypatch):
    chunks = [
        b'{"response": "1. Case identification", "done": false}',
        b"",
        b'{"response": " and validity", "done": false}',
        b'{"response": "", "done": true}',
    ]
    response = Mock()
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = chunks
    post = Mock(return_value=response)
    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm, "PROGRESS_INTERVAL_SECONDS", 0)
    seen = []

    text = llm.generate_text("Prompt", on_progress=seen.append)

    assert text == "1. Case identification and validity"
    assert post.call_args.kwargs["json"]["stream"] is True
    assert post.call_args.kwargs["stream"] is True
    assert seen[-1] == "1. Case identification and validity"


def test_streamed_local_error_is_reported(monkeypatch):
    response = Mock()
    response.raise_for_status.return_value = None
    response.iter_lines.return_value = [b'{"error": "model not found"}']
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=response))

    with pytest.raises(llm.AIUnavailableError, match="model not found"):
        llm.generate_text("Prompt", on_progress=lambda text: None)


def test_assessment_progress_message_names_current_section():
    partial = (
        "1. Case identification and validity\n\nValid case.\n\n"
        "2. Reported clinical event and chronology\n\nUrticaria began"
    )

    assert ai_case_assessment.progress_message(partial) == (
        "Writing section 2 of 11: Reported clinical event and chronology "
        "(15 words so far)."
    )
    assert ai_case_assessment.progress_message("") == (
        "AI model is starting the assessment (0 words so far)."
    )


def test_assessment_prompt_includes_timing_facts_and_findings(monkeypatch):
    from datetime import date

    sent = {}

    def fake_generate(prompt, **kwargs):
        sent["prompt"] = prompt
        return "report"

    monkeypatch.setattr(ai_case_assessment, "generate_text", fake_generate)

    ai_case_assessment.generate_case_assessment(
        {
            "case_id": 2,
            "created_by": 7,
            "event_description": "Urticaria",
            "event_onset_date": date(2026, 9, 1),
        },
        {"product_name": "ABPARA", "therapy_start_date": date(2026, 9, 14)},
        None,
        None,
        None,
        data_quality_findings=[
            "Death is recorded as a seriousness criterion but the outcome "
            "is Recovered/resolved. Confirm which is correct."
        ],
    )

    prompt = sent["prompt"]
    assert "13 day(s) before the suspected product was started" in prompt
    assert "Death is recorded as a seriousness criterion" in prompt
    assert '"case_id"' not in prompt
    assert '"created_by"' not in prompt


def test_timing_facts_for_event_after_start():
    from datetime import date

    facts = ai_case_assessment.timing_facts(
        {"event_onset_date": date(2026, 9, 16)},
        {"therapy_start_date": date(2026, 9, 14)},
    )

    assert facts == [
        "The event began 2 day(s) after the suspected product was started "
        "(therapy start 2026-09-14, onset 2026-09-16)."
    ]


def test_regulatory_facts_for_serious_unlisted_overdue_case():
    from datetime import date

    facts = ai_case_assessment.regulatory_facts(
        {
            "seriousness": True,
            "received_date": date(2026, 9, 16),
            "workflow_status": "New",
            "regulatory_submitted_date": None,
        },
        {"listedness_status": "Not listed"},
        today=date(2026, 10, 7),
    )

    assert facts[0] == (
        "Seriousness as recorded: serious. Listedness: Not listed."
    )
    assert "meets the criteria for expedited (15-day) reporting" in facts[1]
    assert facts[2] == (
        "Reporting clock: Day 0 16 Sep 2026; 15-day timeline; due "
        "01 Oct 2026; status: Overdue by 6 days."
    )


def test_assessment_prompt_requires_numbered_queries():
    sent = {}

    def fake_generate(prompt, **kwargs):
        sent["prompt"] = prompt
        return "report"

    original = ai_case_assessment.generate_text
    ai_case_assessment.generate_text = fake_generate
    try:
        ai_case_assessment.generate_case_assessment(
            {"event_description": "Rash"}, {}, None, None, None
        )
    finally:
        ai_case_assessment.generate_text = original

    assert '"Query 1:"' in sent["prompt"]
    assert "Never repeat a sentence" in sent["prompt"]
    assert "Regulatory facts" in sent["prompt"]


def test_required_queries_cover_dates_death_birth_and_pregnancy():
    from datetime import date

    queries = ai_case_assessment.required_queries(
        {
            "event_onset_date": date(2026, 9, 1),
            "seriousness_criteria": "DEATH",
            "event_outcome": "Recovered/resolved",
            "patient_date_of_birth": date(2026, 9, 1),
            "patient_age_years": 45,
            "patient_pregnancy_status": "Yes",
        },
        {"product_name": "ABPARA", "therapy_start_date": date(2026, 9, 14)},
    )

    assert queries[0].startswith(
        "Confirm the event onset date and the date ABPARA was first given"
    )
    assert "Confirm whether the patient died" in queries[1]
    assert "01 Sep 2026" in queries[2] and "45 years" in queries[2]
    assert queries[3].startswith("Provide pregnancy details")


def test_prompt_contains_queries_and_no_instruction_text_in_facts(monkeypatch):
    from datetime import date

    sent = {}
    monkeypatch.setattr(
        ai_case_assessment,
        "generate_text",
        lambda prompt, **kwargs: sent.setdefault("prompt", prompt),
    )

    ai_case_assessment.generate_case_assessment(
        {"event_onset_date": date(2026, 9, 1), "patient_pregnancy_status": "Yes"},
        {"product_name": "ABPARA", "therapy_start_date": date(2026, 9, 14)},
        None,
        None,
        None,
    )

    prompt = sent["prompt"]
    assert "Confirm the event onset date and the date ABPARA was first given" in prompt
    assert "The patient is recorded as pregnant." in prompt
    assert "must be addressed in the chronology" not in prompt
    assert 'profession recorded as "SC"' in prompt
