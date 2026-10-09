import json

from app.services.llm import generate_text

from app.services.psur_evidence import (
    build_psur_evidence_sections,
)
from app.services.company_profile import platform as _co_platform, platform_name as _co_pv, short_name as _co_short




def _section_evidence(report, section_key):
    evidence_sections = build_psur_evidence_sections(report)

    return evidence_sections.get(
        section_key,
        (
            "No section-specific evidence was assembled from the "
            f"{_co_platform()} for this reporting interval."
        ),
    )


def _product_context(report):
    return {
        "product_name": report["product_name"],
        "active_substances": report["active_substances"],
        "therapeutic_indication": report["therapeutic_indication"],
        "mechanism_of_action": report["mechanism_of_action"],
        "reporting_period_start": str(
            report["reporting_period_start"]
        ),
        "reporting_period_end": str(
            report["reporting_period_end"]
        ),
        "countries_covered": report["countries_covered"],
        "marketing_authorisation_number": (
            report["marketing_authorisation_number"]
        ),
        "marketing_authorisation_procedure": (
            report["marketing_authorisation_procedure"]
        ),
    }


def _generate(prompt):
    content = generate_text(
        prompt,
        max_tokens=2500,
        temperature=0.15,
        timeout=180,
    )
    return content.replace("*", "")


def propose_psur_section_content(
    report,
    section_key,
    section_title,
    saved_content=None,
):
    evidence = _section_evidence(report, section_key)
    saved_content = (saved_content or "").strip()

    no_system_evidence = (
        "No section-specific evidence was assembled" in evidence
    )

    if no_system_evidence and not saved_content:
        return {
            "proposed_content": (
                f"No data were captured or available in the {_co_pv()} "
                "platform for this reporting interval."
            ),
            "evidence_used": {
                "section_key": section_key,
                "system_evidence": evidence,
                "saved_content": saved_content,
            },
        }

    product_context = _product_context(report)

    prompt = f"""
You are improving one saved section of a Periodic Benefit-Risk
Evaluation Report for the {_co_short()} pharmacovigilance team.

Exact section heading:
{section_title}

Improve the saved response below. The saved response is the primary
source text. Preserve its factual meaning unless the supplied {_co_short()}
system evidence clearly supports a correction or a relevant addition.

Use only the saved response, product information, and {_co_short()} system
evidence supplied below. Do not invent facts, dates, studies, clinical
trials, literature findings, exposure data, regulatory activities,
risk-minimisation actions, safety conclusions, or product information.

Use past tense when describing activity or information from the
reporting interval. Improve grammar, clarity, structure and scientific
regulatory wording. Where the supplied records support an explanation,
state it logically and specifically. Where the records do not support
an explanation, do not create one.

If no relevant {_co_short()} system evidence was available, improve only the
language of the saved response; do not add facts.

Do not use bullet points, tables, markdown, headings, generic filler,
or instructions. Do not use the words "draft", "should", "must",
"please", "enter", or "review". Output only the improved section
response.

Product information:
{json.dumps(product_context, default=str, indent=2)}

Saved section response:
{saved_content}

{_co_short()} system evidence for this reporting interval:
{evidence}
""".strip()

    proposed_content = _generate(prompt)

    return {
        "proposed_content": proposed_content,
        "evidence_used": {
            "section_key": section_key,
            "system_evidence": evidence,
            "saved_content": saved_content,
            "product_context": product_context,
        },
    }


def draft_psur_section_content(
    report,
    section_key,
    section_title,
):
    evidence = _section_evidence(report, section_key)
    product_context = _product_context(report)
    return {
        "proposed_content": evidence,
        "evidence_used": {
            "section_key": section_key,
            "system_evidence": evidence,
            "product_context": product_context,
            "proposal_basis": "system_evidence",
        },
    }