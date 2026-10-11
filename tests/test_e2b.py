"""E2B(R2)/E2B(R3) mapping and checks (no database)."""

import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

from app.services import e2b

RECIPIENT = {"key": "national", "name": "National authority", "identifier": "NDA", "versions": ["R2", "R3"],
             "require_all_reactions_meddra": True, "require_batch_for_suspects": False,
             "require_study_registration": False, "require_most_recent_date": True}


def _data(**case):
    base = {"case_id": 1, "case_number": "ICSR-26-009", "received_date": date(2026, 9, 16),
            "most_recent_info_date": date(2026, 9, 20), "icsr_report_type": "Spontaneous report",
            "validity_decision": "Valid", "reporter_qualification": "Physician", "reporter_country_id": 7,
            "patient_initials": "A.N.", "patient_sex": "Female", "patient_age_value": 34, "patient_age_unit": "Years",
            "case_narrative": "Narrative.", "approval_stage": "Approved"}
    base.update(case)
    record = {
        "case": base, "reporters": [], "history": [], "tests": [], "past_drugs": [],
        "products": [{"case_product_id": 2, "product_name": "ABPARA", "product_role": "Suspect", "route": "Intravenous",
                      "start_text": "2026-09", "action_taken": "Drug withdrawn", "generic_name": "Paracetamol"}],
        "reactions": [{"reaction_id": 3, "reported_term": "urticaria", "outcome": "Recovered/resolved",
                       "start_text": "2026-09-14", "serious": "Yes", "crit_hospitalisation": True}],
        "causality": [{"case_product_id": 2, "reaction_id": 3, "source": "Reporter", "result": "Possible"},
                      {"case_product_id": 2, "reaction_id": 3, "source": "AI-assisted draft", "result": "Certain", "is_ai_draft": True}],
    }
    codings = {("reaction", 3): {"dictionary": "MedDRA", "status": "Coded", "code": "90000001", "pt_code": "90000101",
                                 "version": "29.0", "verbatim": "urticaria"}}
    return {"record": record, "countries": {7: "UG"}, "codings": codings,
            "company": {"legal_name": "Acme Ltd", "department": "PV"},
            "settings": {**e2b.DEFAULT_SETTINGS, "sender_identifier": "ACME", "sender_country": "UG"}}


def test_complete_case_has_no_errors_and_both_files_pass_the_checks():
    data = _data()
    assert [i for i in e2b.check(data, "R3", RECIPIENT) if i["level"] == "error"] == []
    now = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)
    r3 = '<?xml version="1.0" encoding="UTF-8"?>\n' + e2b.build_r3(data, RECIPIENT, 5, now)
    assert e2b.check_file(r3, "R3")[0] == []
    root = ET.fromstring(r3.split("?>", 1)[1])
    ns = {"h": e2b.R3_NS}
    value = [v for v in root.iter(f"{{{e2b.R3_NS}}}value") if v.attrib.get("codeSystem") == e2b.OID["meddra"]][0]
    assert value.attrib["code"] == "90000001" and value.attrib["codeSystemVersion"] == "29.0"
    assert "Certain" not in r3                     # AI causality draft never exported
    assert 'value="202609"' in r3                  # partial date kept partial

    r2 = '<?xml version="1.0" encoding="UTF-8"?>\n' + e2b.R2_DOCTYPE + "\n" + e2b.build_r2(data, RECIPIENT, 6, now)
    assert e2b.check_file(r2, "R2")[0] == []
    report = ET.fromstring(r2.split("?>", 1)[1].replace(e2b.R2_DOCTYPE, "")).find("safetyreport")
    assert report.findtext("safetyreportid") == "UG-ACME-ICSR-26-009"
    assert report.findtext("seriousnesshospitalization") == "1" and report.findtext("seriousnessdeath") == "2"
    drug = report.find("patient/drug")
    assert drug.findtext("drugadministrationroute") == "042" and drug.findtext("actiondrug") == "1"
    assert drug.findtext("drugstartdateformat") == "610" and drug.findtext("drugstartdate") == "202609"
    assert report.find("patient/reaction").findtext("reactionmeddrallt") == "90000001"


def test_problems_point_at_the_field_to_correct():
    data = _data(most_recent_info_date=None, reporter_qualification=None, case_narrative="", approval_stage=None)
    data["codings"] = {("reaction", 3): {"dictionary": "Local", "status": "Coded", "code": "LOCAL-E-00001", "verbatim": "urticaria"}}
    data["record"]["reactions"][0]["outcome"] = None
    issues = {i["field"]: i for i in e2b.check(data, "R3", RECIPIENT)}
    assert issues["case-most_recent_info_date"]["level"] == "error"
    assert issues["case-reporter_qualification"]["level"] == "error"
    assert "local term" in issues["coding:reaction-3"]["message"]
    assert issues["reactions-r3-outcome"]["level"] == "error"
    assert issues["narrative"]["level"] == "error" and issues["approval"]["level"] == "error"


def test_recipient_rules_apply():
    data = _data()
    strict = dict(RECIPIENT, require_batch_for_suspects=True, versions=["R3"])
    issues = e2b.check(data, "R2", strict)
    assert any("does not accept E2B(R2)" in i["message"] for i in issues)
    assert any(i["field"] == "products-p2-batch_number" for i in issues)


def test_broken_file_is_reported():
    problems, _ = e2b.check_file("<ichicsr><safetyreport>", "R2")
    assert problems and "not well formed" in problems[0]
    problems, _ = e2b.check_file("<ichicsr><ichicsrmessageheader/></ichicsr>", "R2")
    assert any("Safety report ID" in p for p in problems)


def test_file_names_say_draft():
    assert "DRAFT-not-for-submission" in e2b.file_name({"e2b_version": "R3", "export_version": 2, "status": "Draft"}, "ICSR/26 009")
