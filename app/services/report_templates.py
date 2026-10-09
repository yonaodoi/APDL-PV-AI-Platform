"""Uploaded report templates: storing, checking, activating and using them.

A template only becomes active after it has been test-filled with sample
data without problems. When a report is printed, the active template for
that report type is used; if it cannot be used for any reason, the built-in
layout is used instead and the user is told, so a report is never lost.
"""

import json
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import current_app

from app.db import get_db, query_all, query_one, transaction
from app.services.docx_fill import TemplateError, fill_template, template_markers
from app.services.report_fields import REPORT_TYPES, sample_context, unknown_markers

MAX_TEMPLATE_BYTES = 15 * 1024 * 1024


def template_dir():
    folder = Path(current_app.config["UPLOAD_ROOT"]) / "report_templates"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def read_template(row, original=False):
    name = row["source_filename"] if original and row.get("source_filename") else row["stored_filename"]
    return (template_dir() / name).read_bytes()


def _rollback():
    try:
        get_db().rollback()
    except Exception:
        pass


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def check_template(report_type, template_bytes):
    """Test the template. Returns dict(passed, problems, notes, markers)."""
    problems, notes = [], []
    try:
        values, blocks, structure = template_markers(template_bytes)
    except TemplateError as error:
        return {"passed": False, "problems": [str(error)], "notes": [], "markers": []}
    problems.extend(structure)
    if not values and not blocks:
        problems.append(
            "The template has no markers yet. Use “Map it for me (AI)” or add "
            "markers such as {{case.case_number}} – see the marker guide."
        )
    unknown = unknown_markers(report_type, values, blocks)
    for name in unknown:
        problems.append(f"“{{{{{name}}}}}” is not a field of this report. Check the spelling against the marker guide.")
    if not problems:
        try:
            fill_template(template_bytes, sample_context(report_type))
        except TemplateError as error:
            problems.append(str(error))
        except Exception as error:  # pragma: no cover - unexpected document structure
            problems.append(f"The template could not be test-filled ({error}).")
    used = len(values)
    if not problems:
        notes.append(f"Test-filled with sample data: {used} field(s) and {len(blocks)} repeating or optional section(s).")
    return {"passed": not problems, "problems": problems, "notes": notes, "markers": sorted(values | blocks)}


# --------------------------------------------------------------------------
# Storing
# --------------------------------------------------------------------------

def store_file(data, suffix=".docx"):
    name = f"{uuid4().hex}{suffix}"
    (template_dir() / name).write_bytes(data)
    return name


def save_template(report_type, name, version, filename, data, user_id, source="markers",
                  mapping=None, source_filename=None):
    """Store a new draft and its check result. Returns the template id."""
    if report_type not in REPORT_TYPES:
        raise ValueError("Unknown report type.")
    applied = source == "markers" or mapping is None
    result = check_template(report_type, data) if applied else {
        "passed": False, "problems": [], "notes": ["Waiting for you to confirm the AI's field mapping."], "markers": []}
    stored = store_file(data)
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.report_templates (
                report_type, name, version, original_filename, stored_filename,
                source_filename, source, check_passed, check_problems, check_notes,
                markers, mapping, mapping_applied, uploaded_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s)
            RETURNING template_id
            """,
            (
                report_type, name[:200], (version or None), filename[:255], stored,
                source_filename, source, result["passed"], json.dumps(result["problems"]),
                json.dumps(result["notes"]), json.dumps(result["markers"]),
                json.dumps(mapping) if mapping is not None else None, applied, user_id,
            ),
        )
        return cursor.fetchone()["template_id"]


def apply_mapping(template_id, placements, user_id):
    """Write the confirmed AI mapping into the template and check it."""
    from app.services.docx_fill import insert_markers

    row = get_template(template_id)
    if not row:
        raise ValueError("Template not found.")
    original = read_template(row, original=True)
    marked = insert_markers(original, placements)
    result = check_template(row["report_type"], marked)
    stored = store_file(marked)
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.report_templates
            SET stored_filename = %s, mapping = %s::jsonb, mapping_applied = TRUE,
                check_passed = %s, check_problems = %s::jsonb, check_notes = %s::jsonb,
                markers = %s::jsonb
            WHERE template_id = %s
            """,
            (stored, json.dumps(placements), result["passed"], json.dumps(result["problems"]),
             json.dumps(result["notes"]), json.dumps(result["markers"]), template_id),
        )
    return result


def get_template(template_id):
    return query_one(
        """
        SELECT t.*, u.full_name AS uploaded_by_name, a.full_name AS activated_by_name
        FROM pv.report_templates AS t
        LEFT JOIN pv.users AS u ON u.user_id = t.uploaded_by
        LEFT JOIN pv.users AS a ON a.user_id = t.activated_by
        WHERE t.template_id = %s
        """,
        (template_id,),
    )


def list_templates():
    try:
        return query_all(
            """
            SELECT t.*, u.full_name AS uploaded_by_name
            FROM pv.report_templates AS t
            LEFT JOIN pv.users AS u ON u.user_id = t.uploaded_by
            ORDER BY t.report_type, (t.status = 'Active') DESC, t.created_at DESC
            """
        )
    except Exception:
        _rollback()
        return []


def activate(template_id, user_id):
    row = get_template(template_id)
    if not row:
        raise ValueError("Template not found.")
    if not row["check_passed"]:
        raise ValueError("This template has problems. Fix them and upload it again before making it active.")
    with transaction() as cursor:
        cursor.execute(
            "UPDATE pv.report_templates SET status = 'Historic' WHERE report_type = %s AND status = 'Active'",
            (row["report_type"],),
        )
        cursor.execute(
            """
            UPDATE pv.report_templates
            SET status = 'Active', activated_by = %s, activated_at = CURRENT_TIMESTAMP
            WHERE template_id = %s
            """,
            (user_id, template_id),
        )
    return row


def retire(template_id):
    with transaction() as cursor:
        cursor.execute(
            "UPDATE pv.report_templates SET status = 'Historic' WHERE template_id = %s RETURNING report_type",
            (template_id,),
        )
        return cursor.fetchone()


def delete_draft(template_id):
    row = get_template(template_id)
    if not row or row["status"] != "Draft":
        return False
    with transaction() as cursor:
        cursor.execute("DELETE FROM pv.report_templates WHERE template_id = %s", (template_id,))
    for name in {row["stored_filename"], row.get("source_filename")} - {None}:
        try:
            (template_dir() / name).unlink()
        except OSError:
            pass
    return True


# --------------------------------------------------------------------------
# Printing with the active template
# --------------------------------------------------------------------------

def active_template(report_type):
    try:
        return query_one(
            "SELECT * FROM pv.report_templates WHERE report_type = %s AND status = 'Active'",
            (report_type,),
        )
    except Exception:
        _rollback()
        return None


def render_with_active(report_type, build_context):
    """(BytesIO or None, notice or None).

    None output means: use the built-in layout. ``notice`` explains when an
    active template could not be used.
    """
    row = active_template(report_type)
    if not row:
        return None, None
    label = REPORT_TYPES.get(report_type, {}).get("label", "report")
    try:
        context = build_context()
        if context is None:
            return None, None
        data = fill_template(read_template(row), context)
        return BytesIO(data), None
    except Exception as error:
        current_app.logger.exception("Report template %s could not be used", row.get("template_id"))
        _rollback()
        return None, (
            f"The uploaded {label} template “{row.get('name')}” could not be used "
            f"({error}). The standard layout was used instead."
        )
