"""Licences and integrations: MedDRA, WHODrug, AI providers and other APIs.

Recording a licence number does not switch anything on. Each integration is
used only when it is enabled and its actual mechanism works: dictionary files
imported for MedDRA/WHODrug, or a reachable API with valid credentials. The
connection test shows exactly what was checked.

Credentials are encrypted (see secrets_store) and never shown, logged,
exported or written to the audit trail.
"""

import json
import time
from datetime import date, datetime, timezone
from decimal import Decimal

from flask import current_app, has_app_context

from app.db import query_all, query_one, transaction
from app.services import secrets_store

KINDS = ["MedDRA", "WHODrug", "AI provider", "Other API"]
AI_PROVIDERS = [
    ("anthropic", "Anthropic Claude API"),
    ("ollama", "Local model (Ollama, on this computer or network)"),
]
DEFAULT_AI_SETTINGS = {
    "timeout_seconds": 180,
    "max_retries": 2,
    "monthly_limit": None,            # spending limit in the currency below
    "currency": "USD",
    "price_input_per_million": None,  # for estimating spend
    "price_output_per_million": None,
    "use_for_documents": False,       # documents contain identifiers
}
FIELDS = [
    "name", "provider", "licence_reference", "registration_date", "expiry_date",
    "organisation", "permitted_use", "dictionary_version", "api_endpoint", "model",
]


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def list_integrations():
    return query_all(
        """
        SELECT i.*, u.full_name AS updated_by_name
        FROM pv.integrations AS i
        LEFT JOIN pv.users AS u ON u.user_id = i.updated_by
        ORDER BY CASE i.kind WHEN 'MedDRA' THEN 1 WHEN 'WHODrug' THEN 2 WHEN 'AI provider' THEN 3 ELSE 4 END, i.name
        """
    )


def get_integration(integration_id):
    row = query_one("SELECT * FROM pv.integrations WHERE integration_id = %s", (integration_id,))
    if row and isinstance(row.get("settings"), str):
        row["settings"] = json.loads(row["settings"])
    return row


def licence_state(row, today=None):
    """'Expired', 'Expires soon' (within 60 days), 'Valid' or None."""
    today = today or date.today()
    expiry = row.get("expiry_date")
    if not expiry:
        return None
    if isinstance(expiry, str):
        expiry = date.fromisoformat(expiry[:10])
    if expiry < today:
        return "Expired"
    if (expiry - today).days <= 60:
        return "Expires soon"
    return "Valid"


def public_view(row):
    """Everything about an integration except its credential."""
    out = {k: v for k, v in dict(row).items() if k != "credential_encrypted"}
    out["has_credential"] = bool(row.get("credential_encrypted"))
    out["licence_state"] = licence_state(row)
    return out


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------

def save_integration(kind, values, settings, credential, clear_credential, user_id, integration_id=None):
    """Create or update. ``credential`` is the new secret (blank keeps the old one)."""
    if kind not in KINDS:
        raise ValueError("Unknown integration type.")
    name = (values.get("name") or "").strip()
    if not name:
        raise ValueError("Give the integration a name.")
    for key in ("registration_date", "expiry_date"):
        if values.get(key):
            try:
                values[key] = date.fromisoformat(values[key])
            except ValueError:
                raise ValueError(f"{key.replace('_', ' ').capitalize()} is not a date.")
        else:
            values[key] = None
    if values.get("registration_date") and values.get("expiry_date") and values["expiry_date"] < values["registration_date"]:
        raise ValueError("The expiry date is before the registration date.")
    endpoint = (values.get("api_endpoint") or "").strip()
    if endpoint and not endpoint.lower().startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise ValueError("API endpoints must use https:// (plain http is only allowed for this computer).")
    clean = {k: ((values.get(k) or None) if not isinstance(values.get(k), str) else (values.get(k).strip() or None)) for k in FIELDS}
    clean["name"] = name
    if kind == "AI provider":
        settings = {**DEFAULT_AI_SETTINGS, **(settings or {})}
    changes = []
    with transaction() as cur:
        if integration_id:
            cur.execute("SELECT * FROM pv.integrations WHERE integration_id = %s FOR UPDATE", (integration_id,))
            before = cur.fetchone()
            if not before:
                raise ValueError("This integration no longer exists.")
            before_settings = before["settings"] if isinstance(before["settings"], dict) else json.loads(before["settings"] or "{}")
            changes = [f"{k.replace('_', ' ')}: {_show(before.get(k))} → {_show(clean[k])}" for k in FIELDS if _show(before.get(k)) != _show(clean[k])]
            changes += [f"{k.replace('_', ' ')}: {_show(before_settings.get(k))} → {_show(v)}" for k, v in (settings or {}).items() if _show(before_settings.get(k)) != _show(v)]
            assignments = ", ".join(f"{k} = %s" for k in FIELDS)
            params = [clean[k] for k in FIELDS]
            extra = ", settings = %s::jsonb"
            params.append(json.dumps(settings or {}, default=str))
            if credential:
                extra += ", credential_encrypted = %s, credential_hint = %s, status = 'Not tested'"
                params += [secrets_store.encrypt(credential), secrets_store.hint(credential)]
                changes.append("credential replaced")
            elif clear_credential:
                extra += ", credential_encrypted = NULL, credential_hint = NULL, status = 'Not tested'"
                changes.append("credential removed")
            cur.execute(
                f"UPDATE pv.integrations SET {assignments}{extra}, updated_by = %s, updated_at = NOW() WHERE integration_id = %s",
                params + [user_id, integration_id],
            )
            new_id = integration_id
        else:
            cols = ["kind"] + FIELDS + ["settings", "credential_encrypted", "credential_hint", "created_by", "updated_by"]
            cur.execute(
                f"INSERT INTO pv.integrations ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING integration_id",
                [kind] + [clean[k] for k in FIELDS] + [json.dumps(settings or {}, default=str),
                 secrets_store.encrypt(credential) if credential else None,
                 secrets_store.hint(credential) if credential else None, user_id, user_id],
            )
            new_id = cur.fetchone()["integration_id"]
            changes = ["added" + (" with a credential" if credential else "")]
    _forget_cache()
    return new_id, changes


def set_enabled(integration_id, enabled, user_id):
    row = get_integration(integration_id)
    if not row:
        raise ValueError("This integration no longer exists.")
    if enabled and row["kind"] == "AI provider" and row["status"] != "Working":
        raise ValueError("Run a successful connection test before switching this AI service on.")
    with transaction() as cur:
        if enabled and row["kind"] == "AI provider":
            # One AI service at a time.
            cur.execute("UPDATE pv.integrations SET enabled = FALSE WHERE kind = 'AI provider' AND integration_id <> %s", (integration_id,))
        cur.execute("UPDATE pv.integrations SET enabled = %s, updated_by = %s, updated_at = NOW() WHERE integration_id = %s",
                    (bool(enabled), user_id, integration_id))
    _forget_cache()


def _show(value):
    if value is None or value == "":
        return "blank"
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return str(value)


def _record_test(integration_id, ok, message):
    with transaction() as cur:
        cur.execute(
            "UPDATE pv.integrations SET status = %s, last_test_at = NOW(), last_test_result = %s WHERE integration_id = %s",
            ("Working" if ok else "Failed", message[:2000], integration_id),
        )
    _forget_cache()


# --------------------------------------------------------------------------
# Connection tests (each checks the real mechanism)
# --------------------------------------------------------------------------

def test_connection(integration_id):
    row = get_integration(integration_id)
    if not row:
        raise ValueError("This integration no longer exists.")
    secret = None
    try:
        secret = secrets_store.decrypt(row.get("credential_encrypted"))
    except secrets_store.SecretError as error:
        _record_test(integration_id, False, str(error))
        return False, str(error)
    try:
        if row["kind"] == "AI provider":
            ok, message = _test_ai(row, secret)
        elif row["kind"] in ("MedDRA", "WHODrug"):
            ok, message = _test_dictionary(row, secret)
        else:
            ok, message = _test_http(row, secret)
    except Exception as error:   # never let a test leak the credential
        ok, message = False, f"Test failed: {secrets_store.redact(str(error), [secret])}"
    message = secrets_store.redact(message, [secret])
    _record_test(integration_id, ok, message)
    return ok, message


def _test_ai(row, secret):
    from app.services import llm

    config = ai_config_from_row(row, secret)
    started = time.monotonic()
    try:
        text = llm.generate_text("Reply with the single word OK.", max_tokens=10, config=config, purpose="connection test")
    except llm.AIUnavailableError as error:
        return False, str(error)
    seconds = time.monotonic() - started
    return True, f"Connected to {config['provider']} model {config['model']} in {seconds:.1f} s (reply: {text[:20]!r})."


def _test_dictionary(row, secret):
    from app.services import terminology

    imported = terminology.dictionary_status(row["kind"])
    parts = []
    ok = False
    if imported.get("terms"):
        ok = True
        parts.append(f"{imported['terms']:,} terms imported (version {imported.get('version') or 'not recorded'}, "
                     f"last import {imported.get('imported_at') or 'unknown'}).")
        if row.get("dictionary_version") and imported.get("version") and row["dictionary_version"] != imported["version"]:
            parts.append(f"Note: the licence record says {row['dictionary_version']}.")
    else:
        parts.append("No dictionary files have been imported yet, so coding uses the local terminology only.")
    if row.get("api_endpoint"):
        api_ok, message = _test_http(row, secret)
        parts.append(("API reachable: " if api_ok else "API problem: ") + message)
        ok = ok or api_ok
    state = licence_state(row)
    if state == "Expired":
        parts.append("The licence has expired.")
        ok = False
    return ok, " ".join(parts)


def _test_http(row, secret):
    import requests

    endpoint = row.get("api_endpoint")
    if not endpoint:
        return False, "No API endpoint is recorded."
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    try:
        response = requests.get(endpoint, headers=headers, timeout=15)
    except requests.RequestException as error:
        return False, f"Could not reach {endpoint}: {error.__class__.__name__}."
    if response.status_code in (401, 403):
        return False, f"{endpoint} refused the credential (HTTP {response.status_code})."
    if response.status_code >= 500:
        return False, f"{endpoint} had a server error (HTTP {response.status_code})."
    return True, f"{endpoint} answered (HTTP {response.status_code})."


# --------------------------------------------------------------------------
# AI configuration used by app.services.llm
# --------------------------------------------------------------------------

_CACHE = {"at": 0.0, "value": None}


def _forget_cache():
    _CACHE["at"] = 0.0


def ai_config_from_row(row, secret=None):
    settings = row.get("settings") or {}
    if isinstance(settings, str):
        settings = json.loads(settings)
    settings = {**DEFAULT_AI_SETTINGS, **settings}
    if secret is None and row.get("credential_encrypted"):
        secret = secrets_store.decrypt(row["credential_encrypted"])
    return {
        "source": f"integration {row.get('integration_id')}",
        "integration_id": row.get("integration_id"),
        "provider": (row.get("provider") or "anthropic").lower(),
        "model": row.get("model"),
        "endpoint": row.get("api_endpoint"),
        "api_key": secret,
        "timeout": int(settings.get("timeout_seconds") or 180),
        "retries": max(0, min(int(settings.get("max_retries") or 0), 5)),
        "monthly_limit": _number(settings.get("monthly_limit")),
        "price_in": _number(settings.get("price_input_per_million")),
        "price_out": _number(settings.get("price_output_per_million")),
        "currency": settings.get("currency") or "USD",
        "use_for_documents": bool(settings.get("use_for_documents")),
    }


def _number(value):
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except Exception:
        return None


def active_ai_config():
    """The enabled AI integration's settings, or None to use the .env settings."""
    if not has_app_context():
        return None
    if time.monotonic() - _CACHE["at"] < 30:
        return _CACHE["value"]
    value = None
    try:
        row = query_one(
            "SELECT * FROM pv.integrations WHERE kind = 'AI provider' AND enabled ORDER BY updated_at DESC LIMIT 1"
        )
        if row:
            value = ai_config_from_row(row)
    except Exception:
        try:
            from app.db import get_db

            get_db().rollback()
        except Exception:
            pass
        value = None
    _CACHE.update(at=time.monotonic(), value=value)
    return value


def month_spend():
    try:
        row = query_one(
            "SELECT COALESCE(SUM(estimated_cost), 0) AS total, COUNT(*) AS calls FROM pv.ai_usage "
            "WHERE occurred_at >= date_trunc('month', NOW()) AND status = 'ok'"
        )
        return Decimal(str(row["total"] or 0)), row["calls"]
    except Exception:
        try:
            from app.db import get_db

            get_db().rollback()
        except Exception:
            pass
        return Decimal("0"), 0


def record_ai_usage(purpose, provider, model, input_tokens, output_tokens, cost, duration_ms, status, error=None, attempt=1):
    if not has_app_context():
        return
    try:
        with transaction() as cur:
            cur.execute(
                """
                INSERT INTO pv.ai_usage (purpose, provider, model, input_tokens, output_tokens,
                                         estimated_cost, duration_ms, status, error_message, attempt)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (purpose, provider, model, input_tokens, output_tokens, cost, duration_ms, status, (error or "")[:500] or None, attempt),
            )
    except Exception:
        current_app.logger.warning("Could not record AI usage", exc_info=True)


def usage_summary():
    try:
        return query_all(
            """
            SELECT date_trunc('month', occurred_at)::date AS month, provider, model,
                   COUNT(*) FILTER (WHERE status = 'ok') AS calls,
                   COUNT(*) FILTER (WHERE status <> 'ok') AS failures,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(estimated_cost), 0) AS cost
            FROM pv.ai_usage
            WHERE occurred_at >= date_trunc('month', NOW()) - INTERVAL '5 months'
            GROUP BY 1, 2, 3
            ORDER BY 1 DESC, 2, 3
            """
        )
    except Exception:
        return []
