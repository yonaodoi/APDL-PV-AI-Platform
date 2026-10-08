"""Documents attached to a product complaint, and how they feed the record.

Complaint reports and reporter follow-ups are read by the AI in the
background. The result is a list of *suggested* field updates; nothing in
the complaint changes until a reviewer applies them.
"""

import json
from datetime import date
from pathlib import Path
from threading import Thread

from flask import current_app

from app.db import get_db, query_all, query_one, transaction
from app.services.case_documents import (
    READABLE_EXTENSIONS,
    STATUS_APPLIED,
    STATUS_DISMISSED,
    STATUS_FAILED,
    STATUS_NO_CHANGES,
    STATUS_READING,
    STATUS_READY,
)

COMPLAINT_DOCUMENT_TYPES = (
    ("complaint_report", "Complaint report (form, email, letter)"),
    ("complaint_follow_up", "Reporter follow-up"),
    ("investigation_report", "Investigation / QA / lab report"),
    ("other", "Other"),
)
COMPLAINT_DOCUMENT_TYPE_LABELS = dict(COMPLAINT_DOCUMENT_TYPES)
READABLE_COMPLAINT_TYPES = {"complaint_report", "complaint_follow_up"}

COMPLAINT_FIELDS = {
    "date_received": "Date received",
    "country_id": "Country",
    "reporter_name": "Reporter",
    "reporter_contact": "Reporter contact",
    "product_name": "Product",
    "batch_number": "Batch number",
    "manufacturing_date": "Manufacturing date",
    "expiry_date": "Expiry date",
    "complaint_category": "Category",
    "severity": "Severity",
    "complaint_description": "Description",
}
DATE_FIELDS = {"date_received", "manufacturing_date", "expiry_date"}
APPEND_FIELDS = {"complaint_description"}
CHOICES = {
    "complaint_category": (
        "Product quality", "Packaging", "Labelling", "Adverse event", "Other",
    ),
    "severity": ("Non-serious", "Serious"),
}


def complaint_document_type_label(value):
    return COMPLAINT_DOCUMENT_TYPE_LABELS.get(value or "other", "Other")


def _text(value):
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return " ".join(str(value).split())


def normalise_complaint_extract(extracted, countries):
    """Raw AI output -> {field: value-as-string}, dropping anything invalid."""
    values = {}
    for field in COMPLAINT_FIELDS:
        if field == "country_id":
            continue
        raw = extracted.get(field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if field in DATE_FIELDS:
            try:
                parsed = date.fromisoformat(str(raw).strip())
            except ValueError:
                continue
            if parsed > date.today() and field != "expiry_date":
                continue
            values[field] = parsed.isoformat()
        elif field in CHOICES:
            match = next(
                (c for c in CHOICES[field] if c.casefold() == str(raw).strip().casefold()),
                None,
            )
            if match:
                values[field] = match
        else:
            values[field] = str(raw).strip()

    country = extracted.get("country")
    if country:
        match = next(
            (
                c for c in countries
                if c["country_name"].strip().casefold() == str(country).strip().casefold()
            ),
            None,
        )
        if match:
            values["country_id"] = str(match["country_id"])
    return values


def build_complaint_suggestions(complaint, values, document_label, received_on, countries=(), uncertain=()):
    """Suggested updates where the document differs from the complaint.

    Changes to existing values and values the AI marked uncertain carry a
    reason and start unticked on the review page.
    """
    country_names = {str(c["country_id"]): c["country_name"] for c in countries}
    uncertain = set(uncertain or ())
    suggestions = []
    for field, proposed in values.items():
        current = _text(complaint.get(field))
        if field in APPEND_FIELDS and current:
            if " ".join(proposed.split()).casefold() in current.casefold():
                continue
            new_value = (
                f"{complaint.get(field)}\n\n[From {document_label}, "
                f"{received_on:%d %b %Y}] {proposed}"
            )
            kind = "add"
        else:
            if _text(proposed).casefold() == current.casefold():
                continue
            new_value = proposed
            kind = "fill" if not current else "change"

        def show(value):
            if field == "country_id":
                return country_names.get(str(value), value) if value else ""
            return value

        check_reason = None
        if field in uncertain or (field == "country_id" and "country" in uncertain):
            check_reason = "The AI marked this value as uncertain."
        elif field == "product_name" and current:
            check_reason = "The document names a different product. Check it is the same complaint."
        elif field == "complaint_category" and proposed == "Adverse event":
            check_reason = "An adverse event needs a safety case. Check the document reports one."

        suggestions.append({
            "field": field,
            "table": "complaint",
            "label": COMPLAINT_FIELDS[field],
            "kind": kind,
            "current": show(current),
            "proposed": show(proposed),
            "new_value": new_value,
            "check_reason": check_reason,
        })
    order = list(COMPLAINT_FIELDS)
    suggestions.sort(key=lambda s: order.index(s["field"]))
    return suggestions


def typed_complaint_value(field, value):
    if value in (None, ""):
        return None
    if field in DATE_FIELDS:
        return date.fromisoformat(value)
    if field == "country_id":
        return int(value)
    return value


def _set_status(attachment_id, status, note=None, suggestions=None):
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.record_attachments
            SET processing_status = %s,
                processing_note = %s,
                suggested_updates = COALESCE(%s::jsonb, suggested_updates)
            WHERE attachment_id = %s
            """,
            (
                status,
                note,
                json.dumps(suggestions) if suggestions is not None else None,
                attachment_id,
            ),
        )


def read_complaint_document(app, attachment_id, complaint_id, file_path, document_label):
    """Background job: read the document and store suggested updates."""
    with app.app_context():
        try:
            from app.services.complaint_document_extraction import (
                extract_complaint_fields,
                extract_document_text,
            )

            text = extract_document_text(Path(file_path))
            extracted = extract_complaint_fields(text)
            countries = query_all("SELECT country_id, country_name FROM pv.countries")
            values = normalise_complaint_extract(extracted, countries)
            complaint = query_one(
                "SELECT * FROM pv.product_complaints WHERE complaint_id = %s",
                (complaint_id,),
            )
            suggestions = build_complaint_suggestions(
                complaint or {}, values, document_label, date.today(), countries,
                uncertain=extracted.get("uncertain_fields") or (),
            )
            if suggestions:
                _set_status(
                    attachment_id,
                    STATUS_READY,
                    f"{len(suggestions)} suggested update(s) found. Review "
                    "them before they are applied.",
                    suggestions,
                )
            else:
                _set_status(
                    attachment_id,
                    STATUS_NO_CHANGES,
                    "The document was read; it adds nothing that differs "
                    "from the complaint.",
                    [],
                )
        except Exception as error:
            current_app.logger.exception(
                "Reading complaint attachment %s failed", attachment_id
            )
            try:
                get_db().rollback()
            except Exception:
                pass
            try:
                _set_status(
                    attachment_id,
                    STATUS_FAILED,
                    "The document could not be read automatically "
                    f"({error}). It is still attached to the complaint.",
                )
            except Exception:
                current_app.logger.exception("Could not record read failure")


def process_complaint_attachment(attachment, complaint_id, file_path):
    """Start reading a new complaint attachment when its type is readable.

    Returns (message, category) for the user, or None.
    """
    document_type = attachment["document_type"]
    if document_type not in READABLE_COMPLAINT_TYPES:
        return None
    extension = Path(attachment["original_filename"]).suffix.lower()
    if extension not in READABLE_EXTENSIONS:
        _set_status(
            attachment["attachment_id"],
            STATUS_FAILED,
            "Only PDF, Word (.docx) and text files can be read "
            "automatically. The file is attached.",
        )
        return None
    _set_status(
        attachment["attachment_id"],
        STATUS_READING,
        "The AI is reading the document. This can take a minute or two.",
    )
    Thread(
        target=read_complaint_document,
        args=(
            current_app._get_current_object(),
            attachment["attachment_id"],
            complaint_id,
            str(file_path),
            complaint_document_type_label(document_type).lower(),
        ),
        daemon=True,
    ).start()
    return (
        "The document is being read. Suggested updates will appear under "
        "Attachments; nothing changes until you apply them.",
        "info",
    )


def apply_complaint_suggestions(complaint_id, attachment, selected_fields):
    """Apply chosen suggestions. Returns the applied suggestions."""
    suggestions = [
        s for s in (attachment.get("suggested_updates") or [])
        if s["field"] in selected_fields and s["field"] in COMPLAINT_FIELDS
    ]
    updates = {
        s["field"]: typed_complaint_value(s["field"], s["new_value"])
        for s in suggestions
    }
    if not updates:
        return []

    with transaction() as cursor:
        columns = ", ".join(f"{field} = %s" for field in updates)
        cursor.execute(
            f"UPDATE pv.product_complaints SET {columns}, updated_at = NOW() "
            "WHERE complaint_id = %s",
            (*updates.values(), complaint_id),
        )
        applied_fields = set(updates)
        remaining = [
            s for s in (attachment.get("suggested_updates") or [])
            if s["field"] not in applied_fields
        ]
        if remaining:
            status = STATUS_READY
            note = (
                f"{len(suggestions)} update(s) applied; {len(remaining)} "
                "suggestion(s) still to review or dismiss."
            )
        else:
            status = STATUS_APPLIED
            note = f"{len(suggestions)} update(s) applied to the complaint."
        cursor.execute(
            """
            UPDATE pv.record_attachments
            SET processing_status = %s,
                processing_note = %s,
                suggested_updates = %s::jsonb
            WHERE attachment_id = %s
            """,
            (status, note, json.dumps(remaining), attachment["attachment_id"]),
        )
    return suggestions


def describe_applied(suggestions):
    return "; ".join(
        f"{s['label']}: "
        + (f"\"{s['current']}\" → " if s["current"] and s["kind"] == "change" else "")
        + (f"added \"{s['proposed']}\"" if s["kind"] == "add" else f"\"{s['proposed']}\"")
        for s in suggestions
    )
