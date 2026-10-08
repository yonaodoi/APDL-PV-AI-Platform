"""Documents attached to a safety case, and how they feed case processing.

* RSI documents go into the reference library for the case's suspect
  product, become the current reference, have their reaction terms
  extracted, and trigger an automatic listedness re-check.
* Source reports, follow-up responses and clinical records are read by the
  AI in the background. The result is a list of *suggested* field updates;
  nothing in the case changes until a reviewer applies them.
"""

import json
from datetime import date, time
from pathlib import Path
from shutil import copyfile
from threading import Thread
from uuid import uuid4

from flask import current_app

from app.db import get_db, query_all, query_one, transaction

DOCUMENT_TYPES = (
    ("rsi_innovator", "RSI – Innovator reference safety information"),
    ("rsi_smpc", "RSI – SmPC / product label"),
    ("rsi_apdl", "RSI – APDL product information"),
    ("source_report", "Source report (ADR form, CIOMS, email)"),
    ("follow_up", "Follow-up response"),
    ("clinical", "Lab results / medical records"),
    ("other", "Other"),
)
DOCUMENT_TYPE_LABELS = dict(DOCUMENT_TYPES)

RSI_DOCUMENT_TYPES = {
    "rsi_innovator": "Innovator Reference Safety Information",
    "rsi_smpc": "SmPC",
    "rsi_apdl": "APDL Product Information",
}
READABLE_TYPES = {"source_report", "follow_up", "clinical"}
READABLE_EXTENSIONS = {".pdf", ".docx", ".txt"}

STATUS_READING = "Reading document"
STATUS_READY = "Suggestions ready"
STATUS_NO_CHANGES = "No changes found"
STATUS_APPLIED = "Updates applied"
STATUS_DISMISSED = "Suggestions dismissed"
STATUS_FAILED = "Could not be read"
STATUS_RSI = "Added to reference library"


def document_type_label(value):
    return DOCUMENT_TYPE_LABELS.get(value or "other", "Other")


# --------------------------------------------------------------------------
# Field rules for suggestions
# --------------------------------------------------------------------------

CASE_FIELDS = {
    "country_id": "Country",
    "source": "Report source",
    "reporter_name": "Reporter name",
    "reporter_profession": "Reporter profession",
    "reporter_organisation": "Reporter organisation",
    "reporter_phone": "Reporter phone",
    "reporter_email": "Reporter email",
    "patient_initials": "Patient initials",
    "patient_date_of_birth": "Patient date of birth",
    "patient_age_years": "Patient age (years)",
    "patient_sex": "Patient sex",
    "patient_weight_kg": "Patient weight (kg)",
    "patient_pregnancy_status": "Pregnancy status",
    "patient_address": "Patient address",
    "patient_phone": "Patient phone",
    "medical_history": "Medical history",
    "concomitant_medicines": "Concomitant medicines",
    "event_description": "Event description",
    "treatment_given": "Treatment given",
    "event_onset_date": "Event onset date",
    "event_onset_time": "Event onset time",
    "event_end_date": "Event end date",
    "laboratory_results": "Laboratory results",
    "event_outcome": "Event outcome",
    "seriousness_criteria": "Seriousness criteria",
    "case_narrative": "Case narrative",
}
PRODUCT_FIELDS = {
    "product_name": "Suspect product",
    "generic_name": "Generic name",
    "strength": "Strength",
    "dosage_form": "Dosage form",
    "batch_number": "Batch number",
    "expiry_date": "Expiry date",
    "dose": "Dose",
    "route": "Route",
    "frequency": "Frequency",
    "indication": "Indication",
    "therapy_start_date": "Therapy start date",
    "therapy_end_date": "Therapy end date",
    "action_taken": "Action taken",
}
DATE_FIELDS = {
    "patient_date_of_birth", "event_onset_date", "event_end_date",
    "expiry_date", "therapy_start_date", "therapy_end_date",
}
# Free-text fields where new information is added, not swapped in.
APPEND_FIELDS = {
    "medical_history", "concomitant_medicines", "laboratory_results",
    "treatment_given", "case_narrative",
}
CHOICES = {
    "source": ("Healthcare professional", "Patient or consumer", "Distributor",
               "Literature", "Regulatory authority", "Other"),
    "patient_sex": ("Female", "Male", "Unknown"),
    "patient_pregnancy_status": ("Yes", "No", "Not applicable", "Unknown"),
    "action_taken": ("Drug withdrawn", "Dose increased", "Dose reduced",
                     "Dose not changed", "Unknown"),
    "event_outcome": ("Recovered/resolved", "Recovering/resolving",
                      "Not recovered/not resolved", "Recovered with sequelae",
                      "Fatal", "Unknown"),
}


def weight_in_kg(value):
    """'62 kg', '62.5kg', '62' -> '62' / '62.5'; anything else -> None."""
    import re

    text = str(value or "").strip().lower().replace(",", ".")
    match = re.fullmatch(r"(\d{1,3}(?:\.\d{1,2})?)\s*(kg|kgs|kilograms?)?", text)
    if not match:
        return None
    number = float(match.group(1))
    if not 0 < number < 500:
        return None
    return match.group(1)


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (date, time)):
        return value.isoformat()[:5] if isinstance(value, time) else value.isoformat()
    return " ".join(str(value).split())


def normalise_extracted(extracted, countries):
    """Turn raw AI output into {field: value-as-string}, dropping anything
    invalid. Dates are ISO strings; country becomes country_id."""
    values = {}
    for field in list(CASE_FIELDS) + list(PRODUCT_FIELDS):
        if field == "country_id":
            continue
        raw = extracted.get(field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if field in DATE_FIELDS:
            try:
                values[field] = date.fromisoformat(str(raw).strip()).isoformat()
            except ValueError:
                continue
        elif field == "event_onset_time":
            try:
                values[field] = time.fromisoformat(str(raw).strip()).isoformat()[:5]
            except ValueError:
                continue
        elif field == "patient_weight_kg":
            weight = weight_in_kg(raw)
            if weight:
                values[field] = weight
        elif field == "patient_age_years":
            try:
                age = int(str(raw).strip())
            except ValueError:
                continue
            if 0 <= age <= 130:
                values[field] = str(age)
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


def product_mismatch(product, values):
    """The document's product name, if it is not the case's suspect product."""
    named = (values.get("product_name") or "").strip()
    if not named or not product:
        return None
    named_key = named.casefold()
    for known in (product.get("product_name"), product.get("generic_name")):
        known_key = (known or "").strip().casefold()
        if known_key and (known_key in named_key or named_key in known_key):
            return None
    return named


def build_suggestions(
    case, product, values, document_label, received_on, countries=(),
    uncertain=(), mismatched_product=None,
):
    """Suggested updates where the document differs from the case.

    Suggestions that need a closer look carry a ``check_reason`` and are
    shown unticked: values the AI marked uncertain, and product details
    when the document names a different product.
    """
    country_names = {str(c["country_id"]): c["country_name"] for c in countries}
    uncertain = set(uncertain or ())
    suggestions = []
    for field, proposed in values.items():
        table = "case" if field in CASE_FIELDS else "product"
        record = case if table == "case" else (product or {})
        current = _text(record.get(field))
        label = CASE_FIELDS.get(field) or PRODUCT_FIELDS.get(field)

        if field in APPEND_FIELDS and current:
            if " ".join(proposed.split()).casefold() in current.casefold():
                continue
            new_value = (
                f"{record.get(field)}\n\n[From {document_label}, "
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
        if table == "product" and mismatched_product:
            check_reason = (
                f'The document names "{mismatched_product}", not this '
                "case's suspect product. Check this detail belongs to the "
                "suspect product."
            )
        elif field in uncertain or (field == "country_id" and "country" in uncertain):
            check_reason = "The AI marked this value as uncertain."

        suggestions.append(
            {
                "field": field,
                "table": table,
                "label": label,
                "kind": kind,
                "current": show(current),
                "proposed": show(proposed),
                "new_value": new_value,
                "check_reason": check_reason,
            }
        )
    order = list(CASE_FIELDS) + list(PRODUCT_FIELDS)
    suggestions.sort(key=lambda s: order.index(s["field"]))
    return suggestions


def typed_value(field, value):
    """Convert a stored suggestion back to the database type."""
    if value in (None, ""):
        return None
    if field in DATE_FIELDS:
        return date.fromisoformat(value)
    if field == "event_onset_time":
        return time.fromisoformat(value)
    if field in ("patient_age_years", "country_id"):
        return int(value)
    if field == "patient_weight_kg":
        weight = weight_in_kg(value)
        if weight is None:
            raise ValueError(f"Weight must be a number of kilograms, not {value!r}.")
        return weight
    return value


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------

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


def _case_and_product(case_id):
    case = query_one("SELECT * FROM pv.safety_cases WHERE case_id = %s", (case_id,))
    product = query_one(
        """
        SELECT * FROM pv.case_products
        WHERE case_id = %s
        ORDER BY case_product_id
        LIMIT 1
        """,
        (case_id,),
    )
    return case, product


def add_rsi_from_attachment(attachment, case_id, file_path, actor_user_id):
    """Register an RSI attachment in the reference library. Returns a note."""
    from app.rsi.routes import extract_terms_for_document, rsi_file_path
    from app.services.case_listedness import reassess_product_cases
    from app.services.rsi_extraction import extract_reference_document_text
    from app.services.rsi_versions import make_current

    case, product = _case_and_product(case_id)
    if not product or not product.get("product_name"):
        return None, (
            "Not added to the reference library: record the suspect product "
            "on this case first."
        )

    document_type = RSI_DOCUMENT_TYPES[attachment["document_type"]]
    # Supersede the current document of the same type for this product by
    # reusing its market; otherwise use the case's country.
    same_type = query_one(
        """
        SELECT market
        FROM pv.reference_safety_information
        WHERE is_current = TRUE
          AND LOWER(TRIM(product_name)) = LOWER(TRIM(%s))
          AND LOWER(TRIM(document_type)) = LOWER(TRIM(%s))
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (product["product_name"], document_type),
    )
    if same_type:
        market = same_type["market"]
    else:
        country = query_one(
            "SELECT country_name FROM pv.countries WHERE country_id = %s",
            (case.get("country_id"),),
        )
        market = country["country_name"] if country else None

    extension = Path(attachment["original_filename"]).suffix.lower()
    stored_filename = f"{uuid4().hex}{extension}"
    destination = rsi_file_path(stored_filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    copyfile(file_path, destination)

    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.reference_safety_information (
                product_name, active_substance, market, document_type,
                original_filename, stored_filename, content_type,
                file_size_bytes, uploaded_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING rsi_id
            """,
            (
                product["product_name"],
                product.get("generic_name"),
                market,
                document_type,
                attachment["original_filename"],
                stored_filename,
                attachment.get("content_type"),
                destination.stat().st_size,
                actor_user_id,
            ),
        )
        rsi_id = cursor.fetchone()["rsi_id"]
        superseded = make_current(
            cursor,
            {
                "rsi_id": rsi_id,
                "product_name": product["product_name"],
                "document_type": document_type,
                "market": market,
            },
        )
        cursor.execute(
            "UPDATE pv.record_attachments SET linked_rsi_id = %s WHERE attachment_id = %s",
            (rsi_id, attachment["attachment_id"]),
        )

    try:
        text = extract_reference_document_text(destination)
        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.reference_safety_information
                SET extracted_text = %s,
                    extraction_status = 'Extracted',
                    extracted_at = CURRENT_TIMESTAMP
                WHERE rsi_id = %s
                """,
                (text, rsi_id),
            )
    except Exception:
        current_app.logger.warning("RSI text extraction failed", exc_info=True)
        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.reference_safety_information
                SET extraction_status = 'Extraction failed'
                WHERE rsi_id = %s
                """,
                (rsi_id,),
            )

    term_count, term_error = extract_terms_for_document(
        rsi_id, stored_filename, actor_user_id, attachment["original_filename"]
    )

    from app.audit import write_audit_log

    write_audit_log(
        record_type="reference_safety_information",
        record_id=rsi_id,
        action="Reference safety information created",
        details=(
            f"Added from case attachment ({document_type}) for "
            f"{product['product_name']}; {superseded} older document(s) "
            "marked historic."
        ),
        actor_user_id=actor_user_id,
    )

    rechecked = reassess_product_cases(
        [product["product_name"], product.get("generic_name")],
        actor_user_id=actor_user_id,
    )

    note = (
        f"Added to the reference library as the current {document_type} for "
        f"{product['product_name']}"
        + (f" ({market})" if market else "")
        + ". "
        + (
            f"{term_count} reaction term(s) extracted - verify them under "
            "Reference Safety Information. "
            if not term_error
            else f"Reaction terms could not be extracted: {term_error} "
        )
        + (
            f"Listedness was re-checked on {rechecked} case(s) for this product."
            if rechecked
            else "Listedness results for this product were already up to date."
        )
    )
    return rsi_id, note


def read_document_for_suggestions(app, attachment_id, case_id, file_path, document_label):
    """Background job: read the document and store suggested updates."""
    with app.app_context():
        try:
            from app.services.case_document_extraction import (
                extract_case_fields,
                extract_document_text,
            )

            text = extract_document_text(Path(file_path))
            extracted = extract_case_fields(text)
            countries = query_all("SELECT country_id, country_name FROM pv.countries")
            values = normalise_extracted(extracted, countries)
            case, product = _case_and_product(case_id)
            suggestions = build_suggestions(
                case, product, values, document_label, date.today(), countries,
                uncertain=extracted.get("uncertain_fields") or (),
                mismatched_product=product_mismatch(product, values),
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
                    "from the case.",
                    [],
                )
        except Exception as error:
            current_app.logger.exception(
                "Reading case attachment %s failed", attachment_id
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
                    f"({error}). It is still attached to the case.",
                )
            except Exception:
                current_app.logger.exception("Could not record read failure")


def process_case_attachment(attachment, case_id, file_path, actor_user_id):
    """Start the right processing for a new case attachment.

    Returns (message, category) for the user, or None.
    """
    document_type = attachment["document_type"]
    extension = Path(attachment["original_filename"]).suffix.lower()

    if document_type in RSI_DOCUMENT_TYPES:
        if extension not in (".pdf", ".docx", ".doc"):
            _set_status(
                attachment["attachment_id"],
                STATUS_FAILED,
                "Reference documents must be PDF or Word files.",
            )
            return "Reference documents must be PDF or Word files.", "error"
        try:
            rsi_id, note = add_rsi_from_attachment(
                attachment, case_id, file_path, actor_user_id
            )
        except Exception:
            current_app.logger.exception("Adding RSI from attachment failed")
            try:
                get_db().rollback()
            except Exception:
                pass
            note, rsi_id = (
                "The file was attached, but it could not be added to the "
                "reference library. Add it under Reference Safety Information.",
                None,
            )
        _set_status(
            attachment["attachment_id"],
            STATUS_RSI if rsi_id else STATUS_FAILED,
            note,
        )
        return note, ("success" if rsi_id else "warning")

    if document_type in READABLE_TYPES:
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
            target=read_document_for_suggestions,
            args=(
                current_app._get_current_object(),
                attachment["attachment_id"],
                case_id,
                str(file_path),
                document_type_label(document_type).lower(),
            ),
            daemon=True,
        ).start()
        return (
            "The document is being read. Suggested updates will appear in "
            "the Attachments section; nothing changes until you apply them.",
            "info",
        )
    return None


def apply_suggestions(case_id, attachment, selected_fields, actor_user_id):
    """Apply chosen suggestions. Returns the list of applied labels."""
    suggestions = [
        s for s in (attachment.get("suggested_updates") or [])
        if s["field"] in selected_fields
        and (
            (s["table"] == "case" and s["field"] in CASE_FIELDS)
            or (s["table"] == "product" and s["field"] in PRODUCT_FIELDS)
        )
    ]
    case_updates = {
        s["field"]: typed_value(s["field"], s["new_value"])
        for s in suggestions
        if s["table"] == "case" and s["field"] in CASE_FIELDS
    }
    product_updates = {
        s["field"]: typed_value(s["field"], s["new_value"])
        for s in suggestions
        if s["table"] == "product" and s["field"] in PRODUCT_FIELDS
    }
    if not case_updates and not product_updates:
        return []

    details = "; ".join(
        f"{s['label']}: "
        + (f"\"{s['current']}\" → " if s["current"] and s["kind"] == "change" else "")
        + (f"added \"{s['proposed']}\"" if s["kind"] == "add" else f"\"{s['proposed']}\"")
        for s in suggestions
    )

    with transaction() as cursor:
        if case_updates:
            columns = ", ".join(f"{field} = %s" for field in case_updates)
            cursor.execute(
                f"UPDATE pv.safety_cases SET {columns}, updated_at = NOW() WHERE case_id = %s",
                (*case_updates.values(), case_id),
            )
        if product_updates:
            columns = ", ".join(f"{field} = %s" for field in product_updates)
            cursor.execute(
                f"""
                UPDATE pv.case_products SET {columns}
                WHERE case_product_id = (
                    SELECT case_product_id FROM pv.case_products
                    WHERE case_id = %s ORDER BY case_product_id LIMIT 1
                )
                """,
                (*product_updates.values(), case_id),
            )
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (case_id, action, details, performed_by)
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                "Case updated from attached document",
                f"From {attachment['original_filename']}: {details}"[:4000],
                actor_user_id,
            ),
        )
        applied_fields = {s["field"] for s in suggestions}
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
            note = f"{len(suggestions)} update(s) applied to the case."
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
    return [s["label"] for s in suggestions]
