from app.services.event_coding import (
    build_dictionary,
    code_verbatim,
    known_terms,
    split_verbatim_terms,
)


TERMS = [
    {"term_id": 1, "preferred_term": "Urticaria", "system_organ_class": "Skin"},
    {"term_id": 2, "preferred_term": "Hypersensitivity", "system_organ_class": "Immune"},
    {"term_id": 3, "preferred_term": "Paraesthesia", "system_organ_class": "Nervous"},
    {"term_id": 4, "preferred_term": "Stevens-Johnson syndrome", "system_organ_class": "Skin"},
    {"term_id": 5, "preferred_term": "Pyrexia", "system_organ_class": "General"},
]
SYNONYMS = [
    {"synonym": "hives", "term_id": 1},
    {"synonym": "allergic reaction", "term_id": 2},
    {"synonym": "pins and needles", "term_id": 3},
    {"synonym": "fever", "term_id": 5},
]
DICTIONARY = build_dictionary(TERMS, SYNONYMS)


def coded(verbatim):
    term, method = code_verbatim(verbatim, DICTIONARY)
    return (term["preferred_term"] if term else None), method


def test_exact_preferred_term_match_ignores_case_and_punctuation():
    assert coded("URTICARIA") == ("Urticaria", "Exact")
    assert coded("stevens johnson syndrome") == (
        "Stevens-Johnson syndrome",
        "Exact",
    )


def test_synonym_and_plural_match():
    assert coded("Hives") == ("Urticaria", "Synonym")
    assert coded("Allergic reaction") == ("Hypersensitivity", "Synonym")
    assert coded("fevers") == ("Pyrexia", "Synonym")


def test_misspelling_is_suggested_not_confirmed():
    assert coded("UTICARIA") == ("Urticaria", "Suggested")
    assert coded("hypersensitivty") == ("Hypersensitivity", "Suggested")


def test_unknown_term_is_uncoded():
    assert coded("ffffff") == (None, "Uncoded")


def test_split_keeps_reporter_wording_and_drops_placeholders():
    assert split_verbatim_terms("UTICARIA, HYPERSENSITIVITY; n/a") == [
        "UTICARIA",
        "HYPERSENSITIVITY",
    ]
    assert split_verbatim_terms("Rash and itching with fever") == [
        "Rash",
        "itching",
        "fever",
    ]


def test_split_keeps_known_phrases_containing_and():
    assert split_verbatim_terms(
        "Pins and needles, fever", known_terms(DICTIONARY)
    ) == ["Pins and needles", "fever"]
