"""Complaint investigation: what is still needed before closure, and a
drafted investigation note built from what the record already holds."""

from datetime import date

ADVERSE_EVENT_CATEGORY = "Adverse event"
STATUS_COMPLETE = "Investigation complete"


DRAFT_PLACEHOLDERS = (
    "[record what the investigation found]",
    "[complaint confirmed / not confirmed]",
)


def _filled(value):
    return bool((value or "").strip())


def has_draft_placeholders(text):
    """True when a drafted note still contains its fill-in brackets."""
    return any(marker in (text or "") for marker in DRAFT_PLACEHOLDERS)


def closure_checks(complaint, date_warnings=(), same_batch=()):
    """Checklist for closing the investigation.

    Each item: label, ok (True/False), required (blocks closure when not ok)
    and a short message. Items that do not apply to this complaint are left
    out.
    """
    checks = []

    summary = complaint.get("investigation_summary")
    if not _filled(summary):
        summary_message = "Record the findings and root cause."
    elif has_draft_placeholders(summary):
        summary_message = "Replace the bracketed parts of the drafted note."
    else:
        summary_message = "Recorded."
    checks.append({
        "code": "summary",
        "label": "Investigation summary",
        "ok": summary_message == "Recorded.",
        "required": True,
        "message": summary_message,
    })

    serious = complaint.get("severity") == "Serious"
    capa = _filled(complaint.get("corrective_action"))
    checks.append({
        "code": "capa",
        "label": "Corrective action / CAPA",
        "ok": capa or not serious,
        "required": serious,
        "message": (
            "Recorded."
            if capa
            else (
                "Required for a serious complaint. If no action is needed, record why."
                if serious
                else "Optional for a non-serious complaint."
            )
        ),
    })

    if complaint.get("complaint_category") == ADVERSE_EVENT_CATEGORY:
        linked = complaint.get("linked_case_id")
        checks.append({
            "code": "safety_case",
            "label": "Safety case",
            "ok": bool(linked),
            "required": True,
            "message": (
                f"Linked to {complaint.get('linked_case_number') or 'a safety case'}."
                if linked
                else "This complaint reports an adverse event. Create the safety case first."
            ),
        })

    if date_warnings:
        checks.append({
            "code": "dates",
            "label": "Date check",
            "ok": False,
            "required": False,
            "message": " ".join(date_warnings),
        })

    open_same_batch = [
        other for other in same_batch or []
        if other.get("status") != STATUS_COMPLETE
    ]
    if same_batch:
        checks.append({
            "code": "batch",
            "label": "Same batch",
            "ok": not open_same_batch,
            "required": False,
            "message": (
                f"{len(same_batch)} other complaint(s) for this batch"
                + (f", {len(open_same_batch)} still open" if open_same_batch else ", all closed")
                + ". Consider them in the root cause."
            ),
        })
    return checks


def closure_blockers(checks):
    """Messages for required items that are not done (empty when ready)."""
    return [
        f"{check['label']}: {check['message']}"
        for check in checks
        if check["required"] and not check["ok"]
    ]


def _clip(text, limit=220):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def draft_investigation_note(complaint, same_batch=(), date_warnings=(), attachment_count=0, today=None):
    """A starting point for the investigation summary.

    It states only facts already in the record, and leaves clearly marked
    gaps for the findings, which only the investigator can supply.
    """
    today = today or date.today()
    received = complaint.get("date_received")
    parts = [f"Investigation note drafted {today:%d %b %Y}."]

    product = complaint.get("product_name") or "the product"
    batch = complaint.get("batch_number")
    parts.append(
        f"Complaint {complaint.get('complaint_number') or ''}".strip()
        + (f" received {received:%d %b %Y}" if received else "")
        + f": {complaint.get('complaint_category') or 'complaint'}"
        + f" ({complaint.get('severity') or 'severity not recorded'})"
        + f" for {product}"
        + (f", batch {batch}." if batch else ", batch not recorded.")
    )
    if _filled(complaint.get("complaint_description")):
        parts.append(f"Reported: {_clip(complaint.get('complaint_description'))}")

    if same_batch:
        numbers = ", ".join(o.get("complaint_number", "") for o in same_batch[:5])
        more = f" and {len(same_batch) - 5} more" if len(same_batch) > 5 else ""
        parts.append(
            f"Batch history: {len(same_batch)} other complaint(s) for this batch "
            f"({numbers}{more}); reviewed for a common defect."
        )
    elif batch:
        parts.append("Batch history: no other complaints recorded for this batch.")

    for warning in date_warnings or []:
        parts.append(f"Date check: {warning}")

    if complaint.get("complaint_category") == ADVERSE_EVENT_CATEGORY:
        if complaint.get("linked_case_id"):
            parts.append(
                f"Adverse event processed as safety case {complaint.get('linked_case_number')}."
            )
        else:
            parts.append("Adverse event: safety case not yet created.")

    if attachment_count:
        parts.append(f"Supporting documents: {attachment_count} attached.")

    parts.append("Findings and root cause: [record what the investigation found].")
    parts.append("Conclusion: [complaint confirmed / not confirmed].")
    return "\n".join(parts)
