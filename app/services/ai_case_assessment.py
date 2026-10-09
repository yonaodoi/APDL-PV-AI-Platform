import json
import re

from app.services.llm import deidentify, describe_model, generate_text
from app.services.reporting_clock import evaluate_reporting_clock
from app.services.company_profile import platform as _co_platform, platform_name as _co_pv, short_name as _co_short


SYSTEM_PROMPT = (
    "You are an experienced pharmacovigilance physician supporting a "
    "qualified person for pharmacovigilance (QPPV). You write precise, "
    "evidence-based draft assessments, never invent facts, and clearly "
    "separate reported facts from your assessment."
)


ASSESSMENT_SECTIONS = (
    "Case identification and validity",
    "Reported clinical event and chronology",
    "Suspected product and relevant medical context",
    "Comparison with {company} Product Information",
    "Comparison with innovator Reference Safety Information",
    "Reference safety assessment: listedness, expectedness and frequency",
    "Seriousness assessment",
    "Causality assessment",
    "Data limitations and follow-up required",
    "Regulatory reporting consideration",
    "Overall conclusion",
)

_HEADING_PATTERN = re.compile(r"^\s*(\d{1,2})\.\s+\S", re.M)


# Database bookkeeping fields that add noise to an assessment.
INTERNAL_FIELDS = {
    "case_id",
    "case_product_id",
    "country_id",
    "created_by",
    "created_at",
    "updated_at",
    "assessment_id",
    "rsi_id",
    "assessed_by",
    "assessed_at",
}


def _clinical(record):
    return {
        key: value
        for key, value in deidentify(record).items()
        if key not in INTERNAL_FIELDS and value not in (None, "")
    }


def timing_facts(case, product):
    """Plain-language facts about the time relationship, computed exactly."""
    case = case or {}
    product = product or {}
    onset = case.get("event_onset_date")
    start = product.get("therapy_start_date")
    end = product.get("therapy_end_date")
    facts = []
    if onset and start:
        gap = (onset - start).days
        if gap < 0:
            facts.append(
                f"The event began {-gap} day(s) before the suspected product "
                f"was started (onset {onset}, therapy start {start})."
            )
        else:
            facts.append(
                f"The event began {gap} day(s) after the suspected product "
                f"was started (therapy start {start}, onset {onset})."
            )
    elif onset:
        facts.append(
            "The therapy start date is not recorded, so the time to onset "
            "cannot be determined."
        )
    if onset and end and end < onset:
        facts.append(
            f"Therapy stopped on {end}, before the event began on {onset}."
        )
    if case.get("patient_pregnancy_status") == "Yes":
        facts.append("The patient is recorded as pregnant.")
    return facts


DEATH_WORDS = ("death", "died", "dead", "fatal", "deceased")


def required_queries(case, product):
    """Follow-up queries the report must always contain, worded exactly."""
    case = case or {}
    product = product or {}
    name = product.get("product_name") or "the suspected product"
    onset = case.get("event_onset_date")
    start = product.get("therapy_start_date")
    queries = []

    if onset and start and onset < start:
        queries.append(
            f"Confirm the event onset date and the date {name} was first "
            f"given; the recorded onset ({onset:%d %b %Y}) is before the "
            f"recorded start of therapy ({start:%d %b %Y})."
        )
    elif onset and not start:
        queries.append(f"Provide the date {name} was first given.")

    criteria = (case.get("seriousness_criteria") or "").lower()
    outcome = case.get("event_outcome") or ""
    if any(word in criteria for word in DEATH_WORDS) and outcome != "Fatal":
        queries.append(
            "Confirm whether the patient died. If not, provide the correct "
            f"seriousness criterion; the recorded outcome is {outcome or 'not stated'}."
        )

    birth = case.get("patient_date_of_birth")
    age = case.get("patient_age_years")
    reference = onset or case.get("received_date")
    if birth and age is not None and reference:
        derived = reference.year - birth.year - (
            (reference.month, reference.day) < (birth.month, birth.day)
        )
        if abs(derived - age) > 1:
            queries.append(
                "Provide the patient's correct date of birth; the recorded "
                f"date ({birth:%d %b %Y}) does not match the recorded age "
                f"of {age} years."
            )

    if case.get("patient_pregnancy_status") == "Yes":
        queries.append(
            "Provide pregnancy details: gestational age at exposure, "
            "expected delivery date, and the pregnancy outcome when known."
        )
    return queries


def _bullets(items, empty_text):
    items = [item for item in (items or []) if item]
    if not items:
        return f"- {empty_text}"
    return "\n".join(f"- {item}" for item in items)


def regulatory_facts(case, safety_assessment, today=None):
    """Expedited-reporting position and reporting clock, as recorded."""
    case = case or {}
    serious = bool(case.get("seriousness"))
    listedness = (safety_assessment or {}).get("listedness_status")
    facts = [
        f"Seriousness as recorded: {'serious' if serious else 'non-serious'}. "
        f"Listedness: {listedness or 'not yet assessed'}."
    ]
    if serious and listedness == "Not listed":
        facts.append(
            "As recorded, the case is serious and the event is not listed, so "
            "it meets the criteria for expedited (15-day) reporting to the "
            "regulator. This stands unless follow-up shows the case is not "
            "serious."
        )
    elif serious:
        facts.append(
            "As recorded, the case is serious. Whether expedited reporting is "
            "required depends on listedness and the national requirement."
        )
    else:
        facts.append(
            "As recorded, the case is non-serious and is normally reported "
            "in periodic reports rather than expedited."
        )

    clock = evaluate_reporting_clock(case, today=today)
    if clock:
        received = case["received_date"]
        submitted = clock["submitted_date"]
        facts.append(
            f"Reporting clock: Day 0 {received:%d %b %Y}; "
            f"{clock['timeline_days']}-day timeline; due "
            f"{clock['due_date']:%d %b %Y}; status: {clock['label']}"
            + (f" (submitted {submitted:%d %b %Y})." if submitted else ".")
        )
    return facts


def assessment_model_name():
    return describe_model()


def describe_assessment_progress(text_so_far):
    """Return (section_number, section_title, word_count) for partial text.

    section_number is 0 before the first heading has been written.
    """
    numbers = [
        int(match.group(1))
        for match in _HEADING_PATTERN.finditer(text_so_far or "")
        if 1 <= int(match.group(1)) <= len(ASSESSMENT_SECTIONS)
    ]
    section = max(numbers) if numbers else 0
    title = ASSESSMENT_SECTIONS[section - 1].replace("{company}", _co_short()) if section else ""
    return section, title, len((text_so_far or "").split())


def progress_message(text_so_far):
    section, title, words = describe_assessment_progress(text_so_far)
    if not section:
        return f"AI model is starting the assessment ({words} words so far)."
    return (
        f"Writing section {section} of {len(ASSESSMENT_SECTIONS)}: "
        f"{title} ({words} words so far)."
    )


def generate_case_assessment(
    case,
    product,
    apdl_product_information,
    innovator_rsi,
    safety_assessment,
    on_progress=None,
    data_quality_findings=None,
):
    """
    Generate an AI draft case-assessment narrative.
    The returned text must be reviewed and approved by an authorised PV user.
    ``on_progress(text_so_far)`` receives partial text while it is written.
    """
    case_data = {
        "case": _clinical(case),
        "suspected_product": _clinical(product),
        "apdl_product_information": dict(
            apdl_product_information or {}
        ),
        "innovator_reference_safety_information": dict(
            innovator_rsi or {}
        ),
        "automated_listedness_assessment": dict(
            safety_assessment or {}
        ),
    }

    prompt = f"""
You are assisting a qualified pharmacovigilance team with a draft
individual case safety report assessment.

Use the supplied case data as the primary source. Use uploaded or
online reference information only when it is provided. Do not ask the
user to upload documents. If reference information is unavailable,
state that listedness, expectedness or frequency cannot be assessed
from the available information.Do not invent facts, dates, clinical findings, label information,
regulatory deadlines, or causality evidence.

Prepare a professional pharmacovigilance case assessment report in
clear English using exactly these headings:

1. Case identification and validity
2. Reported clinical event and chronology
3. Suspected product and relevant medical context
4. Comparison with {_co_short()} Product Information
5. Comparison with innovator Reference Safety Information
6. Reference safety assessment: listedness, expectedness and frequency
7. Seriousness assessment
8. Causality assessment
9. Data limitations and follow-up required
10. Regulatory reporting consideration
11. Overall conclusion

Rules:
- Clearly state when information is missing or cannot be assessed.
- Distinguish reported facts from assessment conclusions.
- Do not state that the product caused the event unless the supplied
  data supports that conclusion.
- Do not replace the accountable QPPV or medical reviewer.
- Apply standard pharmacovigilance principles: assess minimum case
  validity, seriousness criteria, temporal relationship, dechallenge
  and rechallenge where available, alternative causes, concomitant
  medicines, listedness, expectedness, frequency and follow-up needs.
- Do not invent a reporting deadline. State only whether regulatory
  assessment is recommended based on the available case information.
- Assess listedness, expectedness and frequency only for the reported adverse
  event or reaction against the supplied reference safety information. Never
  assess the product itself as listed, not listed, expected or unexpected.
- A generic product is not automatically expected. Do not use generic status
  as a reason for any safety conclusion.
- Treat the saved automated_listedness_assessment values as authoritative:
  when Listedness is "Not listed", Expectedness must be "Unexpected" unless
  the saved assessment explicitly states a justified exception.
- Frequency applies only to the reported event in the reference information.
  If the event is not listed and no frequency is stated, write "Not stated" or
  "Not assessable"; do not invent a frequency category.
- Section 6 is mandatory. Use the saved automated_listedness_assessment
  data directly and organise it under these subheadings: Reference used;
  Reported event; Listedness; Expectedness; Frequency; Evidence;
  Scientific rationale; Limitations and follow-up.
- Preserve the saved assessment rationale where available. Do not replace it
  with a different conclusion unless you clearly identify the reason.
- Use plain text only. Do not use markdown, asterisks, bullets, or an introduction.
- Start every main heading on its own line exactly as numbered 1. through 11.
- Keep the heading wording exactly as provided above.
- Write the related assessment as short paragraphs below each heading.
- Leave one blank line before and after every main heading.
- End with this exact statement:
  "AI-generated draft — QPPV/medical reviewer approval required."


Pre-computed facts (calculated by the platform; treat as correct):
{_bullets(timing_facts(case, product), "No timing facts could be calculated.")}

Required follow-up queries (section 9 must start with these, numbered in
this order, with the wording unchanged; add further specific queries after
them only if needed):
{_bullets(required_queries(case, product), "No required queries.")}

Regulatory facts (calculated by the platform; use them in section 10):
{_bullets(regulatory_facts(case, safety_assessment), "No regulatory facts available.")}

Data quality findings (from the platform's consistency checks). Address
every finding explicitly: describe it in section 1 or 2, explain how it
affects seriousness or causality, and list the follow-up query needed in
section 9. Do not resolve a contradiction by choosing one value yourself.
{_bullets(data_quality_findings, "No data quality findings were raised.")}

Additional writing rules:
- Interpret the data; do not restate every field. Mention a field only when
  it matters to the assessment.
- Never expand, translate or guess the meaning of abbreviations, codes or
  placeholder text (for example a profession recorded as "SC"). Quote them
  exactly as recorded and, if they matter, add a follow-up query.
- If a timing fact shows the event began before the suspected product was
  started, say in sections 2 and 8 that this does not support a causal
  relationship unless the dates are wrong.
- If the patient is recorded as pregnant, state this in section 3 and in the
  overall conclusion as an exposure during pregnancy.
- Base the causality section on chronology, dechallenge/rechallenge,
  alternative causes and the findings above. If the event preceded
  exposure, causality cannot be supported as recorded.
- Section 7: state the seriousness as recorded. If it conflicts with the
  outcome or other data, say it cannot be confirmed until clarified. Do not
  guess whether the event was serious.
- Section 9: write each follow-up question to the reporter on its own line
  as "Query 1:", "Query 2:" and so on. Include one query for every data
  quality finding and for any missing date needed to assess causality.
- Section 10: state whether expedited reporting is required using the
  regulatory facts, and state the reporting clock status and due date.
- Never repeat a sentence. Each sentence must appear only once in the report.

Supplied case and reference data:
{json.dumps(case_data, default=str, indent=2)}
""".strip()

    report = generate_text(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=4000,
        temperature=0.2,
        timeout=240,
        on_progress=on_progress,
    )
    return report.replace("*", "")

def generate_rsi_assessment_explanation(
    event_term,
    listedness,
    expectedness,
    frequency,
    evidence,
):
    """Generate a concise scientific explanation for RSI assessment."""
    prompt = f"""
Prepare a concise pharmacovigilance RSI assessment using only the
information below. Do not invent clinical facts or label statements.
Use clear scientific English and exactly these numbered headings:

1. Reported event
2. Reference safety evidence
3. Listedness assessment
4. Expectedness assessment
5. Frequency assessment
6. Scientific interpretation
7. Data limitations and follow-up

Reported event: {event_term}
Listedness: {listedness}
Expectedness: {expectedness}
Frequency: {frequency}
Reference evidence: {evidence}

State clearly when the evidence is insufficient. End with:
"Automated draft assessment — QPPV/medical reviewer confirmation required."
""".strip()

    explanation = generate_text(
        prompt,
        system=SYSTEM_PROMPT,
        max_tokens=1500,
        temperature=0.1,
        timeout=120,
    )
    return explanation.replace("*", "")