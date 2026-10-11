"""The full ICSR record of a safety case (sections A–I).

The case row keeps its original columns, which the rest of the platform
reads (lists, reports, signal screening, listedness). The ICSR form adds the
detailed, repeatable sections in their own tables, and every save keeps the
original columns in step (``_sync_summary_columns``), so existing screens and
downloads keep working.

Values are stored exactly as entered. Dates may be partial (2026, 2026-09 or
2026-09-16) and any field that allows it may carry a null flavour explaining
why it is empty.
"""

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.db import query_all, query_one, transaction

# --------------------------------------------------------------------------
# Choices (wording follows ICH E2B(R3) where it applies)
# --------------------------------------------------------------------------

REPORT_TYPES = ["Spontaneous report", "Report from study", "Other", "Not available to sender (unknown)"]
CASE_CATEGORIES = ["Spontaneous", "Clinical trial", "Literature"]
QUALIFICATIONS = [
    "Physician", "Pharmacist", "Other health professional", "Lawyer",
    "Consumer or other non-health professional",
]
YES_NO_UNKNOWN = ["Yes", "No", "Unknown"]
SEXES = ["Female", "Male", "Unknown"]
AGE_UNITS = ["Years", "Months", "Weeks", "Days", "Hours", "Decades"]
AGE_GROUPS = ["Foetus", "Neonate", "Infant", "Child", "Adolescent", "Adult", "Elderly"]
GESTATION_UNITS = ["Weeks", "Months", "Trimester", "Days"]
WEIGHT_UNITS = ["kg", "g", "lb"]
HEIGHT_UNITS = ["cm", "m", "in"]
DRUG_ROLES = ["Suspect", "Concomitant", "Interacting", "Drug not administered"]
ACTIONS_TAKEN = [
    "Drug withdrawn", "Dose reduced", "Dose increased", "Dose not changed",
    "Unknown", "Not applicable",
]
OUTCOMES = [
    "Recovered/resolved", "Recovering/resolving", "Not recovered/not resolved",
    "Recovered/resolved with sequelae", "Fatal", "Unknown",
]
DURATION_UNITS = ["Minutes", "Hours", "Days", "Weeks", "Months", "Years"]
CHALLENGE_RESULTS = [
    "Positive (reaction abated / recurred)", "Negative (did not abate / did not recur)",
    "Not done", "Not applicable", "Unknown",
]
CAUSALITY_METHODS = ["WHO-UMC", "Naranjo", "Global introspection", "Company algorithm", "Other"]
CAUSALITY_SOURCES = [
    "Reporter", "Healthcare professional", "Investigator", "Literature author",
    "Company", "Regulatory authority", "AI-assisted draft",
]
CAUSALITY_RESULTS = [
    "Certain", "Probable / Likely", "Possible", "Unlikely",
    "Conditional / Unclassified", "Unassessable / Unclassifiable",
    "Related", "Not related",
]
SERIOUSNESS_CRITERIA = [
    ("crit_death", "Results in death"),
    ("crit_life_threatening", "Life-threatening"),
    ("crit_hospitalisation", "Caused or prolonged hospitalisation"),
    ("crit_disability", "Disabling / incapacitating"),
    ("crit_congenital_anomaly", "Congenital anomaly / birth defect"),
    ("crit_other_medically_important", "Other medically important condition"),
]

# ICH E2B null flavours, in plain words.
NULL_FLAVOURS = [
    ("UNK", "Unknown"),
    ("ASKU", "Asked but unknown"),
    ("NASK", "Not asked / not reported"),
    ("NA", "Not applicable"),
    ("MSK", "Withheld (masked)"),
    ("NI", "No information"),
]
NULL_FLAVOUR_LABELS = dict(NULL_FLAVOURS)

# --------------------------------------------------------------------------
# Partial dates
# --------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


class PartialDateError(ValueError):
    pass


def parse_partial_date(value):
    """'2026', '2026-09', '2026-09-16', '16/09/2026', '09/2026', 'Sep 2026',
    '16 Sep 2026' -> ISO partial text ('2026-09-16'), or '' for empty."""
    text = (value or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", text)
    if m:
        year, month, day = m.group(1), m.group(2), m.group(3)
    else:
        m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", text)
        if m:
            day, month, year = m.group(1), m.group(2), m.group(3)
        else:
            m = re.fullmatch(r"(\d{1,2})[/.-](\d{4})", text)
            if m:
                day, month, year = None, m.group(1), m.group(2)
            else:
                m = re.fullmatch(r"(?:(\d{1,2}) )?([A-Za-z]{3,9})\.? (\d{4})", text)
                if not m or m.group(2)[:3].lower() not in _MONTHS:
                    raise PartialDateError(
                        f"“{value}” is not a date. Use a full date, a month and year, or a year (e.g. 2026-09-16, 2026-09, 2026)."
                    )
                day, month, year = m.group(1), str(_MONTHS[m.group(2)[:3].lower()]), m.group(3)
    year_i = int(year)
    if not 1900 <= year_i <= date.today().year + 1:
        raise PartialDateError(f"The year in “{value}” looks wrong.")
    if month is None:
        return f"{year_i:04d}"
    month_i = int(month)
    if not 1 <= month_i <= 12:
        raise PartialDateError(f"The month in “{value}” looks wrong.")
    if day is None:
        return f"{year_i:04d}-{month_i:02d}"
    try:
        full = date(year_i, month_i, int(day))
    except ValueError:
        raise PartialDateError(f"“{value}” is not a real date.")
    return full.isoformat()


def full_date(partial):
    """The date for a complete partial text, else None."""
    if partial and len(partial) == 10:
        try:
            return date.fromisoformat(partial)
        except ValueError:
            return None
    return None


def show_partial(partial):
    """'2026-09-16' -> '16 Sep 2026'; '2026-09' -> 'Sep 2026'; '2026' -> '2026'."""
    if not partial:
        return ""
    parts = partial.split("-")
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{names[int(parts[1]) - 1]} {parts[0]}"
    return f"{int(parts[2]):02d} {names[int(parts[1]) - 1]} {parts[0]}"


def _text(value):
    if isinstance(value, bool):
        return "1" if value else ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(_iso(value) or "").strip()


def _iso(value):
    return value.isoformat() if isinstance(value, (date, datetime)) else (value or "")


# --------------------------------------------------------------------------
# Field definitions for the repeatable sections
# --------------------------------------------------------------------------

# kind: text | number | date (partial) | fulldate | choice | yesno | bool | country
REPORTER_FIELDS = {
    "qualification": "choice", "country_id": "country", "title": "text",
    "given_name": "text", "middle_name": "text", "family_name": "text",
    "organisation": "text", "email": "text", "phone": "text",
}
PRODUCT_FIELDS = {
    "product_role": "choice", "product_name": "text", "brand_name": "text",
    "generic_name": "text", "strength": "text", "dosage_form": "text",
    "indication": "text", "dose_value": "number", "dose_unit": "text",
    "dose": "text", "frequency": "text", "route": "text",
    "start_text": "date", "end_text": "date", "batch_number": "text",
    "expiry_date": "fulldate", "action_taken": "choice",
}
REACTION_FIELDS = {
    "reported_term": "text", "serious": "yesno",
    **{key: "bool" for key, _ in SERIOUSNESS_CRITERIA},
    "outcome": "choice", "medically_confirmed": "yesno",
    "start_text": "date", "end_text": "date",
    "duration_value": "number", "duration_unit": "choice", "country_id": "country",
}
HISTORY_FIELDS = {
    "condition_text": "text", "start_text": "date", "end_text": "date",
    "continuing": "yesno", "comments": "text", "family_history": "bool",
}
TEST_FIELDS = {
    "test_name": "text", "test_date_text": "date", "result_text": "text",
    "result_unit": "text", "normal_low": "text", "normal_high": "text",
    "comments": "text", "more_info_available": "yesno",
}
PAST_DRUG_FIELDS = {
    "drug_name": "text", "indication_text": "text", "reaction_text": "text",
    "start_text": "date", "end_text": "date",
}
CAUSALITY_FIELDS = {
    "product_ref": "ref", "reaction_ref": "ref", "method": "choice", "source": "choice",
    "result": "choice", "comments": "text", "assessor": "text", "assessed_on": "fulldate",
}
DRUG_REACTION_FIELDS = {
    "start_interval_value": "number", "start_interval_unit": "choice",
    "last_dose_interval_value": "number", "last_dose_interval_unit": "choice",
    "dechallenge": "choice", "rechallenge": "choice",
}

CASE_FIELDS = {
    # A. Administrative
    "icsr_report_type": "choice", "case_category": "choice",
    "received_date": "fulldate", "most_recent_info_date": "fulldate",
    "expedited_criteria": "yesno", "expedited_jurisdiction": "text", "expedited_basis": "text",
    "reporting_channel": "text", "reporting_partner": "text",
    "study_identifier": "text", "study_subject_id": "text", "study_blinded": "choice",
    "literature_reference": "text", "literature_authors": "text", "literature_journal": "text",
    "literature_year": "text", "literature_title": "text", "literature_doi": "text",
    "reporter_qualification": "choice", "reporter_country_id": "country",
    "reporter_title": "text", "reporter_given_name": "text", "reporter_middle_name": "text",
    "reporter_family_name": "text", "reporter_name": "text",
    "reporter_organisation": "text", "reporter_email": "text", "reporter_phone": "text",
    # B. Patient
    "patient_initials": "text", "patient_sex": "choice", "patient_dob_text": "date",
    "patient_age_value": "number", "patient_age_unit": "choice", "patient_age_group": "choice",
    "gestation_period": "number", "gestation_unit": "choice",
    "patient_weight_value": "number", "patient_weight_unit": "choice",
    "patient_height_value": "number", "patient_height_unit": "choice",
    "patient_lmp_text": "date", "patient_pregnancy_status": "choice", "patient_ethnicity": "text",
    "patient_allergies": "text", "social_history": "text",
    "patient_died": "yesno", "death_date_text": "date", "death_reported_causes": "text",
    "autopsy_done": "yesno", "autopsy_causes": "text",
    "parent_child_report": "yesno",
    # E. Medical history (unstructured)
    "medical_history_reported": "yesno", "medical_history": "text", "concomitant_medicines": "text",
    "treatment_given": "text",
    # I. Summary
    "case_narrative": "text", "reporter_comments": "text", "company_diagnosis": "text",
    "company_comments": "text", "linked_case_refs": "text",
}
PARENT_FIELDS = {
    "identification": "text", "sex": "choice", "dob_text": "date", "age_value": "number",
    "age_unit": "choice", "lmp_text": "date", "weight_value": "number", "weight_unit": "choice",
    "height_value": "number", "height_unit": "choice", "medical_history_text": "text",
    "past_drugs_text": "text",
}

LABELS = {
    "icsr_report_type": "Report type", "case_category": "Case category",
    "received_date": "Date first received", "most_recent_info_date": "Date of most recent information",
    "expedited_criteria": "Meets local expedited-reporting criteria",
    "expedited_jurisdiction": "Jurisdiction", "expedited_basis": "Basis for the reporting decision",
    "reporter_qualification": "Qualification", "reporter_country_id": "Country",
    "reporter_title": "Title", "reporter_given_name": "Given name", "reporter_middle_name": "Middle name",
    "reporter_family_name": "Family name", "reporter_name": "Reporter name (as recorded)",
    "reporter_organisation": "Organisation", "reporter_email": "Email", "reporter_phone": "Telephone",
    "patient_initials": "Name or initials", "patient_sex": "Sex", "patient_dob_text": "Date of birth",
    "patient_age_value": "Age at onset", "patient_age_unit": "Age unit", "patient_age_group": "Age group",
    "gestation_period": "Gestation period", "patient_weight_value": "Weight", "patient_height_value": "Height",
    "patient_lmp_text": "Last menstrual period", "patient_died": "Patient died",
    "death_date_text": "Date of death", "death_reported_causes": "Reported cause(s) of death",
    "parent_child_report": "Parent–child report", "medical_history_reported": "Relevant medical history reported",
    "medical_history": "Medical history (free text)", "case_narrative": "Case narrative",
    "reporter_comments": "Reporter's comments", "company_diagnosis": "Company's diagnosis",
    "company_comments": "Company's comments", "linked_case_refs": "Linked case references",
    "product_name": "Name as reported", "reported_term": "Reaction as reported",
    "start_text": "Start date", "end_text": "End date", "condition_text": "Condition",
    "test_name": "Test name", "drug_name": "Drug name", "result": "Result",
}


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _rows(sql, case_id):
    try:
        return query_all(sql, (case_id,))
    except Exception:
        from app.db import get_db

        get_db().rollback()
        return []


def load_icsr(case_id):
    case = query_one(
        """
        SELECT c.*, co.country_name
        FROM pv.safety_cases AS c
        LEFT JOIN pv.countries AS co ON co.country_id = c.country_id
        WHERE c.case_id = %s
        """,
        (case_id,),
    )
    if not case:
        return None
    return {
        "case": case,
        "reporters": _rows("SELECT * FROM pv.case_reporters WHERE case_id = %s ORDER BY sort_order, case_reporter_id", case_id),
        "parent": (_rows("SELECT * FROM pv.case_parents WHERE case_id = %s", case_id) or [None])[0],
        "products": _rows("SELECT * FROM pv.case_products WHERE case_id = %s ORDER BY sort_order, case_product_id", case_id),
        "reactions": _rows("SELECT * FROM pv.case_reactions WHERE case_id = %s ORDER BY sort_order, reaction_id", case_id),
        "drug_reactions": _rows("SELECT * FROM pv.case_drug_reactions WHERE case_id = %s", case_id),
        "history": _rows("SELECT * FROM pv.case_medical_history WHERE case_id = %s ORDER BY for_parent, sort_order, history_id", case_id),
        "tests": _rows("SELECT * FROM pv.case_tests WHERE case_id = %s ORDER BY sort_order, test_id", case_id),
        "past_drugs": _rows("SELECT * FROM pv.case_past_drugs WHERE case_id = %s ORDER BY for_parent, sort_order, past_drug_id", case_id),
        "causality": _rows("SELECT * FROM pv.case_causality WHERE case_id = %s ORDER BY sort_order, causality_id", case_id),
    }


def entry_heading(kind, row):
    """A meaningful heading for a repeated entry."""
    row = row or {}
    if kind == "product":
        name = (row.get("product_name") or row.get("brand_name") or row.get("generic_name") or "").strip()
        role = row.get("product_role") or ""
        extra = " ".join(x for x in (row.get("strength"), row.get("dosage_form")) if x)
        return f"{name}{' ' + extra if extra else ''}{' · ' + role if role else ''}" if name else "Drug not yet named"
    if kind == "reaction":
        term = (row.get("reported_term") or "").strip()
        return term[:90] + ("…" if len(term) > 90 else "") if term else "Reaction/event not yet described"
    if kind == "history":
        return (row.get("condition_text") or "").strip() or "Condition not yet described"
    if kind == "test":
        name = (row.get("test_name") or "").strip()
        when = show_partial(row.get("test_date_text"))
        return f"{name}{' · ' + when if when else ''}" if name else "Test not yet named"
    if kind == "past_drug":
        return (row.get("drug_name") or "").strip() or "Drug not yet named"
    if kind == "reporter":
        name = " ".join(x for x in (row.get("title"), row.get("given_name"), row.get("family_name")) if x)
        return name or row.get("organisation") or "Additional primary source"
    return "Entry"


def causality_heading(row, products, reactions):
    product = next((p for p in products if p.get("case_product_id") == row.get("case_product_id")), None)
    reaction = next((r for r in reactions if r.get("reaction_id") == row.get("reaction_id")), None)
    parts = []
    if product:
        parts.append(product.get("product_name") or "drug")
    if reaction:
        parts.append(reaction.get("reported_term") or "reaction")
    pair = " → ".join(parts)
    if row.get("result") or row.get("source"):
        who = row.get("source") or ""
        return f"{pair}: {row.get('result') or 'result not recorded'}{' (' + who + ')' if who else ''}" if pair else (row.get("result") or "Assessment")
    return pair or "Assessment not yet described"


# --------------------------------------------------------------------------
# Reading the submitted form
# --------------------------------------------------------------------------

ROW_PREFIXES = {
    "reporters": REPORTER_FIELDS, "products": PRODUCT_FIELDS, "reactions": REACTION_FIELDS,
    "history": HISTORY_FIELDS, "parent_history": HISTORY_FIELDS, "tests": TEST_FIELDS,
    "past_drugs": PAST_DRUG_FIELDS, "parent_past_drugs": PAST_DRUG_FIELDS, "causality": CAUSALITY_FIELDS,
}
ID_COLUMNS = {
    "reporters": "case_reporter_id", "products": "case_product_id", "reactions": "reaction_id",
    "history": "history_id", "parent_history": "history_id", "tests": "test_id",
    "past_drugs": "past_drug_id", "parent_past_drugs": "past_drug_id", "causality": "causality_id",
}


def _convert(kind, raw, errors, field_id, label):
    value = (raw or "").strip() if isinstance(raw, str) else raw
    if kind == "bool":
        return raw in ("on", "true", "1", "yes", True)
    if value in (None, ""):
        return None
    if kind == "number":
        try:
            number = Decimal(str(value).replace(",", "."))
        except InvalidOperation:
            errors[field_id] = f"{label}: enter a number."
            return None
        if number < 0:
            errors[field_id] = f"{label}: cannot be negative."
        return number
    if kind == "date":
        try:
            return parse_partial_date(value) or None
        except PartialDateError as error:
            errors[field_id] = f"{label}: {error}"
            return None
    if kind == "fulldate":
        try:
            parsed = parse_partial_date(value)
        except PartialDateError as error:
            errors[field_id] = f"{label}: {error}"
            return None
        result = full_date(parsed)
        if not result:
            errors[field_id] = f"{label}: enter the full date (day, month and year)."
        return result
    if kind in ("country", "ref"):
        try:
            return int(value) if kind == "country" else str(value)
        except ValueError:
            return None
    return value


def parse_icsr_form(form):
    """Turn the submitted form into {'case': {...}, 'products': [...], ...} and errors."""
    errors = {}
    data = {"case": {}, "case_nf": {}, "parent": {}, "parent_nf": {}, "drug_reactions": []}
    for name, kind in CASE_FIELDS.items():
        data["case"][name] = _convert(kind, form.get(f"case-{name}"), errors, f"case-{name}", LABELS.get(name, name))
        nf = (form.get(f"nf-case-{name}") or "").strip()
        if nf in NULL_FLAVOUR_LABELS:
            data["case_nf"][name] = nf
    for name, kind in PARENT_FIELDS.items():
        data["parent"][name] = _convert(kind, form.get(f"parent-{name}"), errors, f"parent-{name}", LABELS.get(name, name))
        nf = (form.get(f"nf-parent-{name}") or "").strip()
        if nf in NULL_FLAVOUR_LABELS:
            data["parent_nf"][name] = nf

    for prefix, fields in ROW_PREFIXES.items():
        keys = []
        for key in form.keys():
            m = re.fullmatch(rf"{prefix}-(\w+?)-__key", key)
            if m:
                keys.append(m.group(1))
        rows = []
        for order, key in enumerate(sorted(keys, key=lambda k: int(form.get(f"{prefix}-{k}-__order") or 0))):
            if form.get(f"{prefix}-{key}-__remove") == "1":
                continue
            row = {"__key": key, "__id": form.get(f"{prefix}-{key}-__id") or None, "sort_order": order, "null_flavours": {}}
            for name, kind in fields.items():
                field_id = f"{prefix}-{key}-{name}"
                row[name] = _convert(kind, form.get(field_id), errors, field_id, LABELS.get(name, name.replace("_", " ").capitalize()))
                nf = (form.get(f"nf-{field_id}") or "").strip()
                if nf in NULL_FLAVOUR_LABELS:
                    row["null_flavours"][name] = nf
            if prefix == "products":
                raw_id = (form.get(f"{prefix}-{key}-catalogue_product_id") or "").strip()
                row["catalogue_product_id"] = int(raw_id) if raw_id.isdigit() else None
            if _row_is_empty(prefix, row):
                continue
            rows.append(row)
        data[prefix] = rows

    for key in form.keys():
        m = re.fullmatch(r"dr-(\w+)--(\w+)-__pair", key)
        if not m:
            continue
        product_key, reaction_key = m.group(1), m.group(2)
        row = {"product_ref": product_key, "reaction_ref": reaction_key}
        for name, kind in DRUG_REACTION_FIELDS.items():
            field_id = f"dr-{product_key}--{reaction_key}-{name}"
            row[name] = _convert(kind, form.get(field_id), errors, field_id, name.replace("_", " ").capitalize())
        if any(row[name] not in (None, "") for name in DRUG_REACTION_FIELDS):
            data["drug_reactions"].append(row)
    return data, errors


def _row_is_empty(prefix, row):
    meaningful = [k for k in ROW_PREFIXES[prefix] if k not in ("product_role", "sort_order")]
    return not any(row.get(k) not in (None, "", False) for k in meaningful) and not row["null_flavours"] and not row["__id"]


def check_icsr(data):
    """Rules that stop a save (beyond field formats)."""
    errors = {}
    for row in data.get("products", []):
        if not row.get("product_name"):
            errors[f"products-{row['__key']}-product_name"] = "Each drug needs the name as reported (or remove the drug)."
        if row.get("start_text") and row.get("end_text") and row["end_text"] < row["start_text"]:
            errors[f"products-{row['__key']}-end_text"] = "The stop date is before the start date."
    for row in data.get("reactions", []):
        if not row.get("reported_term"):
            errors[f"reactions-{row['__key']}-reported_term"] = "Each reaction needs the wording as reported (or remove it)."
        if row.get("start_text") and row.get("end_text") and row["end_text"] < row["start_text"]:
            errors[f"reactions-{row['__key']}-end_text"] = "The end date is before the start date."
        if row.get("serious") == "Yes" and not any(row.get(k) for k, _ in SERIOUSNESS_CRITERIA):
            errors[f"reactions-{row['__key']}-serious"] = "Tick at least one seriousness criterion for a serious reaction."
    for prefix in ("history", "parent_history"):
        for row in data.get(prefix, []):
            if not row.get("condition_text"):
                errors[f"{prefix}-{row['__key']}-condition_text"] = "Describe the condition (or remove the entry)."
    for row in data.get("tests", []):
        if not row.get("test_name"):
            errors[f"tests-{row['__key']}-test_name"] = "Name the test (or remove the entry)."
    for prefix in ("past_drugs", "parent_past_drugs"):
        for row in data.get(prefix, []):
            if not row.get("drug_name"):
                errors[f"{prefix}-{row['__key']}-drug_name"] = "Name the drug (or remove the entry)."
    for row in data.get("causality", []):
        if not row.get("product_ref") or not row.get("reaction_ref"):
            errors[f"causality-{row['__key']}-product_ref"] = "Choose the drug and the reaction this assessment is about."
        if row.get("source") == "AI-assisted draft":
            row["is_ai_draft"] = True
    case = data.get("case", {})
    if not case.get("received_date"):
        errors["case-received_date"] = "The date first received (Day 0) is required."
    if case.get("most_recent_info_date") and case.get("received_date") and case["most_recent_info_date"] < case["received_date"]:
        errors["case-most_recent_info_date"] = "The most recent information cannot be before the date first received."
    if case.get("reporter_email"):
        from app.services.approval_settings import valid_email

        if not valid_email(case["reporter_email"]):
            errors["case-reporter_email"] = "The reporter email does not look like an email address."
    return errors


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------

def _clean(value):
    if isinstance(value, Decimal):
        return value
    return value


def _upsert_rows(cursor, table, id_col, case_id, rows, columns, existing_ids, extra=None):
    """Update rows that have ids, insert new ones, delete removed ones.
    Returns {form key: database id}."""
    keep = set()
    keymap = {}
    for row in rows:
        values = {c: row.get(c) for c in columns}
        values["null_flavours"] = json.dumps(row.get("null_flavours") or {})
        values["sort_order"] = row.get("sort_order", 0)
        if extra:
            values.update(extra)
        row_id = row.get("__id")
        if row_id and int(row_id) in existing_ids:
            assignments = ", ".join(f"{c} = %s" for c in values)
            cursor.execute(
                f"UPDATE pv.{table} SET {assignments} WHERE {id_col} = %s AND case_id = %s",
                list(values.values()) + [int(row_id), case_id],
            )
            keep.add(int(row_id))
            keymap[row["__key"]] = int(row_id)
        else:
            cols = ["case_id"] + list(values)
            cursor.execute(
                f"INSERT INTO pv.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING {id_col}",
                [case_id] + list(values.values()),
            )
            new_id = cursor.fetchone()[id_col]
            keep.add(new_id)
            keymap[row["__key"]] = new_id
    removed = [i for i in existing_ids if i not in keep]
    if removed:
        cursor.execute(f"DELETE FROM pv.{table} WHERE {id_col} = ANY(%s) AND case_id = %s", (removed, case_id))
    return keymap, removed


def _existing_ids(cursor, table, id_col, case_id, where=""):
    cursor.execute(f"SELECT {id_col} FROM pv.{table} WHERE case_id = %s {where}", (case_id,))
    return {r[id_col] for r in cursor.fetchall()}


def _describe_changes(before, data):
    """Short plain-language list of what changed, for the case audit log."""
    changes = []
    case_before = before["case"]
    for name, value in data["case"].items():
        old = case_before.get(name)
        if isinstance(old, Decimal) and isinstance(value, Decimal):
            same = old == value
        else:
            same = (_iso(old) or "") == (_iso(value) or "") or (old in (None, "") and value in (None, ""))
        if not same:
            label = LABELS.get(name, name.replace("_", " "))
            changes.append(f"{label}: {_iso(old) or 'blank'} → {_iso(value) or 'blank'}")
    id_cols = {"products": "case_product_id", "reactions": "reaction_id", "tests": "test_id",
               "causality": "causality_id", "reporters": "case_reporter_id"}
    sections = (("products", "drug"), ("reactions", "reaction"), ("history", "medical history entry"),
                ("parent_history", "parent medical history entry"), ("tests", "test"),
                ("past_drugs", "past drug"), ("parent_past_drugs", "parent past drug"),
                ("causality", "causality assessment"), ("reporters", "additional primary source"))
    for prefix, label in sections:
        rows = data.get(prefix) or []
        added = sum(1 for r in rows if not r.get("__id"))
        kept = {str(r.get("__id")) for r in rows if r.get("__id")}
        if prefix in ("history", "parent_history"):
            old_rows = [r for r in before.get("history") or [] if bool(r.get("for_parent")) == (prefix == "parent_history")]
            old_ids = {str(r["history_id"]) for r in old_rows}
        elif prefix in ("past_drugs", "parent_past_drugs"):
            old_rows = [r for r in before.get("past_drugs") or [] if bool(r.get("for_parent")) == (prefix == "parent_past_drugs")]
            old_ids = {str(r["past_drug_id"]) for r in old_rows}
        else:
            old_ids = {str(r[id_cols[prefix]]) for r in before.get(prefix) or []}
        removed = len(old_ids - kept)
        if added:
            changes.append(f"{added} {label}(s) added")
        if removed:
            changes.append(f"{removed} {label}(s) removed")
        old_by_id = {}
        id_col = {"history": "history_id", "parent_history": "history_id", "past_drugs": "past_drug_id",
                  "parent_past_drugs": "past_drug_id"}.get(prefix) or id_cols[prefix]
        source = before.get({"parent_history": "history", "parent_past_drugs": "past_drugs"}.get(prefix, prefix)) or []
        for old in source:
            old_by_id[str(old[id_col])] = old
        fields = [f for f in ROW_PREFIXES[prefix] if not f.endswith("_ref")]
        changed = 0
        for row in rows:
            old = old_by_id.get(str(row.get("__id")))
            if not old:
                continue
            if any(_text(old.get(f)) != _text(row.get(f)) for f in fields):
                changed += 1
        if changed:
            changes.append(f"{changed} {label}(s) changed")
    return changes


def save_icsr(case_id, data, user_id):
    """Save the whole ICSR form in one transaction. Returns a list of changes."""
    before = load_icsr(case_id)
    if not before:
        raise ValueError("Case not found.")
    changes = _describe_changes(before, data)

    with transaction() as cursor:
        case_values = dict(data["case"])
        case_values["null_flavours"] = json.dumps(data.get("case_nf") or {})
        assignments = ", ".join(f"{c} = %s" for c in case_values)
        cursor.execute(
            f"UPDATE pv.safety_cases SET {assignments}, updated_at = NOW() WHERE case_id = %s",
            list(case_values.values()) + [case_id],
        )

        if data["case"].get("parent_child_report") == "Yes" or any(v not in (None, "") for v in data["parent"].values()):
            values = dict(data["parent"])
            values["null_flavours"] = json.dumps(data.get("parent_nf") or {})
            cols = list(values)
            cursor.execute(
                f"INSERT INTO pv.case_parents (case_id, {', '.join(cols)}) VALUES (%s, {', '.join(['%s'] * len(cols))}) "
                f"ON CONFLICT (case_id) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in cols)}",
                [case_id] + list(values.values()),
            )

        _upsert_rows(cursor, "case_reporters", "case_reporter_id", case_id, data.get("reporters", []),
                     list(REPORTER_FIELDS), _existing_ids(cursor, "case_reporters", "case_reporter_id", case_id))
        product_keys, _ = _upsert_rows(
            cursor, "case_products", "case_product_id", case_id, data.get("products", []),
            list(PRODUCT_FIELDS), _existing_ids(cursor, "case_products", "case_product_id", case_id),
        )
        from app.services.product_catalogue import attach_to_record

        for row in data.get("products", []):
            product_row_id = product_keys.get(row["__key"])
            if product_row_id:
                attach_to_record(cursor, "case_products", "case_product_id", product_row_id, row.get("catalogue_product_id"))
        reaction_keys, _ = _upsert_rows(
            cursor, "case_reactions", "reaction_id", case_id, data.get("reactions", []),
            list(REACTION_FIELDS), _existing_ids(cursor, "case_reactions", "reaction_id", case_id),
        )
        _upsert_rows(cursor, "case_medical_history", "history_id", case_id, data.get("history", []),
                     list(HISTORY_FIELDS), _existing_ids(cursor, "case_medical_history", "history_id", case_id, "AND for_parent = FALSE"),
                     extra={"for_parent": False})
        _upsert_rows(cursor, "case_medical_history", "history_id", case_id, data.get("parent_history", []),
                     list(HISTORY_FIELDS), _existing_ids(cursor, "case_medical_history", "history_id", case_id, "AND for_parent = TRUE"),
                     extra={"for_parent": True})
        _upsert_rows(cursor, "case_tests", "test_id", case_id, data.get("tests", []),
                     list(TEST_FIELDS), _existing_ids(cursor, "case_tests", "test_id", case_id))
        _upsert_rows(cursor, "case_past_drugs", "past_drug_id", case_id, data.get("past_drugs", []),
                     list(PAST_DRUG_FIELDS), _existing_ids(cursor, "case_past_drugs", "past_drug_id", case_id, "AND for_parent = FALSE"),
                     extra={"for_parent": False})
        _upsert_rows(cursor, "case_past_drugs", "past_drug_id", case_id, data.get("parent_past_drugs", []),
                     list(PAST_DRUG_FIELDS), _existing_ids(cursor, "case_past_drugs", "past_drug_id", case_id, "AND for_parent = TRUE"),
                     extra={"for_parent": True})

        # Causality rows refer to drugs and reactions by their form keys.
        causality_rows = []
        for row in data.get("causality", []):
            row = dict(row)
            row["case_product_id"] = product_keys.get(row.get("product_ref"))
            row["reaction_id"] = reaction_keys.get(row.get("reaction_ref"))
            row["is_ai_draft"] = row.get("source") == "AI-assisted draft"
            causality_rows.append(row)
        _upsert_rows(
            cursor, "case_causality", "causality_id", case_id, causality_rows,
            ["case_product_id", "reaction_id", "method", "source", "result", "comments", "assessor", "assessed_on", "is_ai_draft"],
            _existing_ids(cursor, "case_causality", "causality_id", case_id),
            extra=None,
        )

        cursor.execute("DELETE FROM pv.case_drug_reactions WHERE case_id = %s", (case_id,))
        for row in data.get("drug_reactions", []):
            product_id = product_keys.get(row["product_ref"])
            reaction_id = reaction_keys.get(row["reaction_ref"])
            if not product_id or not reaction_id:
                continue
            cols = list(DRUG_REACTION_FIELDS)
            cursor.execute(
                f"INSERT INTO pv.case_drug_reactions (case_id, case_product_id, reaction_id, {', '.join(cols)}) "
                f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(cols))})",
                [case_id, product_id, reaction_id] + [row.get(c) for c in cols],
            )

        _sync_summary_columns(cursor, case_id)

        if not before["case"].get("icsr_started_at"):
            cursor.execute("UPDATE pv.safety_cases SET icsr_started_at = NOW() WHERE case_id = %s AND icsr_started_at IS NULL", (case_id,))

        cursor.execute(
            "INSERT INTO pv.case_audit_log (case_id, action, details, performed_by) VALUES (%s, %s, %s, %s)",
            (case_id, "ICSR form saved", "; ".join(changes)[:4000] or "Saved with no changes.", user_id),
        )
    return changes


CRITERIA_TEXT = dict(SERIOUSNESS_CRITERIA)


def _sync_summary_columns(cursor, case_id):
    """Keep the original one-line columns in step with the detailed sections,
    so lists, reports, signal screening and listedness keep working."""
    cursor.execute("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    case = cursor.fetchone()
    cursor.execute("SELECT * FROM pv.case_reactions WHERE case_id = %s ORDER BY sort_order, reaction_id", (case_id,))
    reactions = cursor.fetchall()
    cursor.execute("SELECT * FROM pv.case_products WHERE case_id = %s ORDER BY sort_order, case_product_id", (case_id,))
    products = cursor.fetchall()
    cursor.execute(
        "SELECT * FROM pv.case_causality WHERE case_id = %s ORDER BY (source = 'Company') DESC, sort_order, causality_id",
        (case_id,),
    )
    causality = cursor.fetchall()

    updates = {}
    if reactions:
        terms = [r["reported_term"] for r in reactions if r.get("reported_term")]
        updates["event_description"] = "; ".join(terms) if len(terms) > 1 else (terms[0] if terms else case["event_description"])
        first = reactions[0]
        updates["event_onset_date"] = full_date(first.get("start_text"))
        updates["event_end_date"] = full_date(first.get("end_text"))
        updates["event_outcome"] = _worst_outcome([r.get("outcome") for r in reactions]) or case.get("event_outcome")
        serious = [r for r in reactions if r.get("serious") == "Yes"]
        updates["seriousness"] = bool(serious)
        if serious:
            labels = []
            for r in serious:
                for key, label in SERIOUSNESS_CRITERIA:
                    if r.get(key) and label not in labels:
                        labels.append(label)
            updates["seriousness_criteria"] = "; ".join(labels) or case.get("seriousness_criteria")
    names = [case.get(k) for k in ("reporter_title", "reporter_given_name", "reporter_middle_name", "reporter_family_name")]
    if any(names[1:]) and not (case.get("reporter_name") or "").strip():
        # The name as originally recorded is kept; it is only filled in when empty.
        updates["reporter_name"] = " ".join(n for n in names if n)
    if case.get("reporter_qualification") and not case.get("reporter_profession"):
        updates["reporter_profession"] = case["reporter_qualification"]
    age, unit = case.get("patient_age_value"), case.get("patient_age_unit") or "Years"
    if age is not None and unit == "Years":
        updates["patient_age_years"] = int(age)
    weight, wunit = case.get("patient_weight_value"), case.get("patient_weight_unit") or "kg"
    if weight is not None:
        factor = {"kg": Decimal("1"), "g": Decimal("0.001"), "lb": Decimal("0.45359237")}.get(wunit, Decimal("1"))
        updates["patient_weight_kg"] = (Decimal(weight) * factor).quantize(Decimal("0.01"))
    # The ICSR date of birth (possibly partial) is authoritative; the old
    # full-date column only holds it when it is complete.
    updates["patient_date_of_birth"] = full_date(case.get("patient_dob_text"))
    if causality:
        updates["causality_assessment"] = causality[0].get("result") or case.get("causality_assessment")
    for product in products:
        cursor.execute(
            "UPDATE pv.case_products SET therapy_start_date = %s, therapy_end_date = %s WHERE case_product_id = %s",
            (full_date(product.get("start_text")), full_date(product.get("end_text")), product["case_product_id"]),
        )
    if updates:
        assignments = ", ".join(f"{c} = %s" for c in updates)
        cursor.execute(f"UPDATE pv.safety_cases SET {assignments} WHERE case_id = %s", list(updates.values()) + [case_id])


_OUTCOME_ORDER = ["Fatal", "Not recovered/not resolved", "Recovered/resolved with sequelae",
                  "Recovering/resolving", "Recovered/resolved", "Unknown"]


def _worst_outcome(outcomes):
    present = [o for o in outcomes if o]
    for outcome in _OUTCOME_ORDER:
        if outcome in present:
            return outcome
    return present[0] if present else None


def sync_reactions_from_intake(case_id):
    """After the short intake/edit form is saved: create the first reaction, or
    keep a single reaction in step with the intake fields."""
    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    if not case:
        return
    try:
        reactions = query_all("SELECT * FROM pv.case_reactions WHERE case_id = %s", (case_id,))
    except Exception:
        from app.db import get_db

        get_db().rollback()
        return
    values = {
        "reported_term": case.get("event_description"),
        "serious": "Yes" if case.get("seriousness") else "No",
        "outcome": case.get("event_outcome"),
        "start_text": _iso(case.get("event_onset_date")) or None,
        "end_text": _iso(case.get("event_end_date")) or None,
    }
    if not (values["reported_term"] or "").strip():
        return
    with transaction() as cursor:
        if not reactions:
            cursor.execute(
                "INSERT INTO pv.case_reactions (case_id, reported_term, serious, outcome, start_text, end_text, country_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (case_id, values["reported_term"], values["serious"], values["outcome"],
                 values["start_text"], values["end_text"], case.get("country_id")),
            )
        elif len(reactions) == 1:
            cursor.execute(
                "UPDATE pv.case_reactions SET reported_term = %s, serious = %s, outcome = %s, "
                "start_text = COALESCE(%s, start_text), end_text = COALESCE(%s, end_text) WHERE reaction_id = %s",
                (values["reported_term"], values["serious"], values["outcome"],
                 values["start_text"], values["end_text"], reactions[0]["reaction_id"]),
            )


def form_value(value):
    """How a stored value is shown in an input box."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else ""
    if isinstance(value, Decimal):
        text = format(value.normalize(), "f")
        return text
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)


def row_key(prefix, row):
    """Stable form key for a repeated entry: p12 for saved drug 12, etc."""
    if row.get("__key"):
        return row["__key"]
    letters = {"reporters": ("s", "case_reporter_id"), "products": ("p", "case_product_id"),
               "reactions": ("r", "reaction_id"), "history": ("h", "history_id"),
               "parent_history": ("h", "history_id"), "tests": ("t", "test_id"),
               "past_drugs": ("d", "past_drug_id"), "parent_past_drugs": ("d", "past_drug_id"),
               "causality": ("k", "causality_id")}
    letter, id_col = letters[prefix]
    return f"{letter}{row.get(id_col)}" if row.get(id_col) else f"{letter}x"


def row_id(prefix, row):
    return row.get(ID_COLUMNS[prefix]) or ""


# Completeness check -> the field it is about (clickable checklist).
CHECK_FIELDS = {
    "minimum_patient": "case-patient_initials",
    "identifiable_reporter": "case-reporter_family_name",
    "suspected_product": "first:products-product_name",
    "reported_event": "first:reactions-reported_term",
    "event_onset_date": "first:reactions-start_text",
    "event_outcome": "first:reactions-outcome",
    "seriousness_basis": "first:reactions-serious",
}


# --------------------------------------------------------------------------
# Field-level checklist (clickable completeness)
# --------------------------------------------------------------------------

SKIP_CHECKS = ("event_coding", "follow_up_due_date")


def _nf(row):
    value = (row or {}).get("null_flavours") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value


def _given(row, name):
    """A value is recorded, or a reason it is missing (null flavour) is given."""
    value = (row or {}).get(name)
    return value not in (None, "", []) or bool(_nf(row).get(name))


def field_checks(record, completeness=None):
    """Items still to complete, each pointing at the exact field.

    ``required``: the completeness checks the case cannot pass without;
    ``recommended``: details a complete ICSR normally carries (each can also
    be closed by recording why the information is missing).
    Each item: {target, label, why, section, level}.
    """
    out = []
    for check in completeness or []:
        code = check.get("code") or check.get("check_code")
        if check.get("status") == "Pass" or code in SKIP_CHECKS:
            continue
        out.append({"target": code, "label": check.get("label") or check.get("check_label"),
                    "why": check.get("message"), "section": "Completeness", "level": "required"})

    case = record.get("case") or {}

    def rec(target, label, why, section):
        out.append({"target": target, "label": label, "why": why, "section": section, "level": "recommended"})

    if not _given(case, "icsr_report_type"):
        rec("case-icsr_report_type", "Type of report", "E2B needs the report type (spontaneous, study, other).", "A. Administrative")
    if not _given(case, "reporter_qualification") and not any(r.get("qualification") for r in record.get("reporters") or []):
        rec("case-reporter_qualification", "Reporter qualification",
            "Needed to tell healthcare-professional reports from consumer reports (E2B C.2.r.4).", "A. Administrative")
    if not _given(case, "patient_sex"):
        rec("case-patient_sex", "Patient sex", "Record the sex, or why it is unknown.", "B. Patient")
    if not (_given(case, "patient_dob_text") or _given(case, "patient_age_value") or _given(case, "patient_age_group")):
        rec("case-patient_age_value", "Patient age", "Give the date of birth, the age at onset or at least an age group.", "B. Patient")
    if case.get("patient_died") == "Yes" and not _given(case, "death_date_text"):
        rec("case-death_date_text", "Date of death", "The patient died; record the date (partial is fine) or why it is unknown.", "B. Patient")

    for row in record.get("products") or []:
        key = row_key("products", row)
        name = (row.get("product_name") or "drug").strip()[:40]
        if (row.get("product_role") or "Suspect") not in ("Suspect", "Interacting"):
            continue
        for field, label, why in (
            ("indication", "Indication", "Why the drug was given."),
            ("route", "Route", "How it was given."),
            ("start_text", "Treatment start", "Needed for the time to onset."),
            ("action_taken", "Action taken", "What was done with the drug after the reaction (dechallenge)."),
        ):
            if not _given(row, field):
                rec(f"products-{key}-{field}", f"{label} for {name}", why, "C. Drugs")

    for row in record.get("reactions") or []:
        key = row_key("reactions", row)
        term = (row.get("reported_term") or "reaction").strip()[:40]
        if not _given(row, "start_text"):
            rec(f"reactions-{key}-start_text", f"Onset date for “{term}”", "Needed for the time to onset and reporting clock.", "D. Reactions")
        if not _given(row, "serious"):
            rec(f"reactions-{key}-serious", f"Seriousness of “{term}”", "Say whether this reaction is serious.", "D. Reactions")
        if not _given(row, "outcome"):
            out.append({"target": f"reactions-{key}-outcome", "label": f"Outcome of “{term}”",
                        "why": "Every reaction needs an outcome (or ‘Unknown’).", "section": "D. Reactions", "level": "required"})
    return out
