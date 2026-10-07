from datetime import date

from app.services.signal_detection import (
    build_signal_candidates,
    extract_event_terms,
    summarise_screening,
)


def make_case(case_id, **overrides):
    case = {
        "case_id": case_id,
        "case_number": f"APDL-ICSR-26-{case_id:03d}",
        "received_date": date(2026, 9, 16),
        "seriousness": False,
        "seriousness_criteria": None,
        "event_outcome": "Recovered/resolved",
        "event_description": "Rash",
        "product_name": "ABPARA",
    }
    case.update(overrides)
    return case


def by_term(candidates):
    return {candidate["term"]: candidate for candidate in candidates}


def test_event_text_is_split_into_normalised_terms():
    assert extract_event_terms("UTICARIA, HYPERSENSITIVITY") == [
        "urticaria",
        "hypersensitivity",
    ]
    assert extract_event_terms("Skin rash and itching; Pyrexia") == [
        "rash",
        "pruritus",
        "fever",
    ]


def test_placeholder_events_are_ignored():
    assert extract_event_terms("n/a") == []
    assert extract_event_terms("Unknown") == []
    assert extract_event_terms("") == []


def test_two_cases_with_same_term_form_a_cluster():
    index = make_case(2, event_description="Urticaria, hypersensitivity")
    other = make_case(
        1,
        received_date=date(2026, 8, 1),
        event_description="uticaria",
    )

    candidates = by_term(
        build_signal_candidates(index, ["ABPARA"], [index, other])
    )

    assert set(candidates) == {"urticaria"}
    urticaria = candidates["urticaria"]
    assert urticaria["priority"] == "Medium"
    assert urticaria["supporting_case_ids"] == [1, 2]
    assert urticaria["key"] == "abpara::urticaria"
    assert urticaria["event_term"] == "Urticaria"


def test_cluster_with_a_serious_case_is_high_priority():
    index = make_case(2)
    other = make_case(1, seriousness=True)

    candidate = build_signal_candidates(index, ["ABPARA"], [index, other])[0]

    assert candidate["priority"] == "High"


def test_cases_outside_window_or_other_products_do_not_cluster():
    index = make_case(2)
    too_old = make_case(1, received_date=date(2026, 5, 1))
    other_product = make_case(3, product_name="OTHERMED")

    candidates = build_signal_candidates(
        index, ["ABPARA"], [index, too_old, other_product]
    )

    assert candidates == []


def test_single_fatal_case_raises_critical_signal():
    index = make_case(
        2,
        seriousness=True,
        seriousness_criteria="DEATH",
        event_description="Anaphylactic shock",
    )

    candidate = build_signal_candidates(index, ["ABPARA"], [index])[0]

    assert candidate["term"] == "anaphylaxis"
    assert candidate["priority"] == "Critical"
    assert candidate["supporting_case_ids"] == [2]
    assert "fatal case" in candidate["triggers"][0]


def test_single_serious_unlisted_case_raises_high_signal():
    index = make_case(2, seriousness=True)

    unlisted = build_signal_candidates(
        index, ["ABPARA"], [index], listedness="Not listed"
    )
    listed = build_signal_candidates(
        index, ["ABPARA"], [index], listedness="Listed"
    )

    assert unlisted[0]["priority"] == "High"
    assert listed == []


def test_single_non_serious_case_raises_nothing():
    index = make_case(2)

    assert build_signal_candidates(index, ["ABPARA"], [index]) == []


def test_screening_summary_lists_new_and_linked_signals():
    message = summarise_screening(
        [
            {
                "created": True,
                "signal_number": "AUTO-SIG-0004",
                "product_name": "ABPARA",
                "event_term": "Urticaria",
                "priority": "High",
                "newly_linked_cases": 2,
            },
            {
                "created": False,
                "signal_number": "APDL-SIG-001",
                "product_name": "ABPARA",
                "event_term": "Chills",
                "priority": "Medium",
                "newly_linked_cases": 1,
            },
        ]
    )

    assert "AUTO-SIG-0004 (ABPARA — Urticaria, High)" in message
    assert "linked to existing signal(s): APDL-SIG-001" in message
    assert summarise_screening([]) is None
