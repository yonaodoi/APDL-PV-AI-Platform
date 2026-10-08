from datetime import date

import app.services.case_listedness as listedness
from app import create_app
from config import TestingConfig


CASE = {
    "case_id": 9,
    "case_number": "APDL-ICSR-26-012",
    "event_description": "uticaria, hypersensitivity",
    "seriousness": True,
    "seriousness_criteria": "Hospitalisation",
}
PRODUCT = {"product_name": "ABPARA", "generic_name": "pcm"}
INNOVATOR = {
    "rsi_id": 3,
    "product_name": "ABPARA",
    "active_substance": "pcm",
    "document_type": "Innovator Reference Safety Information",
    "document_version": "12",
    "effective_date": date(2026, 9, 16),
}
EVENT_TERMS = [
    {"verbatim_term": "uticaria", "preferred_term": "Urticaria"},
    {"verbatim_term": "hypersensitivity", "preferred_term": "Hypersensitivity"},
]


def test_events_use_coded_terms_and_fall_back_to_description():
    events = listedness.events_to_assess(CASE, EVENT_TERMS)
    assert events == [
        ("Urticaria", "Urticaria uticaria"),
        ("Hypersensitivity", "Hypersensitivity hypersensitivity"),
    ]
    assert listedness.events_to_assess(CASE, []) == [
        ("uticaria, hypersensitivity", "uticaria, hypersensitivity")
    ]


def test_any_unlisted_event_makes_the_case_unlisted():
    results = [
        {"listedness_status": "Listed", "provisional": False},
        {"listedness_status": "Not listed", "provisional": False},
    ]
    assert listedness.combine_event_results(results) == (
        "Not listed", "Unexpected", False
    )


def test_unassessable_event_gives_insufficient_information():
    results = [
        {"listedness_status": "Listed", "provisional": False},
        {"listedness_status": "Insufficient information", "provisional": True},
    ]
    assert listedness.combine_event_results(results) == (
        "Insufficient information", "Not assessable", True
    )


def test_all_listed_verified_events_are_listed_and_final():
    results = [{"listedness_status": "Listed", "provisional": False}]
    assert listedness.combine_event_results(results) == ("Listed", "Expected", False)


def _patch_case_data(monkeypatch, documents, terms, event_terms=EVENT_TERMS):
    monkeypatch.setattr(listedness, "_load_case", lambda case_id: dict(CASE))
    monkeypatch.setattr(listedness, "_suspect_product", lambda case_id: dict(PRODUCT))
    monkeypatch.setattr(
        listedness, "current_documents_for_product", lambda product: documents
    )
    monkeypatch.setattr(listedness, "_reaction_terms", lambda rsi_id: terms)
    monkeypatch.setattr(
        "app.services.event_coding.code_case_events", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "app.services.event_coding.get_case_event_terms", lambda case_id: event_terms
    )


def test_case_with_uploaded_rsi_and_verified_terms(monkeypatch):
    _patch_case_data(
        monkeypatch,
        [INNOVATOR],
        [
            {"reaction_term": "Urticaria", "source_excerpt": "Urticaria: common", "review_status": "Verified"},
            {"reaction_term": "Hypersensitivity", "source_excerpt": None, "review_status": "Verified"},
        ],
    )
    app = create_app(TestingConfig)
    with app.app_context():
        result = listedness.assess_case(9, allow_online=False)

    assert result["listedness_status"] == "Listed"
    assert result["provisional"] is False
    assert result["rsi_id"] == 3
    assert "Innovator Reference Safety Information version 12" in result["source_title"]


def test_case_with_rsi_but_no_terms_is_not_called_unlisted(monkeypatch):
    _patch_case_data(monkeypatch, [INNOVATOR], [])
    app = create_app(TestingConfig)
    with app.app_context():
        result = listedness.assess_case(9, allow_online=False)

    assert result["listedness_status"] == "Insufficient information"
    assert result["expectedness_status"] == "Not assessable"
    assert "Cannot assess listedness yet" in result["evidence"]


def test_case_without_any_reference_document(monkeypatch):
    _patch_case_data(monkeypatch, [], [])
    app = create_app(TestingConfig)
    with app.app_context():
        result = listedness.assess_case(9, allow_online=False)

    assert result["listedness_status"] == "Insufficient information"
    assert "No current reference safety information is uploaded for ABPARA" in result["evidence"]


def test_reviewer_confirmed_assessment_is_never_overwritten(monkeypatch):
    saved = []
    monkeypatch.setattr(
        listedness,
        "existing_assessment",
        lambda case_id: {"assessment_source": "reviewer", "listedness_status": "Listed"},
    )
    monkeypatch.setattr(
        listedness, "save_automatic_assessment", lambda *a: saved.append(a)
    )
    app = create_app(TestingConfig)
    with app.app_context():
        outcome = listedness.run_automatic_listedness(9, actor_user_id=1)

    assert outcome == {"kept_reviewer": True}
    assert saved == []


def test_unchanged_automatic_result_is_not_saved_again(monkeypatch):
    result = {
        "case": CASE,
        "rsi_id": 3,
        "source_title": "Doc",
        "events": [],
        "listedness_status": "Listed",
        "expectedness_status": "Expected",
        "provisional": False,
        "evidence": "same",
        "matches": [],
    }
    monkeypatch.setattr(
        listedness,
        "existing_assessment",
        lambda case_id: {
            "assessment_source": "automatic",
            "listedness_status": "Listed",
            "expectedness_status": "Expected",
            "is_provisional": False,
            "rsi_evidence": "same",
            "reference_title": "Doc",
            "rsi_id": 3,
        },
    )
    monkeypatch.setattr(listedness, "assess_case", lambda case_id, allow_online=True: result)
    saved = []
    monkeypatch.setattr(
        listedness, "save_automatic_assessment", lambda *a: saved.append(a)
    )
    app = create_app(TestingConfig)
    with app.app_context():
        outcome = listedness.run_automatic_listedness(9, actor_user_id=1)

    assert outcome["unchanged"] is True
    assert saved == []
    assert listedness.listedness_message(outcome) is None


def test_failure_never_blocks_case_processing(monkeypatch):
    def broken(case_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(listedness, "existing_assessment", broken)
    monkeypatch.setattr(listedness, "get_db", lambda: None)
    app = create_app(TestingConfig)
    with app.app_context():
        outcome = listedness.run_automatic_listedness(9, actor_user_id=1)

    assert outcome == {"failed": True}
    message, category = listedness.listedness_message(outcome)
    assert category == "warning"


def test_provisional_result_message_asks_for_confirmation():
    message, category = listedness.listedness_message(
        {
            "listedness_status": "Not listed",
            "expectedness_status": "Unexpected",
            "provisional": True,
            "source_title": "ABPARA — SmPC",
        }
    )
    assert "Provisional" in message
    assert category == "warning"


def test_recheck_button_runs_check_and_screening(monkeypatch):
    import app.cases.routes as case_routes

    calls = []
    monkeypatch.setattr(case_routes, "query_one", lambda sql, params=(): {"case_id": 9})
    monkeypatch.setattr(
        case_routes,
        "run_automatic_listedness",
        lambda case_id, actor_user_id=None, force=False: calls.append(("check", force))
        or {
            "kept_reviewer": False,
            "unchanged": False,
            "listedness_status": "Listed",
            "expectedness_status": "Expected",
            "provisional": False,
            "source_title": "ABPARA — Innovator Reference Safety Information",
        },
    )
    monkeypatch.setattr(
        case_routes,
        "screen_case_for_signals",
        lambda case_id, actor_user_id=None: calls.append(("screen",)) or ([], False),
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0
    )
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"

    response = client.post("/cases/9/listedness/recheck")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/cases/9#listedness")
    assert calls == [("check", False), ("screen",)]


def test_old_flawed_reviewer_conclusion_is_flagged():
    import app.cases.routes as case_routes

    old = {
        "assessment_source": "reviewer",
        "rsi_evidence": "No matching reaction term was found among the automatically extracted RSI reactions.",
    }
    assert case_routes.needs_rereview(old) is True
    assert case_routes.needs_rereview({**old, "assessment_source": "automatic"}) is False
    assert case_routes.needs_rereview({**old, "rsi_evidence": "Matched RSI term: Urticaria"}) is False
    assert case_routes.needs_rereview(None) is False


def test_replacing_a_reviewer_conclusion_forces_the_check_and_is_audited(monkeypatch):
    from datetime import datetime

    import app.cases.routes as case_routes

    calls, audits = [], []
    monkeypatch.setattr(case_routes, "query_one", lambda sql, params=(): {"case_id": 9})
    monkeypatch.setattr(
        case_routes,
        "_load_safety_assessment",
        lambda case_id: {
            "assessment_source": "reviewer",
            "listedness_status": "Not listed",
            "expectedness_status": "Unexpected",
            "updated_at": datetime(2026, 9, 16, 16, 34),
            "assessed_by_name": "Henry Okedi",
        },
    )
    monkeypatch.setattr(
        case_routes,
        "run_automatic_listedness",
        lambda case_id, actor_user_id=None, force=False: calls.append(force)
        or {
            "kept_reviewer": False,
            "unchanged": False,
            "listedness_status": "Insufficient information",
            "expectedness_status": "Not assessable",
            "provisional": True,
            "source_title": "ABPARA — Innovator Reference Safety Information",
        },
    )
    monkeypatch.setattr(
        case_routes, "write_audit_log", lambda **kwargs: audits.append(kwargs)
    )
    monkeypatch.setattr(
        case_routes, "screen_case_for_signals", lambda case_id, actor_user_id=None: ([], False)
    )
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0
    )
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"

    client.post("/cases/9/listedness/recheck", data={"replace_reviewer": "1"})

    assert calls == [True]
    assert audits[0]["action"] == "Reviewer listedness conclusion replaced by automatic check"
    assert "Not listed / Unexpected, saved 16 Sep 2026 by Henry Okedi" in audits[0]["details"]
