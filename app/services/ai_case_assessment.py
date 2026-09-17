import json
import os

import requests


OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
OLLAMA_MODEL = os.environ.get(
    "OLLAMA_MODEL",
    "llama3.2:1b",
)


def generate_case_assessment(
    case,
    product,
    apdl_product_information,
    innovator_rsi,
    safety_assessment,
):
    """
    Generate an AI draft case-assessment narrative locally through Ollama.
    The returned text must be reviewed and approved by an authorised PV user.
    """
    case_data = {
        "case": dict(case or {}),
        "suspected_product": dict(product or {}),
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
4. Comparison with APDL Product Information
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


Supplied case and reference data:
{json.dumps(case_data, default=str, indent=2)}
""".strip()

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 650,
            },
        },
        timeout=180,
    )
    response.raise_for_status()

    report = response.json().get("response", "").strip()
    if not report:
        raise RuntimeError(
            "The local AI model returned an empty assessment report."
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

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 700,
            },
        },
        timeout=120,
    )
    response.raise_for_status()

    explanation = response.json().get("response", "").strip()
    if not explanation:
        raise RuntimeError("The local AI model returned no RSI explanation.")

    return explanation.replace("*", "")