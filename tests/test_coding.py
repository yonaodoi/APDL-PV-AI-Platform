"""MedDRA/WHODrug coding, the local terminology and clickable completeness."""

import io
import zipfile

import pytest

from app.services import coding, icsr, terminology


def _app():
    from app import create_app
    from config import TestingConfig

    return create_app(TestingConfig)


# ------------------------------------------------------------- MedDRA files
# Synthetic rows in the MedDRA ASCII layout; the codes are invented for tests.

LLT = "90000001$Urticaria$90000101$$$$$$$Y$$\n90000002$Hives$90000101$$$$$$$Y$$\n90000008$Old term$90000101$$$$$$$N$$\n"
PT = "90000101$Urticaria$$90000201$$$$$$$$\n"
SOC = "90000201$Skin and subcutaneous tissue disorders$Skin$\n"
HIER = "90000101$90000301$90000401$90000201$Urticaria$Urticarias$Urticarias HLGT$Skin and subcutaneous tissue disorders$Skin$$90000201$Y$\n"


def _zip(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, text in files.items():
            z.writestr(f"MedAscii/{name}", text)
    return out.getvalue()


def test_meddra_release_is_read_from_a_zip_and_needs_every_file():
    files = terminology.collect_meddra_files([("release.zip", _zip({"llt.asc": LLT, "pt.asc": PT, "soc.asc": SOC,
                                                                   "mdhier.asc": HIER, "meddra_release.asc": "29.0$English$\n"}))])
    parsed = terminology.parse_meddra(files)
    assert parsed["version"] == "29.0"
    assert [(c, n, current) for c, n, _, current in parsed["llt"]] == [
        ("90000001", "Urticaria", True), ("90000002", "Hives", True), ("90000008", "Old term", False)]
    assert parsed["hierarchy"][0][-1] is True                      # primary SOC flag
    try:
        terminology.collect_meddra_files([("llt.asc", LLT.encode())])
    except terminology.DictionaryImportError as error:
        assert "pt.asc" in str(error)
    else:
        raise AssertionError("missing files were accepted")
    with pytest.raises(terminology.DictionaryImportError):
        terminology.parse_meddra({"llt.asc": "abc$Not a code$x\n", "pt.asc": PT, "soc.asc": SOC, "mdhier.asc": HIER})


def test_misspelt_wording_is_searched_by_fragments():
    patterns = terminology._fragments("uticaria")
    assert any(p.strip("%") in "urticaria" for p in patterns)
    assert terminology._score("uticaria", "Urticaria") >= 0.6


# ------------------------------------------------------------- automatic coding

def _item(kind="event", text="Urticaria", entity="reaction"):
    return {"entity": entity, "entity_id": 1, "verbatim": text, "kind": kind, **coding.ENTITY_SPECS[entity]}


def _no_local(monkeypatch):
    monkeypatch.setattr(terminology, "local_event_search", lambda text: [])
    monkeypatch.setattr(terminology, "local_product_search", lambda text: [])


def test_without_a_dictionary_the_required_messages_are_given(monkeypatch):
    _no_local(monkeypatch)
    monkeypatch.setattr(terminology, "active_version", lambda d: None)
    proposals = []
    monkeypatch.setattr(terminology, "propose_local_event", lambda term, src, uid: proposals.append(term) or {
        "dictionary": "Local", "code": "LOCAL-E-00009", "term": term, "version": "local", "method": "Local proposal",
        "score": 0.0, "status": "Proposed"})
    result = coding.suggest(_item(text="Itchy wheals"), dict(coding.DEFAULT_RULES), user_id=1)
    assert result["status"] == "Needs review"
    assert coding.MESSAGES["meddra_off"] in result["message"]
    assert coding.MESSAGES["no_match"] in result["message"]
    assert result["dictionary"] == "Local" and result["code"].startswith("LOCAL-")
    assert proposals == ["Itchy wheals"]

    result = coding.suggest(_item("drug", "Abpara", "product"), dict(coding.DEFAULT_RULES, propose_local=False), user_id=1)
    assert coding.MESSAGES["whodrug_off"] in result["message"] and result["code"] is None


def test_exact_match_is_accepted_only_when_the_rule_allows(monkeypatch):
    _no_local(monkeypatch)
    exact = {"dictionary": "MedDRA", "code": "90000001", "term": "Urticaria", "pt_code": "90000101", "pt_name": "Urticaria",
             "soc_code": "90000201", "soc_name": "Skin", "version": "29.0", "method": "Exact", "score": 1.0}
    monkeypatch.setattr(terminology, "active_version", lambda d: "29.0")
    monkeypatch.setattr(terminology, "meddra_exact", lambda text, version=None: exact)
    result = coding.suggest(_item(), dict(coding.DEFAULT_RULES))
    assert (result["status"], result["code"], result["pt_name"], result["version"]) == ("Coded", "90000001", "Urticaria", "29.0")
    result = coding.suggest(_item(), dict(coding.DEFAULT_RULES, auto_accept_exact=False))
    assert result["status"] == "Needs review" and "confirm" in result["message"]


def test_ambiguous_matches_wait_for_a_person(monkeypatch):
    _no_local(monkeypatch)
    monkeypatch.setattr(terminology, "active_version", lambda d: "29.0")
    monkeypatch.setattr(terminology, "meddra_exact", lambda text, version=None: None)
    monkeypatch.setattr(terminology, "synonym_lookup", lambda d, w: None)
    candidates = [{"dictionary": "MedDRA", "code": "90000001", "term": "Urticaria", "score": 0.82, "method": "Dictionary search"},
                  {"dictionary": "MedDRA", "code": "90000002", "term": "Hives", "score": 0.6, "method": "Dictionary search"}]
    monkeypatch.setattr(terminology, "meddra_search", lambda text, version=None: list(candidates))
    result = coding.suggest(_item(text="urticaria rash"), dict(coding.DEFAULT_RULES))
    assert result["status"] == "Needs review" and result["code"] is None
    assert [c["code"] for c in result["candidates"]] == ["90000001", "90000002"]


def test_ai_names_count_only_if_they_are_in_the_dictionary(monkeypatch):
    from app.services import llm

    monkeypatch.setattr(llm, "generate_text", lambda *a, **k: '{"names": ["Urticaria", "Made-up term 123"]}')
    monkeypatch.setattr(terminology, "meddra_exact",
                        lambda text, version=None: {"dictionary": "MedDRA", "code": "90000001", "term": "Urticaria"} if text == "Urticaria" else None)
    found = coding._ai_candidates("wheals all over", "event")
    assert [c["term"] for c in found] == ["Urticaria"] and "verified" in found[0]["method"]


def test_reactions_and_drugs_cannot_be_marked_not_coded(monkeypatch):
    monkeypatch.setattr(coding, "case_items", lambda case_id: [_item()])
    with pytest.raises(ValueError):
        coding.mark_not_coded(1, "reaction", 1, "no reason", 1)


def test_changed_wording_needs_coding_again(monkeypatch):
    monkeypatch.setattr(coding, "case_items", lambda case_id: [_item(text="Hives")])
    monkeypatch.setattr(coding, "case_codings", lambda case_id: {("reaction", 1): {
        "verbatim": "Urticaria", "status": "Coded", "code": "90000001", "message": None}})
    overview = coding.coding_overview(1)
    assert overview["items"][0]["coding"]["status"] == "Needs review"
    assert len(overview["open_required"]) == 1


# ------------------------------------------------------------- pathway and checklist

def test_pathway_coding_stage_lists_each_uncoded_required_item():
    from app.services import case_pathway

    case = {"case_id": 7, "validity_decision": "Valid", "workflow_status": "Assessment", "case_narrative": "x"}
    overview = {"open_required": [{"entity": "reaction", "entity_id": 3, "label": "Reaction/event", "verbatim": "Rash",
                                   "coding": {"status": "Needs review", "message": "2 MedDRA candidate(s) found"}}]}
    with _app().test_request_context():
        p = case_pathway.build_pathway(case, [], [{"reaction_id": 3, "reported_term": "Rash", "outcome": "Recovered"}],
                                       [], None, [], {}, coding=overview)
    coding_stage = next(s for s in p["stages"] if s["key"] == "coding")
    assert coding_stage["issues"][0]["href"].endswith("/cases/7/coding#item-reaction-3")
    assert "needs review" in coding_stage["issues"][0]["text"]


def test_field_checklist_points_at_exact_fields_and_respects_null_flavours():
    record = {
        "case": {"icsr_report_type": "Spontaneous report", "patient_sex": None, "null_flavours": {"patient_sex": "UNK"},
                 "patient_age_value": 30},
        "reporters": [{"qualification": "Physician"}],
        "products": [{"case_product_id": 4, "product_name": "ABPARA", "product_role": "Suspect", "route": "Intravenous",
                      "indication": "Fever", "start_text": "2026-09", "action_taken": None}],
        "reactions": [{"reaction_id": 9, "reported_term": "Rash", "start_text": None, "serious": "No", "outcome": None}],
    }
    checks = [{"check_code": "minimum_patient", "check_label": "Patient", "status": "Review", "message": "Initials missing"},
              {"check_code": "event_coding", "check_label": "Coding", "status": "Review", "message": "x"}]
    items = icsr.field_checks(record, checks)
    targets = {i["target"]: i["level"] for i in items}
    assert targets == {
        "minimum_patient": "required",
        "products-p4-action_taken": "recommended",
        "reactions-r9-start_text": "recommended",
        "reactions-r9-outcome": "required",
    }
    assert all(i["why"] for i in items)


def test_coding_pages_need_sign_in_and_admin_for_imports(monkeypatch):
    monkeypatch.setattr("app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0)
    client = _app().test_client()
    assert client.get("/cases/1/coding").status_code == 302
    with client.session_transaction() as s:
        s["user_id"], s["full_name"], s["role"] = 3, "Officer", "PV Officer"
    assert client.get("/administration/terminology/").status_code in (302, 403)
    assert client.post("/administration/terminology/meddra").status_code in (302, 403)
    with client.session_transaction() as s:
        s["role"] = "QPPV"
    assert client.post("/administration/terminology/rules").status_code in (302, 403)
