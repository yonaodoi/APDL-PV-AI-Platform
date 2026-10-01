from datetime import date

from app.services import case_duplicates


def test_exact_patient_match_is_flagged_with_explanation(monkeypatch):
    case = {
        "case_id": 1,
        "patient_initials": "A.B.",
        "patient_date_of_birth": date(1990, 1, 2),
    }
    candidate = {
        "case_id": 2,
        "case_number": "CASE-2",
        "patient_initials": "AB",
        "patient_date_of_birth": date(1990, 1, 2),
        "reporter_email": None,
        "event_onset_date": None,
        "workflow_status": "New",
        "suspect_products": [],
    }
    monkeypatch.setattr(case_duplicates, "query_all", lambda *_: [candidate])
    monkeypatch.setattr(case_duplicates, "query_one", lambda *_: None)

    matches = case_duplicates.find_possible_duplicates(case, [])

    assert len(matches) == 1
    assert matches[0]["review_status"] == "Needs review"
    assert matches[0]["match_reasons"] == [
        "Patient initials and date of birth match exactly."
    ]


def test_reporter_and_product_match_requires_nearby_onset(monkeypatch):
    case = {
        "case_id": 1,
        "reporter_email": "reporter@example.org",
        "event_onset_date": date(2026, 9, 20),
    }
    candidate = {
        "case_id": 2,
        "case_number": "CASE-2",
        "patient_initials": None,
        "patient_date_of_birth": None,
        "reporter_email": "REPORTER@example.org",
        "event_onset_date": date(2026, 9, 27),
        "workflow_status": "Triage",
        "suspect_products": ["product x"],
    }
    monkeypatch.setattr(case_duplicates, "query_all", lambda *_: [candidate])
    monkeypatch.setattr(case_duplicates, "query_one", lambda *_: None)

    matches = case_duplicates.find_possible_duplicates(
        case,
        [{"product_role": "Suspect", "product_name": "Product X"}],
    )

    assert len(matches) == 1
    assert matches[0]["match_reasons"] == [
        "Reporter email, suspected product, and event onset within 7 days match."
    ]


def test_matching_initials_need_product_and_close_onset(monkeypatch):
    case = {
        "case_id": 1,
        "patient_initials": "XY",
        "event_onset_date": date(2026, 9, 20),
    }
    candidate = {
        "case_id": 2,
        "case_number": "CASE-2",
        "patient_initials": "X.Y.",
        "patient_date_of_birth": None,
        "reporter_email": None,
        "event_onset_date": date(2026, 9, 22),
        "workflow_status": "Triage",
        "suspect_products": ["product x"],
    }
    monkeypatch.setattr(case_duplicates, "query_all", lambda *_: [candidate])
    monkeypatch.setattr(case_duplicates, "query_one", lambda *_: None)

    matches = case_duplicates.find_possible_duplicates(
        case,
        [{"product_role": "Suspect", "product_name": "Product X"}],
    )

    assert len(matches) == 1
    assert "suspected product" in matches[0]["match_reasons"][0]


def test_no_candidate_without_sufficient_matching_evidence(monkeypatch):
    case = {
        "case_id": 1,
        "reporter_email": "reporter@example.org",
        "event_onset_date": date(2026, 9, 20),
    }
    candidate = {
        "case_id": 2,
        "case_number": "CASE-2",
        "patient_initials": None,
        "patient_date_of_birth": None,
        "reporter_email": "reporter@example.org",
        "event_onset_date": date(2026, 9, 20),
        "workflow_status": "Triage",
        "suspect_products": ["product y"],
    }
    monkeypatch.setattr(case_duplicates, "query_all", lambda *_: [candidate])
    monkeypatch.setattr(case_duplicates, "query_one", lambda *_: None)

    matches = case_duplicates.find_possible_duplicates(
        case,
        [{"product_role": "Suspect", "product_name": "Product X"}],
    )

    assert matches == []
