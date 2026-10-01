from contextlib import nullcontext
from datetime import date
from unittest.mock import Mock, patch

import pytest

from app import create_app
from app.services import psur_builder_ai
from config import Config


class TestConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    DEV_AUTO_LOGIN = False


def create_test_app():
    return create_app(TestConfig)


def sign_in(client):
    with client.session_transaction() as session:
        session["user_id"] = 9
        session["username"] = "reviewer"
        session["full_name"] = "PV Reviewer"
        session["role"] = "QPPV"


def report():
    return {
        "psur_id": 7,
        "product_name": "Medicine X",
        "active_substances": "Substance X",
        "therapeutic_indication": "Indication X",
        "mechanism_of_action": "Recorded mechanism",
        "reporting_period_start": date(2026, 1, 1),
        "reporting_period_end": date(2026, 6, 30),
        "countries_covered": "Uganda",
        "marketing_authorisation_number": "MA-123",
        "marketing_authorisation_procedure": "National",
    }


def test_initial_section_draft_uses_only_system_evidence(monkeypatch):
    generate = Mock(return_value="Two reports were recorded for the event.")
    monkeypatch.setattr(
        psur_builder_ai,
        "build_psur_evidence_sections",
        lambda _report: {
            "signal_evaluation": (
                "Signal SIG-1 was recorded as under evaluation with two "
                "supporting cases."
            )
        },
    )
    monkeypatch.setattr(psur_builder_ai, "_generate", generate)

    proposal = psur_builder_ai.draft_psur_section_content(
        report(),
        "signal_evaluation",
        "Signal evaluation",
    )

    generate.assert_not_called()
    assert proposal["proposed_content"] == (
        "Signal SIG-1 was recorded as under evaluation with two "
        "supporting cases."
    )
    assert proposal["evidence_used"]["proposal_basis"] == "system_evidence"


def test_initial_section_without_evidence_returns_platform_record_caveat(
    monkeypatch,
):
    generate = Mock()
    monkeypatch.setattr(
        psur_builder_ai,
        "build_psur_evidence_sections",
        lambda _report: {},
    )
    monkeypatch.setattr(psur_builder_ai, "_generate", generate)

    proposal = psur_builder_ai.draft_psur_section_content(
        report(),
        "literature",
        "Literature",
    )

    assert "No section-specific evidence was assembled" in (
        proposal["proposed_content"]
    )
    generate.assert_not_called()


def test_initial_draft_route_saves_a_pending_proposal_not_section_content():
    app = create_test_app()
    cursor = Mock()
    section = {
        "psur_builder_section_id": 33,
        "section_title": "Executive summary",
        "final_content": None,
    }
    proposal = {
        "proposed_content": "A fact-based suggested section narrative.",
        "evidence_used": {"section_key": "executive_summary"},
    }

    with app.test_client() as client:
        sign_in(client)
        with (
            patch(
                "app.psur.builder_routes.get_or_create_psur_builder",
                return_value=(report(), 44),
            ),
            patch(
                "app.psur.builder_routes.query_one",
                return_value=section,
            ),
            patch(
                "app.psur.builder_routes.draft_psur_section_content",
                return_value=proposal,
            ),
            patch(
                "app.psur.builder_routes.transaction",
                return_value=nullcontext(cursor),
            ),
        ):
            response = client.post(
                "/psur/7/builder/sections/executive_summary/propose",
                data={"proposal_type": "suggestion"},
            )

    assert response.status_code == 302
    insert_call = cursor.execute.call_args_list[1]
    insert_parameters = insert_call.args[1]
    assert insert_parameters[1] == "suggestion"
    assert insert_parameters[2] == proposal["proposed_content"]


def test_accepting_a_section_proposal_does_not_require_other_sections_complete():
    app = create_test_app()
    cursor = Mock()
    pending_proposal = {
        "proposal_id": 5,
        "proposed_content": "Reviewed initial section content.",
        "psur_builder_section_id": 33,
    }

    with app.test_client() as client:
        sign_in(client)
        with (
            patch(
                "app.psur.builder_routes.get_or_create_psur_builder",
                return_value=(report(), 44),
            ),
            patch(
                "app.psur.builder_routes.query_one",
                return_value=pending_proposal,
            ),
            patch(
                "app.psur.builder_routes.query_all",
                side_effect=AssertionError(
                    "Acceptance must not be blocked by other incomplete sections"
                ),
            ),
            patch(
                "app.psur.builder_routes.transaction",
                return_value=nullcontext(cursor),
            ),
        ):
            response = client.post(
                "/psur/7/builder/sections/executive_summary/"
                "proposals/5/accepted"
            )

    assert response.status_code == 302
    section_update = cursor.execute.call_args_list[1]
    assert section_update.args[1] == (
        "Reviewed initial section content.",
        9,
        33,
    )
