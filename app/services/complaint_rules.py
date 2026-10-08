"""Rules for closing product complaint investigations, and change records."""

from datetime import date

from app.services.complaint_workflow import (
    ADVERSE_EVENT_CATEGORY,
    has_draft_placeholders,
)


STATUS_OPEN = "Under investigation"
STATUS_COMPLETE = "Investigation complete"


def validate_complaint_update(
    complaint,
    status,
    investigation_summary,
    corrective_action,
    closure_date,
    today=None,
):
    """Return a list of error messages; empty when the update is allowed."""
    today = today or date.today()
    errors = []
    received = complaint.get("date_received")

    if closure_date and closure_date > today:
        errors.append("The closure date cannot be in the future.")
    if closure_date and received and closure_date < received:
        errors.append(
            "The closure date cannot be before the complaint was received "
            f"({received:%d %b %Y})."
        )

    if status == STATUS_COMPLETE:
        if not (investigation_summary or "").strip():
            errors.append(
                "Record the investigation summary before marking the "
                "investigation complete."
            )
        elif has_draft_placeholders(investigation_summary):
            errors.append(
                "Replace the bracketed parts of the drafted investigation "
                "note with the actual findings and conclusion."
            )
        if not closure_date:
            errors.append(
                "Enter the closure date before marking the investigation "
                "complete."
            )
        if complaint.get("severity") == "Serious" and not (
            corrective_action or ""
        ).strip():
            errors.append(
                "Serious complaints need a corrective action / CAPA entry "
                "before closure. If no action is needed, record why."
            )
        if complaint.get("complaint_category") == ADVERSE_EVENT_CATEGORY and not complaint.get("linked_case_id"):
            errors.append(
                "This complaint reports an adverse event. Create the safety "
                "case before closing the investigation."
            )
    elif closure_date:
        errors.append(
            "Remove the closure date, or mark the investigation complete."
        )

    return errors


def describe_complaint_changes(before, after):
    """Plain-language audit text for what an update changed."""
    changes = []
    if before.get("status") != after.get("status"):
        changes.append(
            f"Status changed from {before.get('status')} to "
            f"{after.get('status')}."
        )
    if before.get("closure_date") != after.get("closure_date"):
        new_date = after.get("closure_date")
        changes.append(
            "Closure date "
            + (f"set to {new_date:%d %b %Y}." if new_date else "removed.")
        )
    for field, label in (
        ("investigation_summary", "Investigation summary"),
        ("corrective_action", "Corrective action / CAPA"),
    ):
        old = (before.get(field) or "").strip()
        new = (after.get(field) or "").strip()
        if old != new:
            if not old:
                changes.append(f"{label} added.")
            elif not new:
                changes.append(f'{label} removed (was: "{_clip(old)}").')
            else:
                changes.append(f'{label} edited (previous: "{_clip(old)}").')
    return " ".join(changes) or "Saved with no changes."


def _clip(text, limit=500):
    return text if len(text) <= limit else text[: limit - 1] + "…"
