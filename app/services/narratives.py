"""Controlled ICSR narratives.

A narrative is built only from the structured case record, using the
approved template for the case type (spontaneous, clinical trial or
literature). The template is a list of sentences per section:

* ``{token}``      – a fact from the case; a sentence is left out when a
                     token outside ``[[ ]]`` has no value,
* ``[[ ... ]]``    – an optional part, left out when any token inside it
                     has no value,
* ``A || B``       – alternatives: the first one whose facts are all
                     available is used.

Nothing is invented: missing facts leave sentences out, "not reported" is
never turned into "absent" or "normal", partial dates stay partial,
intervals are calculated only from complete dates, and contradictions are
listed for the reviewer. Each sentence records the structured fields it
used. Values that look like placeholders or sample data are left out and
reported. AI causality drafts are excluded until a person records an
assessment.
"""

import json
import re
from datetime import date

from app.db import query_all, query_one, transaction
from app.services import icsr

CASE_TYPES = ("Spontaneous", "Clinical trial", "Literature")
EDIT_ROLES = ("System Administrator", "QPPV", "Deputy QPPV", "Group Head RA & Quality")
REVIEW_ROLES = ("System Administrator", "QPPV", "Deputy QPPV", "Group Head RA & Quality", "Medical Reviewer", "Quality Reviewer")

# Section order = narrative paragraph order.
SECTIONS = [
    ("intro", "1. Introduction", "Once per case."),
    ("patient", "2. Patient", "Once per case."),
    ("drug", "3. Suspect drug exposure", "Repeated for each suspect or interacting drug."),
    ("event", "4. Events and chronology – each reaction", "Repeated for each reaction, in date order."),
    ("test", "4. Events and chronology – each test result", "Repeated for each test, in date order."),
    ("treatment", "4. Events and chronology – treatment", "Once per case."),
    ("closing_drug", "5. Closing – each suspect drug", "Action taken and stop date."),
    ("closing_pair", "5. Closing – dechallenge/rechallenge", "Repeated for each drug–reaction pair with a result."),
    ("closing_event", "5. Closing – each reaction", "Outcome and end date."),
    ("death", "5. Closing – death", "Only when the patient died."),
    ("causality_both", "6. Causality – source and company", "A drug–reaction pair with both assessments."),
    ("causality_source", "6. Causality – source only", "A pair with only a source (reporter, investigator, author…) assessment."),
    ("causality_company", "6. Causality – company only", "A pair with only the company assessment."),
    ("references", "7. Associated case references", "Once per case."),
]
SECTION_KEYS = [key for key, _, _ in SECTIONS]
PARAGRAPHS = [
    ("intro", ["intro"]),
    ("patient", ["patient"]),
    ("drug", ["drug"]),
    ("events", ["event", "test", "treatment"]),
    ("closing", ["closing_drug", "closing_pair", "closing_event", "death"]),
    ("causality", ["causality_both", "causality_source", "causality_company"]),
    ("references", ["references"]),
]

TOKENS = {
    "company": "Company name (Company profile)",
    "day0": "Date first received (Day 0)",
    "reporter_type": "Reporter qualification / source",
    "channel": "Reporting channel",
    "partner": "Received through partner",
    "study_id": "Study identifier",
    "subject_id": "Subject / randomisation identifier",
    "blinding": "Blinding statement (when the study is blinded)",
    "lit_authors": "Article authors", "lit_journal": "Journal", "lit_year": "Publication year",
    "lit_title": "Article title", "lit_doi": "DOI / PMID", "lit_reference": "Literature reference (free text)",
    "age": "Age at onset (e.g. 34-year-old)", "age_group": "Age group", "dob": "Date of birth", "sex": "Sex",
    "country": "Country", "ethnicity": "Ethnicity", "weight": "Weight", "height": "Height",
    "pregnancy": "Pregnancy status (when pregnant)",
    "history": "Medical history", "history_stated_none": "Reporter stated no relevant history",
    "family_history": "Family history", "allergies": "Allergies", "social_history": "Social history",
    "past_drugs": "Past drug history", "concomitants": "Concomitant medications",
    "drug": "Drug (brand/generic)", "strength": "Strength", "form": "Pharmaceutical form",
    "dose": "Dose and unit", "frequency": "Frequency", "route": "Route", "indication": "Indication",
    "start": "Start date", "stop": "Stop date", "batch": "Batch number", "expiry": "Expiry date",
    "action": "Action taken",
    "term": "Reaction as reported", "onset": "Onset date", "tto": "Time to onset (from complete dates)",
    "criteria": "Seriousness criteria", "nonserious": "Reported as non-serious", "outcome": "Outcome",
    "end": "End date",
    "test": "Test name", "test_date": "Test date", "result": "Result", "unit": "Unit",
    "low": "Normal low", "high": "Normal high",
    "treatment": "Treatment given",
    "dechallenge": "Dechallenge result", "rechallenge": "Rechallenge result",
    "death_date": "Date of death", "death_causes": "Reported cause(s) of death", "autopsy": "Autopsy causes",
    "source": "Who assessed (reporter, investigator…)", "source_result": "Source assessment",
    "method": "Assessment method", "company_result": "Company assessment",
    "linked_refs": "Linked case references", "parent_child": "Parent–child report statement",
}

_PATIENT = [
    "The patient was a[[ {age}]][[ {age_group}]][[ {sex}]] patient[[ from {country}]][[ ({ethnicity})]].",
    "The patient's date of birth was {dob}.",
    "The patient's weight was {weight}[[ and height {height}]].",
    "The patient was {pregnancy}.",
    "Relevant medical history included {history}. || The reporter stated that the patient had {history_stated_none} relevant medical history.",
    "Family history included {family_history}.",
    "Known allergies: {allergies}.",
    "Social history: {social_history}.",
    "Past drug history included {past_drugs}.",
    "Concomitant medications included {concomitants}.",
]
_DRUG = [
    "The patient received {drug}[[ {strength}]][[ {form}]][[ at a dose of {dose}]][[ {frequency}]][[ by the {route} route]][[ for {indication}]][[, starting on {start}]].",
    "The batch number was {batch}[[ and the expiry date {expiry}]].",
]
_EVENTS = {
    "event": [
        "On {onset}[[ ({tto})]], the patient experienced {term}. || The patient experienced {term} (onset date not reported).",
        "The event was considered serious ({criteria}). || The event was reported as {nonserious}.",
    ],
    "test": [
        "On {test_date}, {test} was {result}[[ {unit}]][[ (reference range {low} to {high})]]. || {test}: {result}[[ {unit}]][[ (reference range {low} to {high})]] (date not reported).",
    ],
    "treatment": ["Treatment for the event(s) included {treatment}."],
}
_CLOSING = {
    "closing_drug": ["The action taken with {drug} was: {action}[[; it was stopped on {stop}]]."],
    "closing_pair": [
        "Dechallenge of {drug} for {term}: {dechallenge}.",
        "Rechallenge with {drug} for {term}: {rechallenge}.",
    ],
    "closing_event": [
        "At the time of the report, the outcome of {term} was {outcome}[[ (end date {end})]]. || The outcome of {term} was not reported.",
    ],
    "death": ["The patient died[[ on {death_date}]][[; the reported cause of death was {death_causes}]][[; autopsy findings: {autopsy}]]."],
}
_CAUSALITY = {
    "causality_both": ["The {source} considered the relationship between {drug} and {term} {source_result}[[ ({method})]], while {company} assessed it as {company_result}."],
    "causality_source": ["The {source} considered the relationship between {drug} and {term} {source_result}[[ ({method})]]."],
    "causality_company": ["{company} assessed the relationship between {drug} and {term} as {company_result}[[ ({method})]]."],
}
_REFERENCES = {"references": ["Associated case reference(s): {linked_refs}.", "{parent_child}"]}

DEFAULT_TEMPLATES = {
    "Spontaneous": {
        "intro": ["This spontaneous case was received by {company} on {day0} from {reporter_type}[[ via {channel}]][[, through {partner}]]."],
        "patient": _PATIENT, "drug": _DRUG, **_EVENTS, **_CLOSING, **_CAUSALITY, **_REFERENCES,
    },
    "Clinical trial": {
        "intro": [
            "This clinical trial case was received by {company} on {day0} from {reporter_type}[[ via {channel}]].",
            "The case was reported in study {study_id}[[ for subject {subject_id}]].",
            "{blinding}",
        ],
        "patient": _PATIENT, "drug": _DRUG, **_EVENTS, **_CLOSING, **_CAUSALITY, **_REFERENCES,
    },
    "Literature": {
        "intro": [
            "This literature case was retrieved by {company} on {day0} from an article by {lit_authors}, published in {lit_journal} in {lit_year}, titled '{lit_title}'[[ ({lit_doi})]]. || This literature case was retrieved by {company} on {day0} from the published article: {lit_reference}.",
            "The date {company} became aware of the article ({day0}) is distinct from the publication date and from the dates of the clinical events.",
        ],
        "patient": _PATIENT, "drug": _DRUG, **_EVENTS, **_CLOSING, **_CAUSALITY, **_REFERENCES,
    },
}

PLACEHOLDER = re.compile(
    r"^\s*(x{2,}|tbd|tbc|n/?a|example|sample|dummy|test patient|john doe|jane doe|lorem ipsum.*|\[[^\]]*\]|\{[^}]*\}|<[^>]*>|\?+)\s*$",
    re.IGNORECASE,
)
QUALIFIER = {
    "Physician": "a physician", "Pharmacist": "a pharmacist", "Other health professional": "a healthcare professional",
    "Lawyer": "a lawyer", "Consumer or other non-health professional": "a consumer",
}
SOURCE_TYPE = {
    "Healthcare professional": "a healthcare professional", "Patient or consumer": "a consumer",
    "Distributor": "a distributor", "Literature": "the literature", "Regulatory authority": "a regulatory authority",
}
CAUSALITY_SOURCE_WORDS = {
    "Reporter": "reporter", "Healthcare professional": "treating healthcare professional", "Investigator": "investigator",
    "Literature author": "author", "Regulatory authority": "regulatory authority",
}


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

def _rollback():
    try:
        from app.db import get_db

        get_db().rollback()
    except Exception:
        pass


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value


def ensure_defaults():
    """Version 1 of each template (the platform default) if none exists yet."""
    existing = {r["case_type"] for r in query_all("SELECT DISTINCT case_type FROM pv.narrative_templates")}
    missing = [t for t in CASE_TYPES if t not in existing]
    if not missing:
        return
    with transaction() as cursor:
        for case_type in missing:
            cursor.execute(
                """
                INSERT INTO pv.narrative_templates (case_type, version, status, sections, change_note, approved_at)
                VALUES (%s, 1, 'Approved', %s::jsonb, 'Platform default template.', NOW())
                ON CONFLICT DO NOTHING
                """,
                (case_type, json.dumps(DEFAULT_TEMPLATES[case_type])),
            )


def _template_row(row):
    if not row:
        return None
    row = dict(row)
    row["sections"] = _json(row.get("sections"), {})
    return row


def approved_template(case_type):
    ensure_defaults()
    return _template_row(query_one(
        "SELECT * FROM pv.narrative_templates WHERE case_type = %s AND status = 'Approved'", (case_type,)))


def get_template(template_id):
    return _template_row(query_one(
        """
        SELECT t.*, c.full_name AS created_by_name, a.full_name AS approved_by_name
        FROM pv.narrative_templates AS t
        LEFT JOIN pv.users AS c ON c.user_id = t.created_by
        LEFT JOIN pv.users AS a ON a.user_id = t.approved_by
        WHERE t.template_id = %s
        """,
        (template_id,),
    ))


def list_templates():
    ensure_defaults()
    rows = query_all(
        """
        SELECT t.*, c.full_name AS created_by_name, a.full_name AS approved_by_name
        FROM pv.narrative_templates AS t
        LEFT JOIN pv.users AS c ON c.user_id = t.created_by
        LEFT JOIN pv.users AS a ON a.user_id = t.approved_by
        ORDER BY t.case_type, t.version DESC
        """
    )
    return [_template_row(r) for r in rows]


def parse_sections(form):
    """Sections from the edit form: one sentence per line."""
    sections, problems = {}, []
    for key in SECTION_KEYS:
        lines = [" ".join(line.split()) for line in (form.get(f"section-{key}") or "").splitlines()]
        sections[key] = [line for line in lines if line]
        for line in sections[key]:
            for part in line.split("||"):
                unknown = sorted(set(re.findall(r"\{([a-z0-9_]+)\}", part)) - set(TOKENS))
                if unknown:
                    problems.append(f"{key}: unknown field(s) {', '.join('{' + u + '}' for u in unknown)}")
                if part.count("[[") != part.count("]]"):
                    problems.append(f"{key}: an optional part [[ ]] is not closed in “{part.strip()[:60]}”")
    if not sections["intro"]:
        problems.append("The introduction needs at least one sentence.")
    return sections, problems


def save_draft(case_type, sections, note, user_id):
    if case_type not in CASE_TYPES:
        raise ValueError("Unknown case type.")
    with transaction() as cursor:
        cursor.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM pv.narrative_templates WHERE case_type = %s", (case_type,))
        version = cursor.fetchone()["v"]
        cursor.execute(
            """
            INSERT INTO pv.narrative_templates (case_type, version, status, sections, change_note, created_by)
            VALUES (%s, %s, 'Draft', %s::jsonb, %s, %s) RETURNING template_id
            """,
            (case_type, version, json.dumps(sections), (note or "").strip()[:2000] or None, user_id),
        )
        return cursor.fetchone()["template_id"], version


def approve(template_id, user_id):
    row = get_template(template_id)
    if not row or row["status"] != "Draft":
        raise ValueError("Only a draft template can be approved.")
    with transaction() as cursor:
        cursor.execute("UPDATE pv.narrative_templates SET status = 'Historic' WHERE case_type = %s AND status = 'Approved'",
                       (row["case_type"],))
        cursor.execute(
            "UPDATE pv.narrative_templates SET status = 'Approved', approved_by = %s, approved_at = NOW() WHERE template_id = %s",
            (user_id, template_id),
        )
    return row


def delete_draft(template_id):
    with transaction() as cursor:
        cursor.execute("DELETE FROM pv.narrative_templates WHERE template_id = %s AND status = 'Draft' RETURNING template_id",
                       (template_id,))
        return cursor.fetchone() is not None


# --------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------

def case_type_for(case):
    category = case.get("case_category")
    if category in CASE_TYPES:
        return category
    if (case.get("icsr_report_type") or "").lower().startswith("report from study") or case.get("study_identifier"):
        return "Clinical trial"
    if case.get("literature_reference") or case.get("literature_title") or case.get("source") == "Literature":
        return "Literature"
    return "Spontaneous"


class Facts:
    """Token values with the structured field each came from."""

    def __init__(self, issues):
        self.values, self.fields, self.issues = {}, {}, issues

    def put(self, token, value, field):
        if value is None:
            return
        text = " ".join(str(value).split()) if not isinstance(value, str) else value.strip()
        if not text:
            return
        if PLACEHOLDER.match(text):
            self.issues.append(f"{field} looks like a placeholder or sample value (“{text[:40]}”) and was left out.")
            return
        self.values[token] = text
        self.fields[token] = field

    def child(self):
        other = Facts(self.issues)
        other.values, other.fields = dict(self.values), dict(self.fields)
        return other


def _lower(value):
    return value[:1].lower() + value[1:] if value and not value[:2].isupper() else value


def _join(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _number(value):
    if value is None:
        return None
    text = f"{value}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def drug_label(product):
    name = (product.get("brand_name") or product.get("product_name") or "").strip()
    generic = (product.get("generic_name") or "").strip()
    if generic and name and generic.lower() not in name.lower():
        return f"{name} ({generic})"
    return name or generic


def _age(case):
    value, unit = _number(case.get("patient_age_value")), (case.get("patient_age_unit") or "Years")
    if not value:
        return None
    singular = {"Years": "year", "Months": "month", "Weeks": "week", "Days": "day", "Hours": "hour", "Decades": "decade"}.get(unit, "year")
    return f"{value}-{singular}-old"


def gather(record, company_name):
    """Facts for the whole case: case-level facts plus the per-item lists."""
    issues = []
    case = record["case"]
    f = Facts(issues)
    f.put("company", company_name, "Company profile")
    if case.get("received_date"):
        f.put("day0", icsr.show_partial(str(case["received_date"])), "Date first received")
    f.put("reporter_type", QUALIFIER.get(case.get("reporter_qualification")) or SOURCE_TYPE.get(case.get("source")),
          "Reporter qualification")
    f.put("channel", _lower(case.get("reporting_channel")), "Reporting channel")
    f.put("partner", case.get("reporting_partner"), "Received through partner")
    f.put("study_id", case.get("study_identifier"), "Study identifier")
    f.put("subject_id", case.get("study_subject_id"), "Subject identifier")
    if case.get("study_blinded") == "Yes":
        f.put("blinding", "The study treatment remains blinded; no treatment assignment is stated in this narrative.", "Study blinded")
    f.put("lit_authors", case.get("literature_authors"), "Article authors")
    f.put("lit_journal", case.get("literature_journal"), "Journal")
    f.put("lit_year", case.get("literature_year"), "Publication year")
    f.put("lit_title", case.get("literature_title"), "Article title")
    f.put("lit_doi", case.get("literature_doi"), "DOI / PMID")
    f.put("lit_reference", case.get("literature_reference"), "Literature reference")

    f.put("age", _age(case), "Age at onset")
    if not _age(case):
        f.put("age_group", _lower(case.get("patient_age_group")), "Age group")
    if case.get("patient_dob_text"):
        f.put("dob", icsr.show_partial(case["patient_dob_text"]), "Date of birth")
    if case.get("patient_sex") in ("Female", "Male"):
        f.put("sex", case["patient_sex"].lower(), "Sex")
    f.put("country", case.get("country_name"), "Country")
    f.put("ethnicity", case.get("patient_ethnicity"), "Ethnicity")
    if case.get("patient_weight_value"):
        f.put("weight", f"{_number(case['patient_weight_value'])} {case.get('patient_weight_unit') or 'kg'}", "Weight")
    if case.get("patient_height_value"):
        f.put("height", f"{_number(case['patient_height_value'])} {case.get('patient_height_unit') or 'cm'}", "Height")
    if (case.get("patient_pregnancy_status") or "").lower() in ("yes", "pregnant"):
        f.put("pregnancy", "pregnant at the time of the event", "Pregnancy status")

    history = [h for h in record.get("history") or [] if not h.get("for_parent")]
    own = [_history_text(h) for h in history if not h.get("family_history")]
    family = [_history_text(h) for h in history if h.get("family_history")]
    if case.get("medical_history"):
        own.append(case["medical_history"].strip().rstrip("."))
    f.put("history", _join(own), "Medical history")
    if not own and case.get("medical_history_reported") == "No":
        f.put("history_stated_none", "no", "Relevant medical history reported")
    f.put("family_history", _join(family), "Family history")
    f.put("allergies", case.get("patient_allergies"), "Allergies")
    f.put("social_history", case.get("social_history"), "Social history")
    past = [d for d in record.get("past_drugs") or [] if not d.get("for_parent")]
    f.put("past_drugs", _join([
        d["drug_name"] + (f" for {d['indication_text']}" if d.get("indication_text") else "")
        + (f" (reaction: {d['reaction_text']})" if d.get("reaction_text") else "") for d in past]), "Past drugs")
    concomitants = [p for p in record.get("products") or [] if p.get("product_role") in ("Concomitant", "Treatment of the reaction")]
    con_text = _join([drug_label(p) + (f" for {p['indication']}" if p.get("indication") else "") for p in concomitants if p.get("product_role") == "Concomitant"])
    if not con_text and case.get("concomitant_medicines"):
        con_text = case["concomitant_medicines"].strip().rstrip(".")
    f.put("concomitants", con_text, "Concomitant medicines")
    f.put("treatment", (case.get("treatment_given") or "").strip().rstrip("."), "Treatment given")
    if case.get("patient_died") == "Yes":
        if case.get("death_date_text"):
            f.put("death_date", icsr.show_partial(case["death_date_text"]), "Date of death")
        f.put("death_causes", case.get("death_reported_causes"), "Reported cause(s) of death")
        f.put("autopsy", case.get("autopsy_causes"), "Autopsy causes")
    f.put("linked_refs", case.get("linked_case_refs"), "Linked case references")
    if case.get("parent_child_report") == "Yes":
        f.put("parent_child", "This is a parent–child report.", "Parent–child report")
    return f, issues


def _history_text(h):
    text = h["condition_text"].strip().rstrip(".")
    when = []
    if h.get("start_text"):
        when.append(f"since {icsr.show_partial(h['start_text'])}")
    if h.get("continuing") == "Yes":
        when.append("ongoing")
    elif h.get("end_text"):
        when.append(f"until {icsr.show_partial(h['end_text'])}")
    return text + (f" ({', '.join(when)})" if when else "")


def _sort_key(partial):
    return (0, partial) if partial else (1, "")


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _render_one(sentence, facts):
    """One alternative: (text, fields) or None when a required fact is missing."""
    used = []

    def fill(part, required):
        tokens = re.findall(r"\{([a-z0-9_]+)\}", part)
        if any(t not in facts.values for t in tokens):
            return None
        used.extend(facts.fields[t] for t in tokens)
        return re.sub(r"\{([a-z0-9_]+)\}", lambda m: facts.values[m.group(1)], part)

    pieces = re.split(r"(\[\[.*?\]\])", sentence)
    out, any_token, any_filled = [], False, False
    for piece in pieces:
        if piece.startswith("[[") and piece.endswith("]]"):
            inner = piece[2:-2]
            if re.search(r"\{[a-z0-9_]+\}", inner):
                any_token = True
            value = fill(inner, False)
            if value is not None:
                out.append(value)
                any_filled = any_filled or bool(re.search(r"\{[a-z0-9_]+\}", inner))
        else:
            if re.search(r"\{[a-z0-9_]+\}", piece):
                any_token = True
                any_filled = True
            value = fill(piece, True)
            if value is None:
                return None
            out.append(value)
    if any_token and not any_filled:
        return None   # nothing to say
    text = "".join(out)
    text = re.sub(r"\s+([,.;)])", r"\1", re.sub(r"\s{2,}", " ", text)).strip()
    text = _articles(text)
    # Partial dates read "in Sep 2026" / "in 2026", full dates "on 16 Sep 2026".
    text = re.sub(r"\b([Oo])n ((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}|\d{4})\b(?! \w+ \d)",
                  lambda m: ("I" if m.group(1) == "O" else "i") + "n " + m.group(2), text)
    if not text:
        return None
    return text[:1].upper() + text[1:], list(dict.fromkeys(used))


def _articles(text):
    return re.sub(r"\b([Aa]) (?=(?:[aeiou]|8|11-|18-|8\d-)[\w-])", lambda m: m.group(1) + "n ", text)


def render_sentence(item, facts):
    for alternative in item.split("||"):
        result = _render_one(alternative.strip(), facts)
        if result:
            return result
    return None


def build(record, template, company_name):
    """The narrative text, its sentences with sources, and the issues found."""
    facts, issues = gather(record, company_name)
    sections = template["sections"]
    sentences = []

    def emit(section, local_facts, paragraph):
        for item in sections.get(section) or []:
            result = render_sentence(item, local_facts)
            if not result:
                continue
            text, fields = result
            if re.search(r"\{[a-z0-9_]+\}|\[\[|\]\]", text):
                issues.append(f"A template sentence in “{section}” could not be completed and was left out.")
                continue
            text = re.sub(r"^The patient was an? patient from ", "The patient was from ", text)
            sentences.append({"paragraph": paragraph, "section": section, "text": text, "fields": fields})

    products = record.get("products") or []
    suspects = [p for p in products if (p.get("product_role") or "Suspect") in ("Suspect", "Interacting")]
    reactions = sorted(record.get("reactions") or [], key=lambda r: _sort_key(r.get("start_text")))
    tests = sorted(record.get("tests") or [], key=lambda t: _sort_key(t.get("test_date_text")))

    emit("intro", facts, "intro")
    emit("patient", facts, "patient")

    drug_facts = {}
    for product in suspects:
        d = facts.child()
        d.put("drug", drug_label(product), "Drug name")
        d.put("strength", product.get("strength"), "Strength")
        d.put("form", _lower(product.get("dosage_form")), "Pharmaceutical form")
        dose = (f"{_number(product['dose_value'])} {product.get('dose_unit') or ''}".strip()
                if product.get("dose_value") else product.get("dose"))
        d.put("dose", dose, "Dose")
        d.put("frequency", _lower(product.get("frequency")), "Frequency")
        d.put("route", _lower(product.get("route")), "Route")
        d.put("indication", _lower(product.get("indication")), "Indication")
        if product.get("start_text"):
            d.put("start", icsr.show_partial(product["start_text"]), "Treatment start")
        if product.get("end_text"):
            d.put("stop", icsr.show_partial(product["end_text"]), "Treatment stop")
        d.put("batch", product.get("batch_number"), "Batch number")
        if product.get("expiry_date"):
            d.put("expiry", icsr.show_partial(str(product["expiry_date"])), "Expiry date")
        if product.get("action_taken") and product["action_taken"] != "Not applicable":
            d.put("action", _lower(product["action_taken"]), "Action taken")
        drug_facts[product.get("case_product_id")] = d
        emit("drug", d, "drug")
        start, stop = icsr.full_date(product.get("start_text")), icsr.full_date(product.get("end_text"))
        if start and stop and stop < start:
            issues.append(f"{drug_label(product)}: the stop date is before the start date.")

    first = suspects[0] if suspects else None
    timeline = [("reaction", r.get("start_text"), r) for r in reactions] + [("test", t.get("test_date_text"), t) for t in tests]
    timeline.sort(key=lambda x: _sort_key(x[1]))
    reaction_facts = {}
    for kind, _, row in timeline:
        if kind == "reaction":
            e = facts.child()
            e.put("term", row.get("reported_term"), "Reaction as reported")
            if row.get("start_text"):
                e.put("onset", icsr.show_partial(row["start_text"]), "Reaction start")
            onset, end = icsr.full_date(row.get("start_text")), icsr.full_date(row.get("end_text"))
            if first:
                start = icsr.full_date(first.get("start_text"))
                if onset and start:
                    days = (onset - start).days
                    if days >= 0:
                        e.put("tto", f"{days} day{'s' if days != 1 else ''} after starting {drug_label(first)}", "Calculated from complete dates")
                    else:
                        issues.append(f"“{row.get('reported_term')}” started before {drug_label(first)} was started; check the dates.")
            if onset and end and end < onset:
                issues.append(f"“{row.get('reported_term')}”: the end date is before the onset date.")
            received = record["case"].get("received_date")
            if onset and isinstance(received, date) and onset > received:
                issues.append(f"“{row.get('reported_term')}” is dated after the case was received; check the dates.")
            if row.get("serious") == "Yes":
                criteria = [label.lower() for key, label in icsr.SERIOUSNESS_CRITERIA if row.get(key)]
                e.put("criteria", _join(criteria) or "criteria not specified", "Seriousness criteria")
            elif row.get("serious") == "No":
                e.put("nonserious", "non-serious", "Serious")
            if row.get("outcome"):
                e.put("outcome", _lower(row["outcome"]), "Outcome")
            if row.get("end_text"):
                e.put("end", icsr.show_partial(row["end_text"]), "Reaction end")
            reaction_facts[row.get("reaction_id")] = e
            emit("event", e, "events")
            if row.get("outcome") == "Fatal" and record["case"].get("patient_died") != "Yes":
                issues.append(f"The outcome of “{row.get('reported_term')}” is fatal but the patient is not recorded as having died.")
        else:
            t = facts.child()
            t.put("test", row.get("test_name"), "Test name")
            if row.get("test_date_text"):
                t.put("test_date", icsr.show_partial(row["test_date_text"]), "Test date")
            t.put("result", row.get("result_text"), "Test result")
            t.put("unit", row.get("result_unit"), "Result unit")
            t.put("low", row.get("normal_low"), "Normal low")
            t.put("high", row.get("normal_high"), "Normal high")
            emit("test", t, "events")
    emit("treatment", facts, "events")

    for product in suspects:
        emit("closing_drug", drug_facts[product.get("case_product_id")], "closing")
    for pair in record.get("drug_reactions") or []:
        d, e = drug_facts.get(pair.get("case_product_id")), reaction_facts.get(pair.get("reaction_id"))
        if not d or not e:
            continue
        p = d.child()
        p.put("term", e.values.get("term"), "Reaction as reported")
        p.put("dechallenge", _lower(pair.get("dechallenge")), "Dechallenge")
        p.put("rechallenge", _lower(pair.get("rechallenge")), "Rechallenge")
        emit("closing_pair", p, "closing")
    for row in reactions:
        emit("closing_event", reaction_facts[row.get("reaction_id")], "closing")
    if record["case"].get("patient_died") == "Yes":
        emit("death", facts, "closing")
        if not any(r.get("outcome") == "Fatal" for r in reactions):
            issues.append("The patient is recorded as having died, but no reaction has a fatal outcome; check whether this is consistent.")

    _causality(record, drug_facts, reaction_facts, issues, emit)
    emit("references", facts, "references")

    paragraphs = []
    for name, _ in PARAGRAPHS:
        text = " ".join(s["text"] for s in sentences if s["paragraph"] == name)
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs), sentences, list(dict.fromkeys(issues))


def _causality(record, drug_facts, reaction_facts, issues, emit):
    rows = record.get("causality") or []
    drafts = [r for r in rows if r.get("is_ai_draft") or r.get("source") == "AI-assisted draft"]
    if drafts:
        issues.append(f"{len(drafts)} AI-assisted causality draft(s) were left out: only assessments a person recorded are described.")
    documented = [r for r in rows if r not in drafts and r.get("result")]
    pairs = {}
    for row in documented:
        pairs.setdefault((row.get("case_product_id"), row.get("reaction_id")), []).append(row)
    for (product_id, reaction_id), assessments in pairs.items():
        d, e = drug_facts.get(product_id), reaction_facts.get(reaction_id)
        if not d or not e:
            continue
        company = [a for a in assessments if a.get("source") == "Company"]
        sources = [a for a in assessments if a.get("source") != "Company"]
        for index, source in enumerate(sources or [None]):
            c = d.child()
            c.put("term", e.values.get("term"), "Reaction as reported")
            if source:
                c.put("source", CAUSALITY_SOURCE_WORDS.get(source.get("source"), (source.get("source") or "reporter").lower()), "Causality source")
                c.put("source_result", (source.get("result") or "").lower(), "Causality result")
                c.put("method", source.get("method"), "Causality method")
            if company and index == 0:
                c.put("company_result", (company[0].get("result") or "").lower(), "Company causality")
                if not source:
                    c.put("method", company[0].get("method"), "Causality method")
            key = ("causality_both" if source and company and index == 0
                   else "causality_source" if source else "causality_company")
            emit(key, c, "causality")


# --------------------------------------------------------------------------
# Narrative versions on a case
# --------------------------------------------------------------------------

def versions(case_id):
    rows = query_all(
        """
        SELECT n.*, c.full_name AS created_by_name, r.full_name AS reviewed_by_name
        FROM pv.case_narratives AS n
        LEFT JOIN pv.users AS c ON c.user_id = n.created_by
        LEFT JOIN pv.users AS r ON r.user_id = n.reviewed_by
        WHERE n.case_id = %s ORDER BY n.version DESC
        """,
        (case_id,),
    )
    out = []
    for row in rows:
        row = dict(row)
        row["sentences"] = _json(row.get("sentences"), [])
        row["issues"] = _json(row.get("issues"), [])
        out.append(row)
    return out


def current_version(case_id):
    return next((v for v in versions(case_id) if v["status"] in ("Current", "Reviewed")), None)


def _insert(cursor, case_id, text, source, user_id, status, template=None, sentences=None, issues=None):
    cursor.execute("SELECT COALESCE(MAX(version), 0) + 1 AS v FROM pv.case_narratives WHERE case_id = %s", (case_id,))
    version = cursor.fetchone()["v"]
    cursor.execute(
        """
        INSERT INTO pv.case_narratives (case_id, version, narrative_text, source, template_id, template_case_type,
                                        template_version, sentences, issues, status, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        RETURNING narrative_id
        """,
        (case_id, version, text, source, template and template["template_id"], template and template["case_type"],
         template and template["version"], json.dumps(sentences or []), json.dumps(issues or []), status, user_id),
    )
    return cursor.fetchone()["narrative_id"], version


def generate_draft(case_id, user_id):
    """A new generated draft. The case's current narrative is not touched."""
    from app.services.company_profile import company

    record = icsr.load_icsr(case_id)
    if not record:
        raise ValueError("Case not found.")
    case_type = case_type_for(record["case"])
    template = approved_template(case_type)
    text, sentences, issues = build(record, template, company()["legal_name"])
    if not text:
        raise ValueError("There are not enough recorded facts to write a narrative yet.")
    with transaction() as cursor:
        _adopt_legacy(cursor, case_id, record["case"], user_id)
        narrative_id, version = _insert(cursor, case_id, text, "Generated", user_id, "Draft", template, sentences, issues)
    return narrative_id, version, issues


def _adopt_legacy(cursor, case_id, case, user_id):
    """Keep a narrative written before versioning as version 1."""
    cursor.execute("SELECT 1 FROM pv.case_narratives WHERE case_id = %s LIMIT 1", (case_id,))
    if not cursor.fetchone() and (case.get("case_narrative") or "").strip():
        _insert(cursor, case_id, case["case_narrative"].strip(), "Earlier narrative", None, "Current")


def adopt(case_id, text, user_id, from_narrative_id=None):
    """Make text the case narrative (a new version). Human edits are kept as
    typed; a generated draft used unchanged keeps its template and sources."""
    text = (text or "").strip()
    if not text:
        raise ValueError("The narrative is empty.")
    if re.search(r"\{[a-z0-9_]+\}|\[\[|\]\]", text):
        raise ValueError("The narrative still contains template placeholders such as {field} or [[ ]]; complete or remove them.")
    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    draft = query_one("SELECT * FROM pv.case_narratives WHERE narrative_id = %s AND case_id = %s",
                      (from_narrative_id, case_id)) if from_narrative_id else None
    with transaction() as cursor:
        _adopt_legacy(cursor, case_id, case, user_id)
        cursor.execute("UPDATE pv.case_narratives SET status = 'Superseded' WHERE case_id = %s AND status IN ('Current', 'Reviewed')",
                       (case_id,))
        unchanged = draft and " ".join(draft["narrative_text"].split()) == " ".join(text.split())
        if unchanged:
            cursor.execute("UPDATE pv.case_narratives SET status = 'Current' WHERE narrative_id = %s", (draft["narrative_id"],))
            version = draft["version"]
        else:
            template = None
            if draft and draft.get("template_id"):
                template = {"template_id": draft["template_id"], "case_type": draft["template_case_type"],
                            "version": draft["template_version"]}
            _, version = _insert(cursor, case_id, text, "Edited", user_id, "Current", template,
                                 _json(draft.get("sentences"), []) if draft else [],
                                 _json(draft.get("issues"), []) if draft else [])
        cursor.execute("UPDATE pv.safety_cases SET case_narrative = %s, updated_at = NOW() WHERE case_id = %s", (text, case_id))
        cursor.execute(
            "INSERT INTO pv.case_audit_log (case_id, action, details, performed_by) VALUES (%s, %s, %s, %s)",
            (case_id, "Narrative updated", f"Narrative version {version} is now the case narrative"
             + (" (generated draft used unchanged)." if unchanged else " (edited by a person)."), user_id),
        )
    return version


def record_form_edit(case_id, before_text, after_text, user_id):
    """The narrative was changed in the ICSR form: keep it as a version."""
    if " ".join((before_text or "").split()) == " ".join((after_text or "").split()):
        return None
    with transaction() as cursor:
        cursor.execute("SELECT 1 FROM pv.case_narratives WHERE case_id = %s LIMIT 1", (case_id,))
        if not cursor.fetchone() and (before_text or "").strip():
            _insert(cursor, case_id, before_text.strip(), "Earlier narrative", None, "Superseded")
        cursor.execute("UPDATE pv.case_narratives SET status = 'Superseded' WHERE case_id = %s AND status IN ('Current', 'Reviewed')",
                       (case_id,))
        if (after_text or "").strip():
            return _insert(cursor, case_id, after_text.strip(), "ICSR form", user_id, "Current")[1]
    return None


def mark_reviewed(case_id, user_id, qppv_alone=False):
    current = current_version(case_id)
    if not current:
        raise ValueError("There is no case narrative to review yet.")
    if current["status"] == "Reviewed":
        raise ValueError("The current narrative is already reviewed.")
    alone = False
    if current.get("created_by") and current["created_by"] == user_id:
        if not qppv_alone:
            raise ValueError("You wrote or adopted this version; another person must review it.")
        alone = True
    with transaction() as cursor:
        cursor.execute("UPDATE pv.case_narratives SET status = 'Reviewed', reviewed_by = %s, reviewed_at = NOW() WHERE narrative_id = %s",
                       (user_id, current["narrative_id"]))
        cursor.execute(
            "INSERT INTO pv.case_audit_log (case_id, action, details, performed_by) VALUES (%s, %s, %s, %s)",
            (case_id, "Narrative reviewed", f"Narrative version {current['version']} reviewed."
             + (" Reviewed by its author (QPPV processing the case alone, allowed in Sign-off settings)." if alone else ""), user_id),
        )
    return current["version"]


def review_state(case_id, case_narrative):
    """For the pathway: 'none', 'unreviewed', 'reviewed' or 'legacy'."""
    if not (case_narrative or "").strip():
        return "none"
    try:
        current = current_version(case_id)
    except Exception:
        _rollback()
        return "legacy"
    if not current:
        return "legacy"
    return "reviewed" if current["status"] == "Reviewed" else "unreviewed"
