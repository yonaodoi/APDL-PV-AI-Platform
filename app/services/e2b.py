"""E2B(R2) and E2B(R3) ICSR XML export.

What this does
  * maps the structured ICSR record (sections A–I) to ICH E2B(R2)
    (DTD 2.1 ``ichicsr``) or E2B(R3) (HL7 v3 ``MCCI_IN200100UV01``),
  * uses the MedDRA coding of each reaction (LLT code and version) and the
    WHODrug coding of each drug (active ingredient names) recorded in the
    coding step,
  * checks the record against the required fields, code lists, date formats
    and the chosen recipient's rules before writing the file; every problem
    points at the field to correct,
  * checks the file itself: it must be well formed, contain every mandatory
    element and, when the official schema files are installed
    (instance/e2b/), pass validation against the ICH XSD (R3) or DTD (R2),
  * keeps every export as a numbered version with its issues, dictionary
    versions and a fingerprint of the file, and writes the audit trail.

What it does not do
  * It never sends anything to a regulator or partner. A file that passed
    the checks is "submission-ready", not "submitted" or "accepted";
    recipients still validate it on receipt (send it to their test gateway
    first).
  * The built-in rules follow the ICH implementation guides; recipient
    specific rules are those configured in Administration › E2B settings.
"""

import hashlib
import json
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from app.db import query_all, query_one, transaction

SETTINGS_KEY = "e2b_settings"
VERSIONS = ("R3", "R2")
STATUSES = ("Draft", "Checked", "Validated")

DEFAULT_SETTINGS = {
    "sender_identifier": "",          # the organisation identifier agreed with the recipient
    "sender_type": "1",               # C.3.1 / R2 sendertype: 1 = pharmaceutical company
    "sender_organisation": "",        # defaults to the company legal name
    "sender_department": "",
    "sender_country": "",             # ISO 3166 alpha-2
    "recipients": [
        {"key": "national", "name": "National medicines authority", "identifier": "", "versions": ["R2", "R3"],
         "require_all_reactions_meddra": True, "require_batch_for_suspects": False,
         "require_study_registration": False, "require_most_recent_date": True},
        {"key": "ema", "name": "EMA EudraVigilance", "identifier": "EVHUMAN", "versions": ["R3"],
         "require_all_reactions_meddra": True, "require_batch_for_suspects": False,
         "require_study_registration": True, "require_most_recent_date": True},
        {"key": "partner", "name": "Licence partner (business partner)", "identifier": "", "versions": ["R2", "R3"],
         "require_all_reactions_meddra": True, "require_batch_for_suspects": False,
         "require_study_registration": False, "require_most_recent_date": True},
    ],
}

# --------------------------------------------------------------------------
# Code lists (ICH E2B). Keep in one place so they can be checked against the
# ICH code list documents when a new version is published.
# --------------------------------------------------------------------------

REPORT_TYPE = {"Spontaneous report": "1", "Report from study": "2", "Other": "3",
               "Not available to sender (unknown)": "4"}
QUALIFICATION = {"Physician": "1", "Pharmacist": "2", "Other health professional": "3", "Lawyer": "4",
                 "Consumer or other non-health professional": "5"}
SEX = {"Male": "1", "Female": "2"}
ROLE = {"Suspect": "1", "Concomitant": "2", "Interacting": "3", "Drug not administered": "4"}
# R3 E.i.7 outcome / R2 reactionoutcome
OUTCOME_R3 = {"Recovered/resolved": "1", "Recovering/resolving": "2", "Not recovered/not resolved": "3",
              "Recovered/resolved with sequelae": "4", "Fatal": "5", "Unknown": "0"}
OUTCOME_R2 = {"Recovered/resolved": "1", "Recovering/resolving": "2", "Not recovered/not resolved": "3",
              "Recovered/resolved with sequelae": "4", "Fatal": "5", "Unknown": "6"}
# R3 G.k.8 / R2 actiondrug
ACTION_R3 = {"Drug withdrawn": "1", "Dose reduced": "2", "Dose increased": "3", "Dose not changed": "4",
             "Unknown": "0", "Not applicable": "9"}
ACTION_R2 = {"Drug withdrawn": "1", "Dose reduced": "2", "Dose increased": "3", "Dose not changed": "4",
             "Unknown": "5", "Not applicable": "6"}
# R3 age unit (UCUM) / R2 age unit code
AGE_UNIT_R3 = {"Years": "a", "Months": "mo", "Weeks": "wk", "Days": "d", "Hours": "h", "Decades": "10.a"}
AGE_UNIT_R2 = {"Decades": "800", "Years": "801", "Months": "802", "Weeks": "803", "Days": "804", "Hours": "805"}
AGE_GROUP = {"Foetus": "0", "Neonate": "1", "Infant": "2", "Child": "3", "Adolescent": "4", "Adult": "5", "Elderly": "6"}
AGE_GROUP_R2 = {"Neonate": "1", "Infant": "2", "Child": "3", "Adolescent": "4", "Adult": "5", "Elderly": "6"}
# R2 route of administration codes (E2B(M) attachment); text is used for R3.
ROUTE_R2 = {
    "auricular": "001", "buccal": "002", "cutaneous": "003", "dental": "004", "epidural": "008",
    "intra-arterial": "013", "intra-articular": "014", "intradermal": "023", "intramuscular": "030",
    "intraocular": "031", "intraperitoneal": "033", "intrathecal": "037", "intravenous bolus": "040",
    "intravenous drip": "041", "intravenous infusion": "041", "intravenous": "042", "iv": "042",
    "intravesical": "043", "nasal": "045", "ophthalmic": "047", "oral": "048", "po": "048",
    "other": "050", "parenteral": "051", "rectal": "054", "inhalation": "055", "respiratory (inhalation)": "055",
    "subcutaneous": "058", "sc": "058", "sublingual": "060", "topical": "061", "transdermal": "062",
    "unknown": "065", "urethral": "066", "vaginal": "067",
}
SERIOUSNESS = [  # (form field, R3 observation code, R2 element)
    ("crit_death", "34", "seriousnessdeath"),
    ("crit_life_threatening", "21", "seriousnesslifethreatening"),
    ("crit_hospitalisation", "33", "seriousnesshospitalization"),
    ("crit_disability", "35", "seriousnessdisabling"),
    ("crit_congenital_anomaly", "12", "seriousnesscongenitalanomali"),
    ("crit_other_medically_important", "26", "seriousnessother"),
]
NULL_FLAVOURS = {"UNK", "ASKU", "NASK", "NA", "MSK", "NI"}

# HL7 / ICH object identifiers used in R3.
OID = {
    "batch_id": "2.16.840.1.113883.3.989.2.1.3.22",
    "message_id": "2.16.840.1.113883.3.989.2.1.3.1",
    "worldwide_id": "2.16.840.1.113883.3.989.2.1.3.2",
    "sender_id": "2.16.840.1.113883.3.989.2.1.3.11",
    "receiver_id": "2.16.840.1.113883.3.989.2.1.3.12",
    "batch_sender": "2.16.840.1.113883.3.989.2.1.3.13",
    "batch_receiver": "2.16.840.1.113883.3.989.2.1.3.14",
    "interaction": "2.16.840.1.113883.1.6",
    "trigger": "2.16.840.1.113883.1.18",
    "act_code": "2.16.840.1.113883.5.4",
    "message_type": "2.16.840.1.113883.3.989.2.1.1.1",
    "report_type": "2.16.840.1.113883.3.989.2.1.1.2",
    "observation": "2.16.840.1.113883.3.989.2.1.1.19",
    "outcome": "2.16.840.1.113883.3.989.2.1.1.11",
    "qualification": "2.16.840.1.113883.3.989.2.1.1.6",
    "sender_type": "2.16.840.1.113883.3.989.2.1.1.7",
    "characterisation": "2.16.840.1.113883.3.989.2.1.1.13",
    "action": "2.16.840.1.113883.3.989.2.1.1.15",
    "age_group": "2.16.840.1.113883.3.989.2.1.1.9",
    "role_class": "2.16.840.1.113883.3.989.2.1.1.20",
    "meddra": "2.16.840.1.113883.6.163",
    "sex": "1.0.5218",
    "country": "1.0.3166.1.2.2",
}
R3_NS = "urn:hl7-org:v3"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


class ExportError(ValueError):
    pass


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

def load_settings():
    try:
        row = query_one("SELECT value FROM pv.app_settings WHERE setting_key = %s", (SETTINGS_KEY,))
    except Exception:
        _rollback()
        row = None
    value = (row or {}).get("value") or {}
    if isinstance(value, str):
        value = json.loads(value)
    settings = {**DEFAULT_SETTINGS, **{k: v for k, v in value.items() if k in DEFAULT_SETTINGS}}
    if not settings.get("recipients"):
        settings["recipients"] = DEFAULT_SETTINGS["recipients"]
    return settings


def save_settings(values, user_id):
    settings = {**load_settings(), **{k: v for k, v in values.items() if k in DEFAULT_SETTINGS}}
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.app_settings (setting_key, value, updated_by, updated_at)
            VALUES (%s, %s::jsonb, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (setting_key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            (SETTINGS_KEY, json.dumps(settings), user_id),
        )
    return settings


def recipient(settings, key):
    return next((r for r in settings["recipients"] if r.get("key") == key), None)


def schema_dir():
    from flask import current_app

    return Path(current_app.instance_path) / "e2b"


def schema_files():
    """The official schema files when installed: {'R3': path or None, 'R2': path or None}."""
    try:
        base = schema_dir()
    except RuntimeError:          # outside the application (tests, scripts)
        return {"R3": None, "R2": None}
    r3 = next(iter(sorted(base.glob("r3/**/MCCI_IN200100UV01.xsd"))), None) if base.exists() else None
    r2 = next(iter(sorted(list(base.glob("r2/**/*.dtd")))), None) if base.exists() else None
    return {"R3": r3, "R2": r2}


def _rollback():
    try:
        from app.db import get_db

        get_db().rollback()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Gathering the case
# --------------------------------------------------------------------------

def gather(case_id):
    from app.services import coding, icsr
    from app.services.company_profile import company

    record = icsr.load_icsr(case_id)
    if not record:
        raise ExportError("Case not found.")
    countries = {r["country_id"]: (r.get("iso_alpha2") or "").strip().upper()
                 for r in query_all("SELECT country_id, iso_alpha2 FROM pv.countries")}
    try:
        codings = coding.case_codings(case_id)
    except Exception:
        _rollback()
        codings = {}
    return {"record": record, "countries": countries, "codings": codings, "company": company()}


def _nf(row, field):
    value = (row or {}).get("null_flavours") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    flavour = value.get(field)
    return flavour if flavour in NULL_FLAVOURS else None


def _ts(partial):
    """Partial ISO text or date -> HL7 TS ('2026', '202609', '20260916')."""
    if not partial:
        return None
    return str(partial)[:10].replace("-", "")


def _r2_date(partial):
    """-> (value, format) with format 102 (CCYYMMDD), 610 (CCYYMM) or 602 (CCYY)."""
    text = _ts(partial)
    if not text:
        return None, None
    return text, {8: "102", 6: "610", 4: "602"}.get(len(text))


def _num(value):
    if value in (None, ""):
        return None
    text = f"{value}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _report_id(data):
    case = data["record"]["case"]
    settings = data["settings"]
    country = _primary_country(data) or (settings.get("sender_country") or "").upper() or "XX"
    sender = (settings.get("sender_identifier") or "").strip() or "SENDER"
    return f"{country}-{sender}-{case['case_number']}"


def _primary_country(data):
    record, countries = data["record"], data["countries"]
    case = record["case"]
    reporters = record.get("reporters") or []
    for cid in [r.get("country_id") for r in reporters] + [case.get("reporter_country_id"), case.get("country_id")]:
        if cid and countries.get(cid):
            return countries[cid]
    return None


def _primary_reporter(record):
    case = record["case"]
    reporters = record.get("reporters") or []
    primary = next((r for r in reporters if r.get("is_primary")), reporters[0] if reporters else None)
    if primary:
        return {"qualification": primary.get("qualification"), "given": primary.get("given_name"),
                "family": primary.get("family_name"), "title": primary.get("title"),
                "organisation": primary.get("organisation"), "country_id": primary.get("country_id"), "row": primary,
                "prefix": f"reporters-s{primary.get('case_reporter_id')}-"}
    return {"qualification": case.get("reporter_qualification"), "given": case.get("reporter_given_name"),
            "family": case.get("reporter_family_name") or case.get("reporter_name"), "title": case.get("reporter_title"),
            "organisation": case.get("reporter_organisation"), "country_id": case.get("reporter_country_id"),
            "row": case, "prefix": "case-reporter_"}


def _reaction_coding(data, reaction):
    cd = data["codings"].get(("reaction", reaction.get("reaction_id")))
    if cd and cd.get("dictionary") == "MedDRA" and cd.get("status") == "Coded" and cd.get("verbatim") == (reaction.get("reported_term") or "").strip():
        return cd
    return None


def _drug_coding(data, product):
    cd = data["codings"].get(("product", product.get("case_product_id")))
    if cd and cd.get("dictionary") == "WHODrug" and cd.get("status") == "Coded":
        return cd
    return None


def _serious(reaction):
    return reaction.get("serious") == "Yes" or any(reaction.get(k) for k, _, _ in SERIOUSNESS)


# --------------------------------------------------------------------------
# Checks on the record (before the file is written)
# --------------------------------------------------------------------------

def check(data, version, recipient_cfg):
    """Problems that stop a submission-ready file (errors) or need a look (warnings).
    Each: {level, field, message}. ``field`` is the ICSR form field to open
    ('coding:<entity>-<id>' for the coding step, 'settings' for E2B settings)."""
    issues = []
    record, settings = data["record"], data["settings"]
    case = record["case"]

    def err(field, message):
        issues.append({"level": "error", "field": field, "message": message})

    def warn(field, message):
        issues.append({"level": "warning", "field": field, "message": message})

    if not (settings.get("sender_identifier") or "").strip():
        err("settings", "The sender identifier (your organisation ID agreed with the recipient) is not set in E2B settings.")
    if recipient_cfg is None:
        err("settings", "Choose a recipient.")
    else:
        if version not in recipient_cfg.get("versions", VERSIONS):
            err("settings", f"{recipient_cfg['name']} does not accept E2B({version}); choose "
                + " or ".join(f"E2B({v})" for v in recipient_cfg.get("versions", [])) + ".")
        if not (recipient_cfg.get("identifier") or "").strip():
            err("settings", f"The receiver identifier for {recipient_cfg['name']} is not set in E2B settings.")

    # C. Report
    if not case.get("received_date"):
        err("case-received_date", "Date first received (C.1.4) is required.")
    if not case.get("most_recent_info_date") and (recipient_cfg or {}).get("require_most_recent_date", True):
        err("case-most_recent_info_date", "Date of most recent information (C.1.5) is required.")
    if case.get("icsr_report_type") not in REPORT_TYPE:
        err("case-icsr_report_type", "Choose the type of report (C.1.3).")
    if case.get("validity_decision") != "Valid":
        err("validity", "The case is not confirmed as a valid ICSR (patient, reporter, suspect drug, reaction).")
    if case.get("icsr_report_type") == "Report from study" and not case.get("study_identifier"):
        err("case-study_identifier", "A study report needs the study identifier (C.5.3).")
    if case.get("icsr_report_type") == "Report from study" and (recipient_cfg or {}).get("require_study_registration"):
        warn("case-study_identifier", "This recipient expects the study registration number and country (C.5.1); "
             "make sure the study identifier includes the registration number.")
    if case.get("case_category") == "Literature" and not (case.get("literature_reference") or case.get("literature_title")):
        err("case-literature_title", "A literature case needs the literature reference (C.4.r.1).")

    # C.2 Primary source
    reporter = _primary_reporter(record)
    if reporter["qualification"] not in QUALIFICATION and not _nf(reporter["row"], "qualification"):
        err(reporter["prefix"] + "qualification", "The primary source qualification (C.2.r.4) is required.")
    if not _primary_country(data):
        err(reporter["prefix"] + "country_id", "The primary source country (C.2.r.3) is required (and the country needs an ISO code).")

    # D. Patient – at least one identifying item (minimum criterion)
    if not any(case.get(k) for k in ("patient_initials", "patient_sex", "patient_age_value", "patient_dob_text",
                                      "patient_age_group")) and not _nf(case, "patient_initials"):
        err("case-patient_initials", "Give at least one patient identifier: initials, sex, age, age group or date of birth (D.1–D.5).")
    if case.get("patient_age_value") and case.get("patient_age_unit") not in AGE_UNIT_R2:
        err("case-patient_age_value", "Choose the unit of the patient's age.")
    if case.get("patient_died") == "Yes" and not case.get("death_date_text") and not _nf(case, "death_date_text"):
        warn("case-death_date_text", "The patient died: give the date of death (D.9.1) or why it is unknown.")

    # E. Reactions
    reactions = record.get("reactions") or []
    if not reactions:
        err("first:reactions-reported_term", "At least one reaction/event (E.i) is required.")
    meddra_versions = set()
    for r in reactions:
        key = f"reactions-r{r['reaction_id']}-"
        name = (r.get("reported_term") or "reaction")[:40]
        if not r.get("reported_term"):
            err(key + "reported_term", "Each reaction needs the wording as reported (E.i.1.1).")
        cd = _reaction_coding(data, r)
        if not cd:
            stored = data["codings"].get(("reaction", r["reaction_id"]))
            why = ("is coded to a local term, which E2B cannot carry" if stored and stored.get("dictionary") == "Local"
                   else "is not coded to MedDRA yet")
            (err if (recipient_cfg or {}).get("require_all_reactions_meddra", True) else warn)(
                f"coding:reaction-{r['reaction_id']}", f"“{name}” {why} (E.i.2.1 needs a MedDRA LLT code and version).")
        else:
            meddra_versions.add(cd.get("version"))
        if not r.get("outcome") and not _nf(r, "outcome"):
            err(key + "outcome", f"The outcome of “{name}” (E.i.7) is required.")
        if r.get("serious") == "Yes" and not any(r.get(k) for k, _, _ in SERIOUSNESS):
            err(key + "serious", f"“{name}” is serious: tick the seriousness criteria (E.i.3.2).")
        if r.get("outcome") == "Fatal" and case.get("patient_died") != "Yes":
            warn("case-patient_died", f"“{name}” has a fatal outcome but the patient is not recorded as died (D.9).")
    if len(meddra_versions) > 1:
        warn("coding", "Reactions are coded with different MedDRA versions (" + ", ".join(sorted(v for v in meddra_versions if v))
             + "); recode them to one version before sending.")

    # G. Drugs
    products = record.get("products") or []
    suspects = [p for p in products if (p.get("product_role") or "Suspect") in ("Suspect", "Interacting")]
    if not suspects:
        err("first:products-product_name", "At least one suspect or interacting drug (G.k.1) is required.")
    for p in products:
        key = f"products-p{p['case_product_id']}-"
        name = (p.get("product_name") or "drug")[:40]
        if not p.get("product_name"):
            err(key + "product_name", "Each drug needs its name as reported (G.k.2.2).")
        if p.get("product_role") and p["product_role"] not in ROLE:
            err(key + "product_role", f"Choose the role of {name} (G.k.1).")
        if version == "R2" and p.get("route") and _route_r2(p["route"]) is None:
            warn(key + "route", f"The route “{p['route']}” of {name} has no E2B(R2) route code; it is sent as “Other” (050). "
                 "Use a standard route name such as Oral or Intravenous.")
        if p in suspects and (recipient_cfg or {}).get("require_batch_for_suspects") and not p.get("batch_number") \
                and not _nf(p, "batch_number"):
            err(key + "batch_number", f"This recipient requires the batch number of suspect drug {name} (G.k.4.r.7).")
        if p in suspects and not _drug_coding(data, p):
            warn(f"coding:product-{p['case_product_id']}", f"{name} is not coded to WHODrug; the active ingredient is taken from the record as reported.")
        if p.get("dose_value") and not p.get("dose_unit"):
            err(key + "dose_unit", f"Give the unit of the dose of {name} (G.k.4.r.1b).")

    # H. Narrative
    if not (case.get("case_narrative") or "").strip():
        err("narrative", "The case narrative (H.1) is required.")
    else:
        try:
            from app.services.narratives import review_state

            if review_state(case["case_id"], case.get("case_narrative")) == "unreviewed":
                warn("narrative", "The narrative has not been reviewed by a second person yet.")
        except Exception:
            _rollback()

    # Field lengths (the longest limits in the ICH guide)
    limits = [("case-patient_initials", case.get("patient_initials"), 60, "Patient initials (D.1)"),
              ("case-case_narrative", case.get("case_narrative"), 100000 if version == "R3" else 20000, "Narrative (H.1)")]
    for field, value, limit, label in limits:
        if value and len(value) > limit:
            err(field, f"{label} is longer than E2B({version}) allows ({limit} characters).")

    # Sign-off
    if case.get("approval_stage") != "Approved":
        err("approval", "The case has not been approved in sign-off yet. A file made now is a draft for review; approve the case to make a submission-ready file.")
    return issues


def _route_r2(route):
    text = re.sub(r"\s+", " ", (route or "").strip().lower())
    return ROUTE_R2.get(text)


# --------------------------------------------------------------------------
# E2B(R3)
# --------------------------------------------------------------------------

def _el(parent, tag, text=None, **attrs):
    element = ET.SubElement(parent, f"{{{R3_NS}}}{tag}", {k.rstrip("_").replace("__", ":"): str(v) for k, v in attrs.items() if v is not None})
    if text is not None:
        element.text = str(text)
    return element


def _xsi(element, kind):
    element.set(f"{{{XSI}}}type", kind)
    return element


def _null(element, flavour):
    element.set("nullFlavor", flavour)
    return element


def _obs(parent, obs_code, display, value_type=None, **value):
    """subjectOf2/observation or outboundRelationship2/observation with a value."""
    observation = _el(parent, "observation", classCode="OBS", moodCode="EVN")
    _el(observation, "code", code=obs_code, codeSystem=OID["observation"], displayName=display)
    if value_type:
        v = _el(observation, "value")
        _xsi(v, value_type)
        for key, val in value.items():
            if val is not None:
                v.set(key, str(val))
    return observation


def build_r3(data, recipient_cfg, message_number, now):
    record, settings, company = data["record"], data["settings"], data["company"]
    case = record["case"]
    ET.register_namespace("", R3_NS)
    ET.register_namespace("xsi", XSI)
    stamp = now.strftime("%Y%m%d%H%M%S")
    report_id = _report_id(data)

    root = ET.Element(f"{{{R3_NS}}}MCCI_IN200100UV01", {"ITSVersion": "XML_1.0"})
    root.set(f"{{{XSI}}}schemaLocation", "urn:hl7-org:v3 MCCI_IN200100UV01.xsd")
    _el(root, "id", extension=f"{settings['sender_identifier']}-B{message_number}", root=OID["batch_id"])   # N.1.2
    _el(root, "creationTime", value=stamp)                                                                  # N.1.5
    _el(root, "responseModeCode", code="D")
    _el(root, "interactionId", root=OID["interaction"], extension="MCCI_IN200100UV01")
    _el(root, "name", code="1", codeSystem=OID["message_type"], displayName="ichicsr")                     # N.1.1

    msg = _el(root, "PORR_IN049016UV")
    _el(msg, "id", extension=f"{settings['sender_identifier']}-{message_number}", root=OID["message_id"])  # N.2.r.1
    _el(msg, "creationTime", value=stamp)                                                                  # N.2.r.4
    _el(msg, "interactionId", root=OID["interaction"], extension="PORR_IN049016UV")
    _el(msg, "processingCode", code="P")
    _el(msg, "processingModeCode", code="T")
    _el(msg, "acceptAckCode", code="AL")
    receiver = _el(_el(msg, "receiver", typeCode="RCV"), "device", classCode="DEV", determinerCode="INSTANCE")
    _el(receiver, "id", extension=recipient_cfg.get("identifier"), root=OID["receiver_id"])               # N.2.r.3
    sender = _el(_el(msg, "sender", typeCode="SND"), "device", classCode="DEV", determinerCode="INSTANCE")
    _el(sender, "id", extension=settings["sender_identifier"], root=OID["sender_id"])                       # N.2.r.2

    act = _el(msg, "controlActProcess", classCode="CACT", moodCode="EVN")
    _el(act, "code", code="PORR_TE049016UV", codeSystem=OID["trigger"])
    _el(act, "effectiveTime", value=stamp)                                                                 # C.1.2
    event = _el(_el(act, "subject", typeCode="SUBJ"), "investigationEvent", classCode="INVSTG", moodCode="EVN")
    _el(event, "id", extension=report_id, root=OID["message_id"])                                          # C.1.1
    _el(event, "id", extension=report_id, root=OID["worldwide_id"])                                        # C.1.8.1
    _el(event, "code", code="PAT_ADV_EVNT", codeSystem=OID["act_code"])
    _el(event, "text", case.get("case_narrative") or "")                                                   # H.1
    _el(event, "statusCode", code="active")
    _el(_el(event, "effectiveTime"), "low", value=_ts(case.get("received_date")))                          # C.1.4
    _el(event, "availabilityTime", value=_ts(case.get("most_recent_info_date") or case.get("received_date")))  # C.1.5

    # C.4 literature
    literature = case.get("literature_reference") or " ".join(
        x for x in (case.get("literature_authors"), case.get("literature_title"), case.get("literature_journal"),
                    case.get("literature_year"), case.get("literature_doi")) if x)
    if literature:
        ref = _el(_el(event, "reference", typeCode="REFR"), "document", classCode="DOC", moodCode="EVN")
        _el(ref, "code", code="2", codeSystem="2.16.840.1.113883.3.989.2.1.1.27", displayName="literatureReference")
        _el(ref, "bibliographicDesignationText", literature[:500])

    assessment = _el(_el(event, "component", typeCode="COMP"), "adverseEventAssessment", classCode="INVSTG", moodCode="EVN")
    role = _el(_el(assessment, "subject1", typeCode="SBJ"), "primaryRole", classCode="INVSBJ")
    person = _el(role, "player1", classCode="PSN", determinerCode="INSTANCE")
    initials = _el(person, "name")
    if case.get("patient_initials"):
        initials.text = case["patient_initials"]
    else:
        _null(initials, _nf(case, "patient_initials") or "UNK")
    sex = case.get("patient_sex")
    gender = _el(person, "administrativeGenderCode")
    if sex in SEX:
        gender.set("code", SEX[sex]); gender.set("codeSystem", OID["sex"])
    else:
        _null(gender, _nf(case, "patient_sex") or "UNK")
    if case.get("patient_dob_text"):
        _el(person, "birthTime", value=_ts(case["patient_dob_text"]))                                      # D.2.1
    if case.get("patient_died") == "Yes":
        if case.get("death_date_text"):
            _el(person, "deceasedTime", value=_ts(case["death_date_text"]))                               # D.9.1
        else:
            _null(_el(person, "deceasedTime"), _nf(case, "death_date_text") or "UNK")

    # D.7 medical history and D.8 past drugs
    history = [h for h in record.get("history") or [] if not h.get("for_parent")]
    if history or record.get("past_drugs"):
        organizer = _el(_el(role, "subjectOf2", typeCode="SBJ"), "organizer", classCode="CATEGORY", moodCode="EVN")
        _el(organizer, "code", code="1", codeSystem="2.16.840.1.113883.3.989.2.1.1.20", displayName="relevantMedicalHistoryAndConcurrentConditions")
        for h in history:
            cd = data["codings"].get(("history", h.get("history_id")))
            obs = _el(_el(organizer, "component", typeCode="COMP"), "observation", classCode="OBS", moodCode="EVN")
            code = _el(obs, "code")
            if cd and cd.get("dictionary") == "MedDRA" and cd.get("status") == "Coded":
                code.set("code", cd["code"]); code.set("codeSystem", OID["meddra"]); code.set("codeSystemVersion", cd.get("version") or "")
            else:
                _null(code, "UNK")
            _el(code, "originalText", h.get("condition_text") or "")
            eff = _xsi(_el(obs, "effectiveTime"), "IVL_TS")
            if h.get("start_text"):
                _el(eff, "low", value=_ts(h["start_text"]))
            if h.get("end_text"):
                _el(eff, "high", value=_ts(h["end_text"]))
            if h.get("continuing") in ("Yes", "No"):
                _obs(_el(obs, "inboundRelationship", typeCode="REFR"), "13", "continuing", "BL",
                     value="true" if h["continuing"] == "Yes" else "false")
    if case.get("medical_history"):
        _obs(_el(role, "subjectOf2", typeCode="SBJ"), "18", "historyAndConcurrentConditionText", "ED").find(
            f"{{{R3_NS}}}value").text = case["medical_history"][:10000]                                     # D.7.2

    # D.2.2 age / D.2.3 age group / D.3 weight / D.4 height / D.6 LMP
    if case.get("patient_age_value") and case.get("patient_age_unit") in AGE_UNIT_R3:
        _obs(_el(role, "subjectOf2", typeCode="SBJ"), "3", "age", "PQ",
             value=_num(case["patient_age_value"]), unit=AGE_UNIT_R3[case["patient_age_unit"]])
    if case.get("patient_age_group") in AGE_GROUP:
        _obs(_el(role, "subjectOf2", typeCode="SBJ"), "4", "ageGroup", "CE",
             code=AGE_GROUP[case["patient_age_group"]], codeSystem=OID["age_group"])
    if case.get("patient_weight_value"):
        unit = {"kg": "kg", "g": "g", "lb": "[lb_av]"}.get(case.get("patient_weight_unit") or "kg", "kg")
        _obs(_el(role, "subjectOf2", typeCode="SBJ"), "7", "bodyWeight", "PQ", value=_num(case["patient_weight_value"]), unit=unit)
    if case.get("patient_height_value"):
        unit = {"cm": "cm", "m": "m", "in": "[in_i]"}.get(case.get("patient_height_unit") or "cm", "cm")
        _obs(_el(role, "subjectOf2", typeCode="SBJ"), "17", "height", "PQ", value=_num(case["patient_height_value"]), unit=unit)
    if case.get("patient_lmp_text"):
        _obs(_el(role, "subjectOf2", typeCode="SBJ"), "22", "lastMenstrualPeriodDate", "TS", value=_ts(case["patient_lmp_text"]))

    # E. Reactions
    reaction_ids = {}
    for r in record.get("reactions") or []:
        obs = _el(_el(role, "subjectOf2", typeCode="SBJ"), "observation", classCode="OBS", moodCode="EVN")
        rid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{report_id}/reaction/{r['reaction_id']}"))
        reaction_ids[r["reaction_id"]] = rid
        _el(obs, "id", root=rid)
        _el(obs, "code", code="29", codeSystem=OID["observation"], displayName="reaction")
        eff = _xsi(_el(obs, "effectiveTime"), "IVL_TS")
        if r.get("start_text"):
            _el(eff, "low", value=_ts(r["start_text"]))                                                     # E.i.4
        if r.get("end_text"):
            _el(eff, "high", value=_ts(r["end_text"]))                                                      # E.i.5
        value = _xsi(_el(obs, "value"), "CE")
        cd = _reaction_coding(data, r)
        if cd:
            value.set("code", cd["code"]); value.set("codeSystem", OID["meddra"]); value.set("codeSystemVersion", cd.get("version") or "")
        _el(value, "originalText", r.get("reported_term") or "")                                         # E.i.1.1
        for field, code, display in [(k, c, k[5:]) for k, c, _ in SERIOUSNESS]:
            rel = _el(obs, "outboundRelationship2", typeCode="PERT")
            o = _obs(rel, code, display, "BL")
            v = o.find(f"{{{R3_NS}}}value")
            if r.get(field):
                v.set("value", "true")
            else:
                _null(v, "NI")                                                                              # E.i.3.2
        if r.get("outcome") in OUTCOME_R3:
            _obs(_el(obs, "outboundRelationship2", typeCode="PERT"), "27", "outcome", "CE",
                 code=OUTCOME_R3[r["outcome"]], codeSystem=OID["outcome"])                                  # E.i.7
        if r.get("medically_confirmed") in ("Yes", "No"):
            _obs(_el(obs, "outboundRelationship2", typeCode="PERT"), "24", "medicalConfirmationByHealthProfessional",
                 "BL", value="true" if r["medically_confirmed"] == "Yes" else "false")                     # E.i.8

    # F. Tests
    for t in record.get("tests") or []:
        organizer = _el(_el(role, "subjectOf2", typeCode="SBJ"), "organizer", classCode="CATEGORY", moodCode="EVN")
        _el(organizer, "code", code="3", codeSystem="2.16.840.1.113883.3.989.2.1.1.20", displayName="testsAndProceduresRelevantToTheInvestigation")
        obs = _el(_el(organizer, "component", typeCode="COMP"), "observation", classCode="OBS", moodCode="EVN")
        code = _el(obs, "code")
        cd = data["codings"].get(("test", t.get("test_id")))
        if cd and cd.get("dictionary") == "MedDRA" and cd.get("status") == "Coded":
            code.set("code", cd["code"]); code.set("codeSystem", OID["meddra"]); code.set("codeSystemVersion", cd.get("version") or "")
        else:
            _null(code, "UNK")
        _el(code, "originalText", t.get("test_name") or "")                                              # F.r.2.1
        if t.get("test_date_text"):
            _el(obs, "effectiveTime", value=_ts(t["test_date_text"]))                                      # F.r.1
        result = (t.get("result_text") or "").strip()
        if re.fullmatch(r"-?\d+(\.\d+)?", result):
            v = _xsi(_el(obs, "value"), "IVL_PQ")
            _el(v, "center", value=result, unit=t.get("result_unit") or "1")                              # F.r.3.2
        elif result:
            _xsi(_el(obs, "value"), "ED").text = result                                                    # F.r.3.4
        for bound, code in (("normal_low", "L"), ("normal_high", "H")):
            if t.get(bound):
                rng = _el(_el(obs, "referenceRange", typeCode="REFV"), "observationRange", classCode="OBS", moodCode="EVN.CRT")
                _xsi(_el(rng, "value"), "PQ").attrib.update({"value": str(t[bound]), "unit": t.get("result_unit") or "1"})
                _el(rng, "interpretationCode", code=code, codeSystem="2.16.840.1.113883.5.83")   # F.r.4 / F.r.5

    # G. Drugs
    drug_ids = {}
    for p in record.get("products") or []:
        organizer = _el(_el(role, "subjectOf2", typeCode="SBJ"), "organizer", classCode="CATEGORY", moodCode="EVN")
        _el(organizer, "code", code="4", codeSystem="2.16.840.1.113883.3.989.2.1.1.20", displayName="drugInformation")
        admin = _el(_el(organizer, "component", typeCode="COMP"), "substanceAdministration", classCode="SBADM", moodCode="EVN")
        did = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{report_id}/drug/{p['case_product_id']}"))
        drug_ids[p["case_product_id"]] = did
        _el(admin, "id", root=did)
        if p.get("dose") or p.get("frequency"):
            _el(admin, "text", " ".join(x for x in (p.get("dose"), p.get("frequency")) if x))              # G.k.4.r.8
        eff = _xsi(_el(admin, "effectiveTime"), "IVL_TS")
        if p.get("start_text"):
            _el(eff, "low", value=_ts(p["start_text"]))                                                     # G.k.4.r.4
        if p.get("end_text"):
            _el(eff, "high", value=_ts(p["end_text"]))                                                      # G.k.4.r.5
        if p.get("route"):
            route = _el(admin, "routeCode")
            _el(route, "originalText", p["route"])                                                         # G.k.4.r.10.1
        if p.get("dose_value"):
            _el(admin, "doseQuantity", value=_num(p["dose_value"]), unit=p.get("dose_unit") or "1")        # G.k.4.r.1
        product = _el(_el(_el(admin, "consumable", typeCode="CSM"), "instanceOfKind", classCode="INST"),
                      "kindOfProduct", classCode="MMAT", determinerCode="KIND")
        _el(product, "name", p.get("product_name") or "")                                                 # G.k.2.2
        if p.get("batch_number"):
            _el(_el(admin.find(f"{{{R3_NS}}}consumable/{{{R3_NS}}}instanceOfKind"), "productInstanceInstance",
                    classCode="MMAT", determinerCode="INSTANCE"), "lotNumberText", p["batch_number"])     # G.k.4.r.7
        coded = _drug_coding(data, p)
        ingredients = (coded or {}).get("pt_name") or p.get("generic_name")
        if ingredients:
            for name in [x.strip() for x in re.split(r"[;,/+]", ingredients) if x.strip()][:10]:
                ingredient = _el(product, "ingredient", classCode="ACTI")
                _el(_el(ingredient, "ingredientSubstance", classCode="MMAT", determinerCode="KIND"), "name", name)  # G.k.2.3.r.1
        if p.get("indication"):
            ind = _el(_el(admin, "inboundRelationship", typeCode="RSON"), "observation", classCode="OBS", moodCode="EVN")
            _el(ind, "code", code="19", codeSystem=OID["observation"], displayName="indication")
            v = _xsi(_el(ind, "value"), "CE")
            icd = data["codings"].get(("product_indication", p.get("case_product_id")))
            if icd and icd.get("dictionary") == "MedDRA" and icd.get("status") == "Coded":
                v.set("code", icd["code"]); v.set("codeSystem", OID["meddra"]); v.set("codeSystemVersion", icd.get("version") or "")
            _el(v, "originalText", p["indication"])                                                        # G.k.7.r.1
        if p.get("action_taken") in ACTION_R3:
            act_obs = _el(_el(admin, "outboundRelationship2", typeCode="PERT"), "act", classCode="ACT", moodCode="EVN")
            _el(act_obs, "code", code=ACTION_R3[p["action_taken"]], codeSystem=OID["action"])            # G.k.8

    # G.k.1 characterisation and G.k.9.i.2 causality
    for p in record.get("products") or []:
        causal = _el(_el(assessment, "component", typeCode="COMP"), "causalityAssessment", classCode="OBS", moodCode="EVN")
        _el(causal, "code", code="20", codeSystem=OID["observation"], displayName="interventionCharacterization")
        _xsi(_el(causal, "value"), "CE").attrib.update({"code": ROLE.get(p.get("product_role") or "Suspect", "1"),
                                                        "codeSystem": OID["characterisation"]})
        _el(_el(_el(causal, "subject2", typeCode="SUBJ"), "productUseReference", classCode="SBADM", moodCode="EVN"), "id",
            root=drug_ids[p["case_product_id"]])
    for c in record.get("causality") or []:
        if c.get("is_ai_draft") or c.get("source") == "AI-assisted draft" or not c.get("result"):
            continue
        if c.get("case_product_id") not in drug_ids or c.get("reaction_id") not in reaction_ids:
            continue
        causal = _el(_el(assessment, "component", typeCode="COMP"), "causalityAssessment", classCode="OBS", moodCode="EVN")
        _el(causal, "code", code="39", codeSystem=OID["observation"], displayName="causality")
        _xsi(_el(causal, "value"), "ST").text = c["result"]                                               # G.k.9.i.2.r.3
        if c.get("method"):
            _el(causal, "methodCode").append(_text_el("originalText", c["method"]))                       # G.k.9.i.2.r.2
        author = _el(_el(causal, "author", typeCode="AUT"), "assignedEntity", classCode="ASSIGNED")
        _el(_el(author, "code"), "originalText", c.get("source") or "")                                    # G.k.9.i.2.r.1
        _el(_el(_el(causal, "subject1", typeCode="SUBJ"), "adverseEffectReference", classCode="OBS", moodCode="EVN"), "id",
            root=reaction_ids[c["reaction_id"]])
        _el(_el(_el(causal, "subject2", typeCode="SUBJ"), "productUseReference", classCode="SBADM", moodCode="EVN"), "id",
            root=drug_ids[c["case_product_id"]])

    # H.2–H.4 comments
    for code, display, value in (("10", "comment", case.get("reporter_comments")),
                                 ("15", "senderDiagnosis", case.get("company_diagnosis")),
                                 ("11", "senderComment", case.get("company_comments"))):
        if value:
            _obs(_el(assessment, "component1", typeCode="COMP"), code, display, "ED").find(f"{{{R3_NS}}}value").text = value[:20000]

    # C.1.3 report type, C.1.6/C.1.7 and C.2 primary source, C.3 sender
    char = _el(_el(event, "subjectOf2", typeCode="SUBJ"), "investigationCharacteristic", classCode="OBS", moodCode="EVN")
    _el(char, "code", code="1", codeSystem="2.16.840.1.113883.3.989.2.1.1.23", displayName="ichReportType")
    _xsi(_el(char, "value"), "CE").attrib.update({"code": REPORT_TYPE.get(case.get("icsr_report_type"), "4"),
                                                  "codeSystem": OID["report_type"]})
    if case.get("expedited_criteria") in ("Yes", "No"):
        char = _el(_el(event, "subjectOf2", typeCode="SUBJ"), "investigationCharacteristic", classCode="OBS", moodCode="EVN")
        _el(char, "code", code="3", codeSystem="2.16.840.1.113883.3.989.2.1.1.23", displayName="localCriteriaForExpedited")
        _xsi(_el(char, "value"), "BL").set("value", "true" if case["expedited_criteria"] == "Yes" else "false")  # C.1.7

    reporter = _primary_reporter(record)
    source = _el(_el(event, "outboundRelationship", typeCode="SPRT"), "relatedInvestigation", classCode="INVSTG", moodCode="EVN")
    _el(source, "code", code="2", codeSystem="2.16.840.1.113883.3.989.2.1.1.22", displayName="sourceReport")
    entity = _el(_el(_el(source, "subjectOf2", typeCode="SUBJ"), "controlActEvent", classCode="CACT", moodCode="EVN"),
                 "author", typeCode="AUT")
    assigned = _el(entity, "assignedEntity", classCode="ASSIGNED")
    if reporter.get("organisation"):
        _el(_el(assigned, "representedOrganization", classCode="ORG", determinerCode="INSTANCE"), "name", reporter["organisation"])
    who = _el(assigned, "assignedPerson", classCode="PSN", determinerCode="INSTANCE")
    name = _el(who, "name")
    for part, value in (("prefix", reporter.get("title")), ("given", reporter.get("given")), ("family", reporter.get("family"))):
        if value:
            _el(name, part, value)
    country = data["countries"].get(reporter.get("country_id")) or _primary_country(data)
    if country:
        _el(_el(_el(who, "asLocatedEntity", classCode="LOCE"), "location", classCode="COUNTRY", determinerCode="INSTANCE"),
            "code", code=country, codeSystem=OID["country"])                                               # C.2.r.3
    if reporter["qualification"] in QUALIFICATION:
        _el(assigned, "code", code=QUALIFICATION[reporter["qualification"]], codeSystem=OID["qualification"])  # C.2.r.4

    sender_role = _el(_el(_el(event, "subjectOf1", typeCode="SUBJ"), "controlActEvent", classCode="CACT", moodCode="EVN"),
                      "author", typeCode="AUT")
    sender_entity = _el(sender_role, "assignedEntity", classCode="ASSIGNED")
    _el(sender_entity, "code", code=settings.get("sender_type") or "1", codeSystem=OID["sender_type"])   # C.3.1
    _el(_el(sender_entity, "representedOrganization", classCode="ORG", determinerCode="INSTANCE"), "name",
        settings.get("sender_organisation") or company.get("legal_name"))                                 # C.3.2
    return ET.tostring(root, encoding="unicode", xml_declaration=False)


def _text_el(tag, text):
    element = ET.Element(f"{{{R3_NS}}}{tag}")
    element.text = text
    return element


# --------------------------------------------------------------------------
# E2B(R2)
# --------------------------------------------------------------------------

def _sub(parent, tag, text=None):
    element = ET.SubElement(parent, tag)
    if text not in (None, ""):
        element.text = str(text)
    return element


def _r2_date_pair(parent, tag, partial):
    value, fmt = _r2_date(partial)
    if value:
        _sub(parent, tag + "format", fmt)
        _sub(parent, tag, value)


def build_r2(data, recipient_cfg, message_number, now):
    record, settings, company = data["record"], data["settings"], data["company"]
    case = record["case"]
    report_id = _report_id(data)
    root = ET.Element("ichicsr", {"lang": "en"})
    header = _sub(root, "ichicsrmessageheader")
    _sub(header, "messagetype", "ichicsr")
    _sub(header, "messageformatversion", "2.1")
    _sub(header, "messageformatrelease", "2.0")
    _sub(header, "messagenumb", f"{settings['sender_identifier']}-{message_number}")
    _sub(header, "messagesenderidentifier", settings["sender_identifier"])
    _sub(header, "messagereceiveridentifier", recipient_cfg.get("identifier"))
    _sub(header, "messagedateformat", "204")
    _sub(header, "messagedate", now.strftime("%Y%m%d%H%M%S"))

    report = _sub(root, "safetyreport")
    _sub(report, "safetyreportversion", str(message_number))
    _sub(report, "safetyreportid", report_id)
    country = _primary_country(data)
    _sub(report, "primarysourcecountry", country)
    _sub(report, "occurcountry", country)
    _sub(report, "transmissiondateformat", "102")
    _sub(report, "transmissiondate", now.strftime("%Y%m%d"))
    _sub(report, "reporttype", REPORT_TYPE.get(case.get("icsr_report_type"), "4"))
    reactions = record.get("reactions") or []
    serious = any(_serious(r) for r in reactions)
    _sub(report, "serious", "1" if serious else "2")
    for field, _, element in SERIOUSNESS:
        _sub(report, element, "1" if any(r.get(field) for r in reactions) else "2")
    _r2_date_pair(report, "receivedate", case.get("received_date"))
    _r2_date_pair(report, "receiptdate", case.get("most_recent_info_date") or case.get("received_date"))
    _sub(report, "additionaldocument", "2")
    _sub(report, "fulfillexpeditecriteria", "1" if case.get("expedited_criteria") == "Yes" else "2")
    _sub(report, "companynumb", report_id)

    reporter = _primary_reporter(record)
    source = _sub(report, "primarysource")
    _sub(source, "reportertitle", reporter.get("title"))
    _sub(source, "reportergivename", reporter.get("given"))
    _sub(source, "reporterfamilyname", reporter.get("family"))
    _sub(source, "reporterorganization", reporter.get("organisation"))
    _sub(source, "reportercountry", data["countries"].get(reporter.get("country_id")) or country)
    _sub(source, "qualification", QUALIFICATION.get(reporter["qualification"]))
    literature = case.get("literature_reference") or " ".join(
        x for x in (case.get("literature_authors"), case.get("literature_title"), case.get("literature_journal"),
                    case.get("literature_year"), case.get("literature_doi")) if x)
    if literature:
        _sub(source, "literaturereference", literature[:500])
    if case.get("study_identifier"):
        _sub(source, "studyname", case["study_identifier"][:100])

    sender = _sub(report, "sender")
    _sub(sender, "sendertype", settings.get("sender_type") or "1")
    _sub(sender, "senderorganization", (settings.get("sender_organisation") or company.get("legal_name") or "")[:60])
    _sub(sender, "senderdepartment", (settings.get("sender_department") or company.get("department") or "")[:60])
    _sub(sender, "sendercountrycode", (settings.get("sender_country") or "").upper() or None)
    _sub(sender, "senderemailaddress", company.get("pv_email"))
    receiver = _sub(report, "receiver")
    _sub(receiver, "receivertype", "2" if recipient_cfg.get("key") in ("ema", "national") else "6")
    _sub(receiver, "receiverorganization", recipient_cfg.get("name"))

    patient = _sub(report, "patient")
    _sub(patient, "patientinitial", case.get("patient_initials"))
    _r2_date_pair(patient, "patientbirthdate", case.get("patient_dob_text"))
    if case.get("patient_age_value") and case.get("patient_age_unit") in AGE_UNIT_R2:
        _sub(patient, "patientonsetage", _num(case["patient_age_value"]))
        _sub(patient, "patientonsetageunit", AGE_UNIT_R2[case["patient_age_unit"]])
    _sub(patient, "patientagegroup", AGE_GROUP_R2.get(case.get("patient_age_group")))
    if case.get("patient_weight_value"):
        weight = float(case["patient_weight_value"])
        weight = weight * 0.45359237 if case.get("patient_weight_unit") == "lb" else weight / 1000 if case.get("patient_weight_unit") == "g" else weight
        _sub(patient, "patientweight", _num(round(weight, 1)))
    if case.get("patient_height_value"):
        height = float(case["patient_height_value"])
        height = height * 100 if case.get("patient_height_unit") == "m" else height * 2.54 if case.get("patient_height_unit") == "in" else height
        _sub(patient, "patientheight", _num(round(height)))
    _sub(patient, "patientsex", SEX.get(case.get("patient_sex")))
    _r2_date_pair(patient, "patientlastmenstrualdate", case.get("patient_lmp_text"))
    _sub(patient, "patientmedicalhistorytext", (case.get("medical_history") or "")[:10000] or None)
    for h in [h for h in record.get("history") or [] if not h.get("for_parent")]:
        episode = _sub(patient, "medicalhistoryepisode")
        cd = data["codings"].get(("history", h.get("history_id")))
        if cd and cd.get("dictionary") == "MedDRA" and cd.get("status") == "Coded":
            _sub(episode, "patientepisodenamemeddraversion", cd.get("version"))
            _sub(episode, "patientepisodename", cd["code"])
        else:
            _sub(episode, "patientepisodename", h.get("condition_text"))
        _r2_date_pair(episode, "patientmedicalstartdate", h.get("start_text"))
        _sub(episode, "patientmedicalcontinue", {"Yes": "1", "No": "2"}.get(h.get("continuing"), "3"))
        _r2_date_pair(episode, "patientmedicalenddate", h.get("end_text"))
        _sub(episode, "patientmedicalcomment", h.get("comments"))
    for d in [d for d in record.get("past_drugs") or [] if not d.get("for_parent")]:
        past = _sub(patient, "patientpastdrugtherapy")
        _sub(past, "patientdrugname", d.get("drug_name"))
        _r2_date_pair(past, "patientdrugstartdate", d.get("start_text"))
        _r2_date_pair(past, "patientdrugenddate", d.get("end_text"))
    if case.get("patient_died") == "Yes":
        death = _sub(patient, "patientdeath")
        _r2_date_pair(death, "patientdeathdate", case.get("death_date_text"))
        _sub(death, "patientautopsyyesno", {"Yes": "1", "No": "2"}.get(case.get("autopsy_done"), "3"))
        if case.get("death_reported_causes"):
            cause = _sub(death, "patientdeathcause")
            _sub(cause, "patientdeathreport", case["death_reported_causes"][:250])

    for r in reactions:
        reaction = _sub(patient, "reaction")
        _sub(reaction, "primarysourcereaction", (r.get("reported_term") or "")[:200])
        cd = _reaction_coding(data, r)
        if cd:
            _sub(reaction, "reactionmeddraversionllt", cd.get("version"))
            _sub(reaction, "reactionmeddrallt", cd["code"])
            _sub(reaction, "reactionmeddraversionpt", cd.get("version"))
            _sub(reaction, "reactionmeddrapt", cd.get("pt_code"))
        _r2_date_pair(reaction, "reactionstartdate", r.get("start_text"))
        _r2_date_pair(reaction, "reactionenddate", r.get("end_text"))
        _sub(reaction, "reactionoutcome", OUTCOME_R2.get(r.get("outcome")))

    for t in record.get("tests") or []:
        test = _sub(patient, "test")
        _r2_date_pair(test, "testdate", t.get("test_date_text"))
        _sub(test, "testname", (t.get("test_name") or "")[:100])
        _sub(test, "testresult", (t.get("result_text") or "")[:50])
        _sub(test, "testunit", t.get("result_unit"))
        _sub(test, "lowtestrange", t.get("normal_low"))
        _sub(test, "hightestrange", t.get("normal_high"))
        _sub(test, "moreinformation", {"Yes": "1", "No": "2"}.get(t.get("more_info_available")))

    pairs = {}
    for c in record.get("causality") or []:
        if c.get("is_ai_draft") or c.get("source") == "AI-assisted draft" or not c.get("result"):
            continue
        pairs.setdefault(c.get("case_product_id"), []).append(c)
    reaction_terms = {r["reaction_id"]: r for r in reactions}
    for p in record.get("products") or []:
        drug = _sub(patient, "drug")
        _sub(drug, "drugcharacterization", ROLE.get(p.get("product_role") or "Suspect", "1"))
        _sub(drug, "medicinalproduct", (p.get("product_name") or "")[:70])
        _sub(drug, "drugbatchnumb", p.get("batch_number"))
        if p.get("dose_value"):
            _sub(drug, "drugstructuredosagenumb", _num(p["dose_value"]))
        _sub(drug, "drugdosagetext", " ".join(x for x in (p.get("dose"), p.get("frequency")) if x)[:100] or None)
        _sub(drug, "drugdosageform", (p.get("dosage_form") or "")[:50] or None)
        if p.get("route"):
            _sub(drug, "drugadministrationroute", _route_r2(p["route"]) or "050")
        _sub(drug, "drugindication", (p.get("indication") or "")[:250] or None)
        _r2_date_pair(drug, "drugstartdate", p.get("start_text"))
        _r2_date_pair(drug, "drugenddate", p.get("end_text"))
        _sub(drug, "actiondrug", ACTION_R2.get(p.get("action_taken")))
        for c in pairs.get(p.get("case_product_id"), []):
            relatedness = _sub(drug, "drugreactionrelatedness")
            reaction = reaction_terms.get(c.get("reaction_id")) or {}
            cd = _reaction_coding(data, reaction) if reaction else None
            if cd:
                _sub(relatedness, "drugreactionassesmeddraversion", cd.get("version"))
                _sub(relatedness, "drugreactionasses", cd["code"])
            _sub(relatedness, "drugassessmentsource", (c.get("source") or "")[:60])
            _sub(relatedness, "drugassessmentmethod", (c.get("method") or "")[:35] or None)
            _sub(relatedness, "drugresult", (c.get("result") or "")[:35])
        coded = _drug_coding(data, p)
        for name in [x.strip() for x in re.split(r"[;,/+]", (coded or {}).get("pt_name") or p.get("generic_name") or "") if x.strip()][:10]:
            _sub(_sub(drug, "activesubstance"), "activesubstancename", name[:100])

    summary = _sub(patient, "summary")
    _sub(summary, "narrativeincludeclinical", (case.get("case_narrative") or "")[:20000])
    _sub(summary, "reportercomment", (case.get("reporter_comments") or "")[:500] or None)
    _sub(summary, "senderdiagnosis", (case.get("company_diagnosis") or "")[:250] or None)
    _sub(summary, "sendercomment", (case.get("company_comments") or "")[:2000] or None)
    return ET.tostring(root, encoding="unicode")


R2_DOCTYPE = '<!DOCTYPE ichicsr SYSTEM "http://eudravigilance.ema.europa.eu/dtd/icsr21xml.dtd">'


# --------------------------------------------------------------------------
# Checking the file
# --------------------------------------------------------------------------

def _r3_required(root):
    """(label, present) for the elements every R3 ICSR must carry."""
    def q(tag):
        return f"{{{R3_NS}}}{tag}"

    def has_attr(elements, attr):
        return any(el.attrib.get(attr) for el in elements)

    event = root.find(f".//{q('investigationEvent')}")
    observations = list(root.iter(q("observation")))
    return [
        ("Batch number (N.1.2)", any(el.attrib.get("root") == OID["batch_id"] and el.attrib.get("extension")
                                     for el in root.findall(q("id")))),
        ("Receiver identifier (N.2.r.3)", has_attr(root.findall(f"{q('PORR_IN049016UV')}/{q('receiver')}/{q('device')}/{q('id')}"), "extension")),
        ("Sender identifier (N.2.r.2)", has_attr(root.findall(f"{q('PORR_IN049016UV')}/{q('sender')}/{q('device')}/{q('id')}"), "extension")),
        ("Sender's safety report identifier (C.1.1)", event is not None and any(
            el.attrib.get("root") == OID["message_id"] and el.attrib.get("extension") for el in event.findall(q("id")))),
        ("Date first received (C.1.4)", event is not None and has_attr(event.findall(f"{q('effectiveTime')}/{q('low')}"), "value")),
        ("Date of most recent information (C.1.5)", event is not None and has_attr(event.findall(q("availabilityTime")), "value")),
        ("Type of report (C.1.3)", has_attr(root.findall(f".//{q('investigationCharacteristic')}/{q('value')}"), "code")),
        ("At least one reaction (E.i)", any((o.find(q("code")) is not None and o.find(q("code")).attrib.get("code") == "29")
                                            for o in observations)),
        ("At least one drug (G.k)", root.find(f".//{q('substanceAdministration')}") is not None),
    ]


R2_REQUIRED = [
    ("ichicsrmessageheader/messagenumb", "Message number"),
    ("ichicsrmessageheader/messagesenderidentifier", "Sender identifier"),
    ("ichicsrmessageheader/messagereceiveridentifier", "Receiver identifier"),
    ("safetyreport/safetyreportid", "Safety report ID (A.1.0.1)"),
    ("safetyreport/primarysourcecountry", "Primary source country (A.1.1)"),
    ("safetyreport/reporttype", "Type of report (A.1.4)"),
    ("safetyreport/receivedate", "Date first received (A.1.6)"),
    ("safetyreport/receiptdate", "Date of most recent information (A.1.7)"),
    ("safetyreport/primarysource/qualification", "Reporter qualification (A.2.1.4)"),
    ("safetyreport/sender/senderorganization", "Sender organisation (A.3.1.2)"),
    ("safetyreport/patient/reaction/primarysourcereaction", "Reaction as reported (B.2.i.0)"),
    ("safetyreport/patient/drug/medicinalproduct", "Drug name (B.4.k.2.1)"),
]


def check_file(xml_text, version):
    """(problems, schema_used). Problems are messages; empty means it passed."""
    problems = []
    try:
        root = ET.fromstring(xml_text.split("?>", 1)[-1].replace(R2_DOCTYPE, ""))
    except ET.ParseError as error:
        return [f"The XML is not well formed: {error}"], None
    if version == "R3":
        for label, present in _r3_required(root):
            if not present:
                problems.append(f"Missing in the file: {label}.")
        for el in root.iter():
            for key in ("value",):
                v = el.attrib.get(key)
                if el.tag.endswith(("low", "high", "availabilityTime", "birthTime", "deceasedTime", "creationTime")) and v \
                        and not re.fullmatch(r"\d{4}(\d{2}(\d{2}(\d{2}(\d{2}(\d{2})?)?)?)?)?", v):
                    problems.append(f"Date “{v}” is not in the HL7 format CCYY[MM[DD[HHMM[SS]]]].")
    else:
        for path, label in R2_REQUIRED:
            el = root.find(path)
            if el is None or not (el.text or "").strip():
                problems.append(f"Missing in the file: {label}.")
        for el in root.iter():
            if el.tag.endswith("format") and el.text and el.text not in ("102", "203", "204", "602", "610", "802", "803", "804"):
                problems.append(f"Unknown date format {el.text} in {el.tag}.")
    schema_used = None
    path = schema_files().get(version)
    if path:
        try:
            from lxml import etree

            document = etree.fromstring(xml_text.encode("utf-8").split(b"?>", 1)[-1].replace(R2_DOCTYPE.encode(), b""))
            if version == "R3":
                schema = etree.XMLSchema(etree.parse(str(path)))
            else:
                schema = etree.DTD(str(path))
            if not schema.validate(document):
                for entry in list(schema.error_log)[:30]:
                    problems.append(f"Schema ({path.name}) line {entry.line}: {entry.message}")
            schema_used = path.name
        except Exception as error:  # schema could not be loaded
            problems.append(f"The installed schema {path.name} could not be used: {error}")
    return problems, schema_used


# --------------------------------------------------------------------------
# Making and keeping an export
# --------------------------------------------------------------------------

def preview(case_id, version, recipient_key):
    data = gather(case_id)
    data["settings"] = load_settings()
    cfg = recipient(data["settings"], recipient_key)
    return check(data, version, cfg), data, cfg


def create_export(case_id, version, recipient_key, user_id):
    """Write the file, check it and keep it as a new version. Returns the export row."""
    if version not in VERSIONS:
        raise ExportError("Choose E2B(R3) or E2B(R2).")
    issues, data, cfg = preview(case_id, version, recipient_key)
    if cfg is None:
        raise ExportError("Choose a recipient.")
    if version not in cfg.get("versions", VERSIONS):
        raise ExportError(f"{cfg['name']} does not accept E2B({version}); choose "
                          + " or ".join(f"E2B({v})" for v in cfg.get("versions", [])) + ".")
    settings = data["settings"]
    if not (settings.get("sender_identifier") or "").strip():
        raise ExportError("Set the sender identifier in Administration › E2B settings first.")
    case = data["record"]["case"]
    now = datetime.now(timezone.utc)
    with transaction() as cursor:
        cursor.execute("SELECT COALESCE(MAX(export_version), 0) + 1 AS v FROM pv.e2b_exports WHERE case_id = %s", (case_id,))
        export_version = cursor.fetchone()["v"]
        cursor.execute("SELECT nextval('pv.e2b_message_seq') AS n")
        message_number = cursor.fetchone()["n"]
    build = build_r3 if version == "R3" else build_r2
    body = build(data, cfg, message_number, now)
    if version == "R3":
        xml_text = '<?xml version="1.0" encoding="UTF-8"?>\n' + body
    else:
        xml_text = '<?xml version="1.0" encoding="UTF-8"?>\n' + R2_DOCTYPE + "\n" + body
    file_problems, schema_used = check_file(xml_text, version)
    errors = [i for i in issues if i["level"] == "error"]
    for message in file_problems:
        issues.append({"level": "error", "field": "file", "message": message})
    if errors or file_problems:
        status = "Draft"
    elif schema_used:
        status = "Validated"
    else:
        status = "Checked"
    meddra = sorted({cd.get("version") for (entity, _), cd in data["codings"].items()
                     if entity == "reaction" and cd.get("dictionary") == "MedDRA" and cd.get("version")})
    whodrug = sorted({cd.get("version") for (entity, _), cd in data["codings"].items()
                      if entity == "product" and cd.get("dictionary") == "WHODrug" and cd.get("version")})
    digest = hashlib.sha256(xml_text.encode("utf-8")).hexdigest()
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.e2b_exports (case_id, export_version, e2b_version, recipient_key, recipient_name, receiver_identifier,
                                        message_number, safety_report_id, status, issues, schema_used, meddra_version,
                                        whodrug_version, xml_text, file_sha256, case_updated_at, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s)
            RETURNING export_id
            """,
            (case_id, export_version, version, cfg["key"], cfg["name"], cfg.get("identifier"), str(message_number),
             _report_id(data), status, json.dumps(issues), schema_used, ", ".join(meddra) or None,
             ", ".join(whodrug) or None, xml_text, digest, case.get("updated_at"), user_id),
        )
        export_id = cursor.fetchone()["export_id"]
        cursor.execute(
            "INSERT INTO pv.case_audit_log (case_id, action, details, performed_by) VALUES (%s, %s, %s, %s)",
            (case_id, "E2B file generated",
             f"E2B({version}) export version {export_version} for {cfg['name']}: {status}"
             f" ({len([i for i in issues if i['level'] == 'error'])} error(s), "
             f"{len([i for i in issues if i['level'] == 'warning'])} warning(s)). SHA-256 {digest[:16]}…. Not transmitted.",
             user_id),
        )
    return get_export(export_id)


def exports(case_id):
    rows = query_all(
        """
        SELECT e.export_id, e.case_id, e.export_version, e.e2b_version, e.recipient_name, e.status, e.issues,
               e.schema_used, e.meddra_version, e.whodrug_version, e.file_sha256, e.created_at, e.message_number,
               u.full_name AS created_by_name
        FROM pv.e2b_exports AS e LEFT JOIN pv.users AS u ON u.user_id = e.created_by
        WHERE e.case_id = %s ORDER BY e.export_version DESC
        """,
        (case_id,),
    )
    out = []
    for row in rows:
        row = dict(row)
        if isinstance(row.get("issues"), str):
            row["issues"] = json.loads(row["issues"])
        out.append(row)
    return out


def get_export(export_id):
    row = query_one("SELECT * FROM pv.e2b_exports WHERE export_id = %s", (export_id,))
    if row:
        row = dict(row)
        if isinstance(row.get("issues"), str):
            row["issues"] = json.loads(row["issues"])
    return row


def file_name(export, case_number):
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", case_number)
    label = {"Draft": "DRAFT-not-for-submission", "Checked": "checked", "Validated": "schema-validated"}[export["status"]]
    return f"{safe}_E2B-{export['e2b_version']}_v{export['export_version']}_{label}.xml"
