"""Sign-off settings an administrator can change: stage titles, who reviews
and approves, extra notification addresses (reviewers, approvers and PV officers) and
email timing."""

import json
import re

from app.db import get_db, query_one, transaction

SETTING_KEY = "case_sign_off"
DEFAULTS = {
    "review_title": "QPPV review",
    "approval_title": "Case approval",
    "reviewer_roles": ["QPPV", "Deputy QPPV"],
    "approver_roles": ["Group Head RA & Quality"],
    "review_extra_emails": [],
    "approval_extra_emails": [],
    "officer_roles": ["PV Officer"],
    "officer_extra_emails": [],
    "notify_officer": True,
    "digest_hour": 8,
    "urgent_days": 3,
}


EMAIL_PATTERN = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[a-z]{2,}$", re.IGNORECASE)


def valid_email(value):
    return bool(EMAIL_PATTERN.match((value or "").strip()))


def clean_emails(text):
    """Split a comma / semicolon / newline separated list into addresses."""
    if isinstance(text, list):
        items = text
    else:
        items = (text or "").replace(";", ",").replace("\n", ",").split(",")
    out = []
    for item in items:
        item = item.strip().strip(",;").lower()
        if item and valid_email(item) and item not in out:
            out.append(item)
    return out


def normalise(values):
    """Fill gaps with defaults and keep values sensible."""
    result = dict(DEFAULTS)
    for key in DEFAULTS:
        if key in (values or {}) and values[key] not in (None, ""):
            result[key] = values[key]
    result["review_title"] = str(result["review_title"]).strip()[:60] or DEFAULTS["review_title"]
    result["approval_title"] = str(result["approval_title"]).strip()[:60] or DEFAULTS["approval_title"]
    for key in ("reviewer_roles", "approver_roles", "officer_roles"):
        roles = [r for r in result[key] if isinstance(r, str) and r.strip()]
        result[key] = roles or list(DEFAULTS[key])
    for key in ("review_extra_emails", "approval_extra_emails", "officer_extra_emails"):
        result[key] = clean_emails(result[key])
    flag = result["notify_officer"]
    result["notify_officer"] = flag if isinstance(flag, bool) else str(flag).lower() in ("1", "true", "on", "yes")
    try:
        result["digest_hour"] = min(max(int(result["digest_hour"]), 0), 23)
    except (TypeError, ValueError):
        result["digest_hour"] = DEFAULTS["digest_hour"]
    try:
        result["urgent_days"] = min(max(int(result["urgent_days"]), 0), 30)
    except (TypeError, ValueError):
        result["urgent_days"] = DEFAULTS["urgent_days"]
    return result


def load_settings():
    """Current settings; the defaults if none are saved or the table is missing."""
    try:
        row = query_one(
            "SELECT value FROM pv.app_settings WHERE setting_key = %s", (SETTING_KEY,)
        )
    except Exception:
        try:
            get_db().rollback()
        except Exception:
            pass
        return dict(DEFAULTS)
    value = row["value"] if row else {}
    if isinstance(value, str):
        value = json.loads(value)
    return normalise(value)


def save_settings(values, user_id):
    settings = normalise(values)
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.app_settings (setting_key, value, updated_by, updated_at)
            VALUES (%s, %s::jsonb, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (setting_key) DO UPDATE
            SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            (SETTING_KEY, json.dumps(settings), user_id),
        )
    return settings
