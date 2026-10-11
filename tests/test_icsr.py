from datetime import date

import pytest

from app.services import case_pathway, icsr


@pytest.mark.parametrize("raw, expected", [
    ("2026", "2026"),
    ("2026-9", "2026-09"),
    ("2026-09-16", "2026-09-16"),
    ("16/09/2026", "2026-09-16"),
    ("09/2026", "2026-09"),
    ("Sep 2026", "2026-09"),
    ("16 September 2026", "2026-09-16"),
    ("", ""),
])
def test_partial_dates_keep_their_precision(raw, expected):
    assert icsr.parse_partial_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "2026-13", "1850", "yesterday"])
def test_impossible_dates_are_refused(raw):
    with pytest.raises(icsr.PartialDateError):
        icsr.parse_partial_date(raw)


def test_partial_dates_are_shown_as_entered():
    assert icsr.show_partial("2026-09") == "Sep 2026"
    assert icsr.show_partial("2026") == "2026"
    assert icsr.full_date("2026-09") is None
    assert icsr.full_date("2026-09-16") == date(2026, 9, 16)


class FakeForm(dict):
    def getlist(self, key):
        return [self[key]] if key in self else []


def _form(**extra):
    form = FakeForm({"case-received_date": "2026-09-16"})
    form.update(extra)
    return form


def test_form_rows_null_flavours_and_removed_entries():
    form = _form(**{
        "case-patient_dob_text": "1992-03",
        "nf-case-patient_ethnicity": "NASK",
        "products-p1-__key": "p1", "products-p1-__id": "1", "products-p1-__order": "0", "products-p1-__remove": "0",
        "products-p1-product_name": "ABPARA 500mg", "products-p1-product_role": "Suspect", "products-p1-dose_value": "1,5",
        "reactions-n1-__key": "n1", "reactions-n1-__id": "", "reactions-n1-__order": "1", "reactions-n1-__remove": "0",
        "reactions-n1-reported_term": "Facial swelling", "reactions-n1-serious": "Yes", "reactions-n1-crit_hospitalisation": "on",
        "reactions-n2-__key": "n2", "reactions-n2-__id": "", "reactions-n2-__order": "2", "reactions-n2-__remove": "1",
        "reactions-n2-reported_term": "removed one",
        "tests-n3-__key": "n3", "tests-n3-__id": "", "tests-n3-__order": "0", "tests-n3-__remove": "0",  # empty: ignored
    })
    data, errors = icsr.parse_icsr_form(form)
    assert errors == {}
    assert data["case"]["patient_dob_text"] == "1992-03"
    assert data["case_nf"] == {"patient_ethnicity": "NASK"}
    assert data["products"][0]["product_name"] == "ABPARA 500mg"        # wording kept as reported
    assert str(data["products"][0]["dose_value"]) == "1.5"
    assert [r["reported_term"] for r in data["reactions"]] == ["Facial swelling"]
    assert data["reactions"][0]["crit_hospitalisation"] is True
    assert data["tests"] == []
    assert icsr.check_icsr(data) == {}


def test_rules_point_at_the_exact_field():
    form = _form(**{
        "case-patient_lmp_text": "31/02/2026",
        "reactions-n1-__key": "n1", "reactions-n1-__id": "", "reactions-n1-__order": "0", "reactions-n1-__remove": "0",
        "reactions-n1-serious": "Yes", "reactions-n1-outcome": "Fatal",
        "causality-n9-__key": "n9", "causality-n9-__id": "", "causality-n9-__order": "0", "causality-n9-__remove": "0",
        "causality-n9-result": "Possible",
    })
    data, errors = icsr.parse_icsr_form(form)
    errors.update(icsr.check_icsr(data))
    assert "case-patient_lmp_text" in errors
    assert "reactions-n1-reported_term" in errors
    assert "reactions-n1-serious" in errors                  # serious without a criterion
    assert "causality-n9-product_ref" in errors


def test_headings_are_meaningful():
    assert icsr.entry_heading("product", {}) == "Drug not yet named"
    assert icsr.entry_heading("product", {"product_name": "ABPARA", "strength": "500 mg", "product_role": "Suspect"}) == "ABPARA 500 mg · Suspect"
    assert icsr.entry_heading("reaction", {"reported_term": "Rash"}) == "Rash"
    assert icsr.entry_heading("test", {"test_name": "ALT", "test_date_text": "2026-09"}) == "ALT · Sep 2026"
    products = [{"case_product_id": 1, "product_name": "ABPARA"}]
    reactions = [{"reaction_id": 2, "reported_term": "Rash"}]
    heading = icsr.causality_heading({"case_product_id": 1, "reaction_id": 2, "result": "Possible", "source": "Company"}, products, reactions)
    assert heading == "ABPARA → Rash: Possible (Company)"


def test_validity_suggestions_flag_uncertainty():
    case = {"patient_address": "Kampala", "reporter_qualification": "Pharmacist", "event_description": "rash"}
    s = case_pathway.suggest_validity(case, [{"product_name": "ABPARA", "product_role": "Suspect"}], [])
    assert s["validity_patient"][0] == "Uncertain"
    assert s["validity_reporter"][0] == "Uncertain"
    assert s["validity_product"][0] == "Present"
    assert s["validity_event"][0] == "Present"
    vague = case_pathway.suggest_validity({"patient_initials": "patient", "reporter_name": "unknown"}, [], [])
    assert vague["validity_patient"][0] == "Absent" and vague["validity_reporter"][0] == "Absent"
    assert case_pathway.validity_outcome({k: "Present" for k, _ in case_pathway.VALIDITY_CRITERIA}) == "Valid"


def _pathway(app, **case):
    base = {"case_id": 5, "workflow_status": "Assessment", "validity_decision": "Valid", "case_narrative": "x"}
    base.update(case)
    with app.test_request_context():
        return case_pathway.build_pathway(
            base,
            [{"case_product_id": 1, "product_name": "ABPARA", "product_role": "Suspect"}],
            [{"reaction_id": 2, "reported_term": "Rash", "outcome": "Recovered/resolved"}],
            [{"code": "minimum_patient", "label": "Identifiable patient", "status": "Pass", "message": ""}],
            {"assessment_source": "reviewer", "listedness_status": "Listed"},
            [{"case_product_id": 1, "reaction_id": 2, "result": "Possible", "is_ai_draft": False}],
            {"open": 0},
        )


def test_pathway_moves_through_the_stages():
    from app import create_app
    from config import TestingConfig

    app = create_app(TestingConfig)
    p = _pathway(app, validity_decision=None)
    assert p["current"] == "validation" and p["next_action"]["label"] == "Confirm case validity"
    p = _pathway(app)
    assert p["current"] == "review"
    assert [s["state"] for s in p["stages"]][:6] == ["done"] * 6
    p = _pathway(app, approval_stage="Approved")
    assert p["current"] == "reporting"
    p = _pathway(app, approval_stage="Approved", regulatory_submitted_date=date(2026, 10, 1))
    assert p["current"] is None and p["next_action"]["label"] == "Case complete"


def test_ai_draft_causality_does_not_count():
    from app import create_app
    from config import TestingConfig

    app = create_app(TestingConfig)
    with app.test_request_context():
        p = case_pathway.build_pathway(
            {"case_id": 5, "workflow_status": "Assessment", "validity_decision": "Valid", "case_narrative": "x"},
            [{"case_product_id": 1, "product_name": "ABPARA", "product_role": "Suspect"}],
            [{"reaction_id": 2, "reported_term": "Rash", "outcome": "Fatal"}],
            [],
            {"assessment_source": "reviewer", "listedness_status": "Listed"},
            [{"case_product_id": 1, "reaction_id": 2, "result": "Probable / Likely", "is_ai_draft": True, "source": "AI-assisted draft"}],
            {"open": 0},
        )
    assert p["current"] == "assessment"
    assert any("AI-assisted" in i["text"] for i in p["issues"])


def test_follow_up_summary_counts_overdue():
    s = case_pathway.follow_up_summary(
        [{"status": "Open", "due_date": date(2026, 10, 1), "assigned_to_name": "Henry"},
         {"status": "Open", "due_date": date(2026, 10, 20)},
         {"status": "Completed", "due_date": date(2026, 9, 1)}],
        [{"status": "Sent", "sent_at": date(2026, 10, 2)}],
        today=date(2026, 10, 11),
    )
    assert s["open"] == 2 and s["overdue"] == 1 and s["next_due"] == date(2026, 10, 1) and s["owners"] == ["Henry"]
