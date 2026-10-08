from datetime import date

import app.cases.ai_report_routes as ai_routes
import app.rsi.routes as rsi_routes
from app.services.psur_evidence import _rsi_lines, rsi_appendix_text
from app.services.rsi_assessment import (
    assess_event_against_rsi,
    choose_rsi_document,
    rsi_document_rank,
)


def _doc(rsi_id, document_type, effective=None, **extra):
    return {
        "rsi_id": rsi_id,
        "product_name": "ABPARA",
        "active_substance": "pcm",
        "document_type": document_type,
        "document_version": "12",
        "effective_date": effective,
        **extra,
    }


APDL = _doc(1, "APDL Product Information")
INNOVATOR_OLD = _doc(2, "Innovator Reference Safety Information", date(2025, 1, 1))
INNOVATOR_NEW = _doc(3, "Innovator Reference Safety Information", date(2026, 9, 16))
SMPC = _doc(4, "SmPC", date(2026, 9, 16))


def test_innovator_reference_is_preferred_then_label_then_apdl():
    assert rsi_document_rank(INNOVATOR_NEW) < rsi_document_rank(SMPC)
    assert rsi_document_rank(SMPC) < rsi_document_rank(APDL)
    assert choose_rsi_document([APDL, SMPC, INNOVATOR_OLD, INNOVATOR_NEW]) == INNOVATOR_NEW
    assert choose_rsi_document([APDL, SMPC]) == SMPC
    assert choose_rsi_document([APDL]) == APDL
    assert choose_rsi_document([]) is None


def test_no_extracted_terms_means_cannot_assess_not_unlisted():
    result = assess_event_against_rsi(INNOVATOR_NEW, [], "Urticaria")

    assert result["available"] is False
    assert "Cannot assess listedness yet" in result["message"]
    assert "Innovator Reference Safety Information version 12" in result["message"]


def test_match_on_verified_terms_is_listed_and_not_provisional():
    reactions = [
        {"reaction_term": "Urticaria", "source_excerpt": "Urticaria (common)", "review_status": "Verified"},
        {"reaction_term": "Nausea", "source_excerpt": None, "review_status": "Verified"},
    ]

    result = assess_event_against_rsi(INNOVATOR_NEW, reactions, "urticaria, hypersensitivity")

    assert result["listedness_status"] == "Listed"
    assert result["expectedness_status"] == "Expected"
    assert result["provisional"] is False
    assert "Assessed against: ABPARA — Innovator Reference Safety Information version 12" in result["evidence"]
    assert "2 verified, 0 not yet verified" in result["evidence"]


def test_unverified_terms_make_result_provisional():
    reactions = [
        {"reaction_term": "Nausea", "source_excerpt": None, "review_status": "Verified"},
        {"reaction_term": "Headache", "source_excerpt": None, "review_status": "Proposed"},
    ]

    result = assess_event_against_rsi(INNOVATOR_NEW, reactions, "Chills")

    assert result["listedness_status"] == "Not listed"
    assert result["provisional"] is True
    assert "PROVISIONAL" in result["evidence"]


def test_route_helper_uses_innovator_document_and_refuses_without_terms(monkeypatch):
    seen = {}

    def fake_query_all(sql, params=()):
        if "FROM pv.reference_safety_information" in sql:
            return [APDL, INNOVATOR_NEW]
        seen["terms_for"] = params
        return []

    monkeypatch.setattr(rsi_routes, "query_all", fake_query_all)

    result = rsi_routes.automatic_uploaded_rsi_assessment("ABPARA", "Chills")

    assert seen["terms_for"] == (3,)
    assert result["available"] is False


def test_route_helper_returns_none_without_any_current_document(monkeypatch):
    monkeypatch.setattr(rsi_routes, "query_all", lambda sql, params=(): [])

    assert rsi_routes.automatic_uploaded_rsi_assessment("ABPARA", "Chills") is None


def test_ai_assessment_picks_newest_innovator_and_apdl_documents(monkeypatch):
    apdl_new = _doc(5, "APDL Product Information", date(2026, 9, 1))
    monkeypatch.setattr(
        ai_routes,
        "query_all",
        lambda sql, params=(): [apdl_new, INNOVATOR_NEW, SMPC, INNOVATOR_OLD, APDL],
    )

    _, apdl, innovator = ai_routes.rsi_documents_for_product(
        {"product_name": "ABPARA", "generic_name": "pcm"}
    )

    assert apdl == apdl_new
    assert innovator == INNOVATOR_NEW


def test_psur_section_5_includes_undated_documents():
    text = _rsi_lines([
        {
            "document_type": "APDL Product Information",
            "document_version": "12",
            "market": "EU",
            "effective_date": None,
            "added_on": date(2026, 9, 16),
        }
    ])

    assert "no effective date recorded" in text
    assert "16 Sep 2026" in text


def test_psur_appendix_2_lists_current_reference_documents():
    text = rsi_appendix_text(
        "ABPARA",
        [
            {
                "document_type": "Innovator Reference Safety Information",
                "document_version": "12",
                "market": "EU",
                "effective_date": date(2026, 9, 16),
                "reference_product_name": "norvatis",
                "original_filename": "PCM.pdf",
                "source_url": "https://example.org/label",
            }
        ],
    )

    assert "in force at the time this report was prepared" in text
    assert "Innovator Reference Safety Information; reference product: norvatis; version 12; market: EU; effective 16 Sep 2026; file: PCM.pdf; source: https://example.org/label." in text


def test_psur_appendix_2_says_when_nothing_is_recorded():
    assert "No current reference safety information for ABPARA" in rsi_appendix_text("ABPARA", [])
