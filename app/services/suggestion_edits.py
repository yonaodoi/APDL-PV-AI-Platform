"""Correcting AI-suggested values before they are applied.

The suggestion review page shows each suggested value in an editable box.
When the reviewer changes a value, it is checked with the same rules used
for the AI's own values (dates, choices, numbers) and replaces the
suggestion before it is applied. The audit trail records that it was edited.
"""

NOT_EDITABLE = {"country_id"}


def annotate(suggestions, date_fields, choices, append_fields, time_fields=()):
    """Add how each suggestion can be edited, for the template."""
    out = []
    for item in suggestions or []:
        item = dict(item)
        field = item.get("field")
        if field in NOT_EDITABLE:
            item["editor"] = "none"
        elif field in date_fields:
            item["editor"] = "date"
        elif field in time_fields:
            item["editor"] = "time"
        elif field in choices:
            item["editor"] = "choice"
            item["choices"] = list(choices[field])
        elif field in append_fields or len(str(item.get("proposed") or "")) > 80:
            item["editor"] = "textarea"
        else:
            item["editor"] = "text"
        out.append(item)
    return out


def apply_edits(suggestions, form, normalise, selected):
    """Return (suggestions with reviewer edits, errors).

    ``normalise(values)`` must return {field: cleaned string} and drop
    anything invalid, as the AI extraction does.
    """
    updated, errors = [], []
    for item in suggestions or []:
        field = item.get("field")
        key = f"value_{field}"
        if field not in selected or field in NOT_EDITABLE or key not in form:
            updated.append(item)
            continue
        typed = " ".join(str(form.get(key) or "").split()) if item.get("kind") != "add" else str(form.get(key) or "").strip()
        proposed = str(item.get("proposed") or "")
        if typed == proposed.strip() or typed == " ".join(proposed.split()):
            updated.append(item)
            continue
        if not typed:
            errors.append(f"{item.get('label', field)}: the value is empty. Untick the row instead.")
            continue
        cleaned = normalise({field: typed}).get(field)
        if cleaned in (None, ""):
            errors.append(f"{item.get('label', field)}: “{typed}” is not a valid value.")
            continue
        item = dict(item)
        if item.get("kind") == "add":
            new_value = str(item.get("new_value") or "")
            prefix = new_value[: len(new_value) - len(proposed)] if proposed and new_value.endswith(proposed) else new_value + "\n\n"
            item["new_value"] = prefix + cleaned
        else:
            item["new_value"] = cleaned
        item["proposed"] = cleaned
        item["label"] = f"{item.get('label', field)} (edited)"
        updated.append(item)
    return updated, errors
