"""AI mapping for templates that have no markers.

The AI reads the template's labels (e.g. "Patient initials:", a table
header "Batch number") and proposes which report field belongs where. The
proposal is shown to the user to correct and confirm; only then are markers
written into a copy of the template. The AI never fills real data.
"""

import json
import re

from app.services.docx_fill import document_outline
from app.services.report_fields import REPORT_TYPES

HOW = ("after", "replace", "row")


def _catalogue_text(report_type):
    spec = REPORT_TYPES[report_type]
    lines = ["Single fields:"]
    lines += [f"- {name}: {label}" for name, label in spec["fields"].items()]
    for list_name, info in spec["lists"].items():
        lines.append(f"Repeating list '{list_name}' ({info['label']}); use {list_name}.<field> in a table row:")
        lines += [f"- {list_name}.{sub}: {label}" for sub, label in info["fields"].items()]
    return "\n".join(lines)


def build_prompt(report_type, outline):
    spec = REPORT_TYPES[report_type]
    numbered = "\n".join(f"[{item['ref']}] {item['text']}" for item in outline)
    return f"""You are mapping a Word form used for a pharmacovigilance report ("{spec['label']}") to the
fields our system can print. Below are the form's lines. Each starts with a reference:
p<N> is a paragraph, t<T>r<R>c<C> is table T, row R, column C.

FORM:
{numbered}

FIELDS THE SYSTEM CAN PRINT:
{_catalogue_text(report_type)}

For each place in the form where a value should be printed, give one placement:
- "ref": the reference where the value goes. For a label and value in the same paragraph or cell
  ("Patient initials: ____"), use that ref with "how": "after". For a label cell with an empty
  value cell next to it, use the EMPTY cell's ref with "how": "replace". For a table with a header
  row and an empty row meant for several products/events, use the empty row's cells with
  "how": "row" and a list field such as products.batch_number.
- "field": exactly one name from the list above.
- "label": the form's wording you matched.
Only map what clearly matches. Do not invent fields. Leave headings and instructions alone.

Reply with JSON only: {{"placements": [{{"ref": "...", "field": "...", "how": "after|replace|row", "label": "..."}}]}}"""


def clean_placements(report_type, raw, outline):
    """Keep only valid placements: known refs, known fields, one per ref."""
    spec = REPORT_TYPES[report_type]
    valid_fields = set(spec["fields"])
    for list_name, info in spec["lists"].items():
        valid_fields |= {f"{list_name}.{sub}" for sub in info["fields"]}
    refs = {item["ref"]: item["text"] for item in outline}
    out, seen = [], set()
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        ref, field = str(item.get("ref", "")).strip(), str(item.get("field", "")).strip()
        how = item.get("how") if item.get("how") in HOW else "after"
        if ref not in refs or field not in valid_fields or ref in seen:
            continue
        if how == "row" and "." in field and field.split(".")[0] not in spec["lists"]:
            how = "replace"
        seen.add(ref)
        out.append({"ref": ref, "field": field, "how": how,
                    "label": str(item.get("label") or refs[ref])[:120],
                    "form_text": refs[ref][:120]})
    return out


def _parse_json(text):
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("The AI did not return a mapping.")
    return json.loads(match.group(0))


def propose_mapping(report_type, template_bytes):
    """Ask the AI where each field goes. Returns (placements, outline)."""
    from app.services.llm import PURPOSE_EXTRACTION, generate_text

    outline = document_outline(template_bytes)
    if not outline:
        raise ValueError("The template has no text to map.")
    reply = generate_text(
        build_prompt(report_type, outline),
        system="You map document form labels to data fields. Reply with JSON only.",
        max_tokens=4000,
        json_output=True,
        purpose=PURPOSE_EXTRACTION,
    )
    data = _parse_json(reply)
    return clean_placements(report_type, data.get("placements"), outline), outline
