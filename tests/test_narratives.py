"""Controlled ICSR narratives built from the structured record."""

from datetime import date

from app.services import narratives


def _template(case_type="Spontaneous"):
    return {"template_id": 1, "case_type": case_type, "version": 1, "sections": narratives.DEFAULT_TEMPLATES[case_type]}


def _record(**case):
    base = {
        "received_date": date(2026, 9, 16), "reporter_qualification": "Physician", "reporting_channel": "Email",
        "patient_age_value": 34, "patient_age_unit": "Years", "patient_sex": "Female", "country_name": "Uganda",
    }
    base.update(case)
    return {
        "case": base,
        "products": [
            {"case_product_id": 1, "product_name": "ABPARA", "generic_name": "Paracetamol", "product_role": "Suspect",
             "dose_value": 1, "dose_unit": "g", "route": "Intravenous", "start_text": "2026-09-10", "batch_number": "B123",
             "action_taken": "Drug withdrawn", "end_text": "2026-09-14"},
            {"case_product_id": 2, "product_name": "Amoxil", "product_role": "Suspect", "start_text": "2026-09"},
        ],
        "reactions": [{"reaction_id": 5, "reported_term": "urticaria", "start_text": "2026-09-14", "serious": "Yes",
                       "crit_hospitalisation": True, "outcome": "Recovered/resolved", "end_text": "2026-09-20"}],
        "tests": [], "history": [], "past_drugs": [], "drug_reactions": [],
        "causality": [
            {"case_product_id": 1, "reaction_id": 5, "source": "Reporter", "result": "Probable / Likely", "method": "WHO-UMC"},
            {"case_product_id": 1, "reaction_id": 5, "source": "Company", "result": "Possible"},
            {"case_product_id": 2, "reaction_id": 5, "source": "AI-assisted draft", "result": "Certain", "is_ai_draft": True},
        ],
    }


def test_spontaneous_narrative_follows_the_required_sequence():
    text, sentences, issues = narratives.build(_record(), _template(), "Acme Pharma Ltd")
    paragraphs = text.split("\n\n")
    assert paragraphs[0] == "This spontaneous case was received by Acme Pharma Ltd on 16 Sep 2026 from a physician via email."
    assert "34-year-old female patient from Uganda" in text
    # Each suspect drug separately; time to onset only from complete dates.
    assert "ABPARA (Paracetamol)" in text and "Amoxil, starting in Sep 2026" in text
    assert "{" not in text and "[[" not in text
    assert "4 days after starting ABPARA (Paracetamol)" in text
    # Source and company causality presented separately; AI draft left out.
    assert "The reporter considered the relationship between ABPARA (Paracetamol) and urticaria probable / likely (WHO-UMC), while Acme Pharma Ltd assessed it as possible." in text
    assert "certain" not in text.lower()
    assert any("AI-assisted causality draft" in i for i in issues)
    # Every sentence keeps the structured fields it used.
    assert all(s["fields"] for s in sentences if "{" not in s["text"])
    order = [s["paragraph"] for s in sentences]
    assert order.index("intro") < order.index("patient") < order.index("drug") < order.index("events") < order.index("closing") < order.index("causality")


def test_missing_facts_are_left_out_not_invented():
    record = _record(patient_sex="Unknown", country_name=None, reporting_channel=None)
    record["reactions"][0].update(outcome=None, start_text=None)
    text, _, _ = narratives.build(record, _template(), "Acme")
    assert "via" not in text.split("\n\n")[0]
    assert "The outcome of urticaria was not reported." in text
    assert "onset date not reported" in text
    assert "normal" not in text.lower() and "absent" not in text.lower()


def test_placeholders_and_contradictions_are_reported():
    record = _record(patient_ethnicity="XXXX", patient_died="Yes")
    record["products"][0]["end_text"] = "2026-09-01"     # stop before start
    text, _, issues = narratives.build(record, _template(), "Acme")
    assert "XXXX" not in text
    assert any("placeholder" in i for i in issues)
    assert any("stop date is before the start date" in i for i in issues)
    assert any("no reaction has a fatal outcome" in i for i in issues)


def test_clinical_trial_preserves_blinding_and_literature_names_the_article():
    record = _record(case_category="Clinical trial", study_identifier="APD-301", study_subject_id="S-0042", study_blinded="Yes")
    assert narratives.case_type_for(record["case"]) == "Clinical trial"
    text, _, _ = narratives.build(record, _template("Clinical trial"), "Acme")
    assert "This clinical trial case was received by Acme" in text and "study APD-301 for subject S-0042" in text
    assert "remains blinded" in text

    record = _record(case_category="Literature", literature_authors="Okello A, Namuli B", literature_journal="Afr J Med",
                     literature_year="2026", literature_title="Urticaria after IV paracetamol", literature_doi="PMID 123")
    text, _, _ = narratives.build(record, _template("Literature"), "Acme")
    assert "This literature case was retrieved by Acme" in text
    assert "an article by Okello A, Namuli B, published in Afr J Med in 2026, titled 'Urticaria after IV paracetamol' (PMID 123)" in text
    assert "distinct from the publication date" in text


def test_template_editing_rejects_unknown_fields_and_open_brackets():
    form = {f"section-{k}": "" for k in narratives.SECTION_KEYS}
    form["section-intro"] = "Received on {day0} from {reporter_typ}.\nSee [[ {channel} here."
    _, problems = narratives.parse_sections(form)
    assert any("{reporter_typ}" in p for p in problems)
    assert any("not closed" in p for p in problems)
    form["section-intro"] = ""
    _, problems = narratives.parse_sections(form)
    assert any("introduction needs" in p for p in problems)


def test_adopting_text_with_placeholders_is_refused(monkeypatch):
    try:
        narratives.adopt(1, "The patient received {drug}.", 1)
    except ValueError as error:
        assert "placeholders" in str(error)
    else:
        raise AssertionError("placeholder text was accepted")
