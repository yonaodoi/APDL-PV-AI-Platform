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
6. Listedness and expectedness assessment
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
                "num_predict": 1100,
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