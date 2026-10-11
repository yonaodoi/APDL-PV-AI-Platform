"""Several patient reports in one uploaded document."""

import json

from app.services import document_intake as intake

PAGES = [
    (1, "LINE LISTING\nPatient 1\nInitials: J.K. Age: 45 Sex: Male\nReaction: rash\nOutcome: Recovering"),
    (2, "Patient 2\nInitials: M.N. Age: 30\nReaction: headache\nPatient 3\nInitials: P.Q. Age: 61\nReaction: urticaria"),
]


def _app():
    from app import create_app
    from config import TestingConfig

    return create_app(TestingConfig)


def test_quotes_are_found_despite_spacing_and_punctuation():
    text = "Patient 2\nInitials: M.N.  Age: 30"
    assert intake._find(text, "patient 2 initials M.N. age 30") == (0, len(text))
    assert intake._find(text, "two words") is None              # too short to trust
    assert intake._find(text, "Patient 9 Initials X") is None


def test_report_on_a_shared_page_takes_only_its_own_text():
    report = {"pages": [2], "start_quote": "Patient 3 Initials: P.Q.", "end_quote": "Reaction: urticaria"}
    text, excerpt, found, flags = intake.report_text(PAGES, report, others_on_same_pages=True)
    assert found and text.startswith("Patient 3") and "M.N." not in text and not flags


def test_report_ends_where_the_next_patient_starts():
    report = {"pages": [2], "start_quote": "Patient 2 Initials: M.N.", "end_quote": "Unknown"}
    text, _, found, flags = intake.report_text(PAGES, report, True, ["Patient 3 Initials: P.Q."])
    assert found and "M.N." in text and "P.Q." not in text and not flags


def test_last_report_on_a_page_runs_to_the_end():
    report = {"pages": [2], "start_quote": "Patient 3 Initials: P.Q.", "end_quote": "x"}
    text, _, found, flags = intake.report_text(PAGES, report, True, ["Patient 2 Initials: M.N."])
    assert text.startswith("Patient 3") and text.endswith("urticaria") and not flags


def test_unconfirmed_boundaries_are_flagged_not_hidden():
    report = {"pages": [2], "start_quote": "Patient 2 Initials: M.N.", "end_quote": "words that are not there at all"}
    text, excerpt, found, flags = intake.report_text(PAGES, report, others_on_same_pages=True)
    assert found and "P.Q." in text
    assert any("Other patients are on the same page" in f for f in flags)


def test_invented_excerpts_are_reported():
    report = {"pages": [1], "start_quote": "this sentence is not in the document", "end_quote": None}
    _, _, found, flags = intake.report_text(PAGES, report, others_on_same_pages=False)
    assert not found and any("not found in the document text" in f for f in flags)


def test_ai_answer_is_cleaned(monkeypatch):
    from app.services import llm

    reply = {"reports": [
        {"label": "P1", "kind": "New patient", "pages": [1, "2", 7, "x"], "start_quote": "Patient 1", "patient": {"bad": 1}},
        {"label": "F", "kind": "Something else", "pages": []},
        "junk",
    ]}
    monkeypatch.setattr(llm, "generate_text", lambda *a, **k: json.dumps(reply))
    with _app().app_context():
        reports = intake.find_reports(PAGES)
    assert [r["pages"] for r in reports] == [[1, 2], []]       # page 7 does not exist
    assert reports[1]["kind"] == "New patient" and reports[0]["patient"] is None


def test_same_patient_identity_ignores_punctuation():
    a = intake._identity({"patient_initials": "j.k.", "patient_age_years": "45", "patient_sex": "Male"})
    b = intake._identity({"patient_initials": "JK", "patient_age_years": "45", "patient_sex": "Male"})
    assert a == b


def test_intake_pages_need_sign_in():
    client = _app().test_client()
    assert client.get("/cases/intake/").status_code == 302
    assert client.post("/cases/intake/upload").status_code == 302
