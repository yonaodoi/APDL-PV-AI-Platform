"""The company product catalogue.

Holds the reusable facts about each product (identifier, brand, active
ingredients, strength, form, approved routes, manufacturer, MAH, registration,
market, status, reference safety information). Safety cases, signals and
complaints can pick a product: its reusable facts are filled in, and the
record keeps the catalogue reference plus a snapshot of the version used, so a
later catalogue change never silently rewrites an existing record. Case
specifics (dose, dates, batch, expiry, patient, event, outcome, causality) are
never taken from the catalogue.
"""

import hashlib
import json
import re
from datetime import date, datetime

from app.db import query_all, query_one, transaction

FIELDS = [
    ("product_code", "Product identifier"),
    ("brand_name", "Brand name"),
    ("generic_name", "Generic name / active ingredient(s)"),
    ("strength", "Strength"),
    ("dosage_form", "Pharmaceutical form"),
    ("routes", "Approved route(s)"),
    ("manufacturer", "Manufacturer"),
    ("mah", "Marketing authorisation holder"),
    ("registration_number", "Registration number"),
    ("market", "Country / market"),
    ("status", "Product status"),
    ("notes", "Notes"),
]
LABELS = dict(FIELDS)
STATUSES = ["Active", "Pending approval", "Suspended", "Withdrawn", "Discontinued"]
REQUIRED = ("product_code", "brand_name")
MANAGE_ROLES = ("System Administrator", "QPPV", "Deputy QPPV", "Group Head RA & Quality")

# Facts copied into a record when a catalogue product is chosen.
REUSABLE = ("brand_name", "generic_name", "strength", "dosage_form", "routes", "mah", "manufacturer")

HEADER_ALIASES = {
    "product_code": ["product_code", "product_identifier", "product_id", "identifier", "code", "sku", "item_code"],
    "brand_name": ["brand_name", "brand", "trade_name", "product_name", "name"],
    "generic_name": ["generic_name", "generic", "active_ingredients", "active_ingredient", "active_substance", "inn"],
    "strength": ["strength", "potency"],
    "dosage_form": ["dosage_form", "pharmaceutical_form", "form"],
    "routes": ["routes", "approved_routes", "route", "route_of_administration"],
    "manufacturer": ["manufacturer", "manufactured_by"],
    "mah": ["mah", "marketing_authorisation_holder", "marketing_authorization_holder", "ma_holder"],
    "registration_number": ["registration_number", "registration_no", "reg_no", "ma_number", "licence_number", "license_number"],
    "market": ["market", "country", "country_market", "territory"],
    "status": ["status", "product_status"],
    "notes": ["notes", "comments", "remarks"],
}


def _clean(value, limit=500):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def normalise_status(value):
    text = _clean(value).lower()
    for status in STATUSES:
        if text == status.lower():
            return status
    if text in ("", "current", "registered", "marketed", "yes", "active "):
        return "Active"
    if text in ("withdrawn", "cancelled", "revoked"):
        return "Withdrawn"
    if text in ("pending", "under review", "submitted"):
        return "Pending approval"
    return None


def clean_product(values):
    """Tidy one product's values; returns (product, problems)."""
    product = {}
    problems = []
    for key, _ in FIELDS:
        product[key] = _clean(values.get(key), 2000 if key == "notes" else 500) or None
    if product.get("product_code"):
        product["product_code"] = product["product_code"].upper()
    status = normalise_status(values.get("status"))
    if status is None:
        problems.append(f"Unknown status “{values.get('status')}”. Use: {', '.join(STATUSES)}.")
        status = "Active"
    product["status"] = status
    for key in REQUIRED:
        if not product.get(key):
            problems.append(f"{LABELS[key]} is required.")
    rsi = values.get("rsi_id")
    product["rsi_id"] = int(rsi) if str(rsi or "").isdigit() else None
    return product, problems


def _key(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def list_products(q="", status="", market=""):
    filters, params = [], []
    if q:
        like = f"%{q.strip()}%"
        filters.append("(p.product_code ILIKE %s OR p.brand_name ILIKE %s OR p.generic_name ILIKE %s OR p.registration_number ILIKE %s)")
        params += [like, like, like, like]
    if status:
        filters.append("p.status = %s")
        params.append(status)
    if market:
        filters.append("p.market = %s")
        params.append(market)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    return query_all(
        f"""
        SELECT p.*, r.document_type AS rsi_document_type, r.document_version AS rsi_version,
               u.full_name AS updated_by_name
        FROM pv.product_catalogue AS p
        LEFT JOIN pv.reference_safety_information AS r ON r.rsi_id = p.rsi_id
        LEFT JOIN pv.users AS u ON u.user_id = p.updated_by
        {where}
        ORDER BY p.brand_name, p.strength NULLS FIRST, p.product_code
        """,
        tuple(params),
    )


def get_product(product_id):
    return query_one("SELECT * FROM pv.product_catalogue WHERE product_id = %s", (product_id,))


def product_versions(product_id):
    return query_all(
        """
        SELECT v.*, u.full_name AS changed_by_name
        FROM pv.product_catalogue_versions AS v
        LEFT JOIN pv.users AS u ON u.user_id = v.changed_by
        WHERE v.product_id = %s
        ORDER BY v.version DESC
        """,
        (product_id,),
    )


def markets():
    return [r["market"] for r in query_all(
        "SELECT DISTINCT market FROM pv.product_catalogue WHERE market IS NOT NULL ORDER BY market"
    )]


def picker_options(include_inactive=False):
    """Compact list for the product pickers on forms."""
    rows = query_all(
        """
        SELECT product_id, product_code, brand_name, generic_name, strength, dosage_form, routes,
               manufacturer, mah, market, status, version
        FROM pv.product_catalogue
        WHERE %s OR status IN ('Active', 'Pending approval')
        ORDER BY brand_name, strength NULLS FIRST
        """,
        (include_inactive,),
    )
    return [dict(r, label=picker_label(r)) for r in rows]


def picker_label(row):
    bits = [row.get("brand_name")]
    extra = " ".join(x for x in (row.get("strength"), row.get("dosage_form")) if x)
    if extra:
        bits.append(extra)
    label = " ".join(b for b in bits if b)
    tail = [x for x in (row.get("market"), row.get("product_code")) if x]
    if tail:
        label += f" ({' · '.join(tail)})"
    if row.get("status") and row["status"] != "Active":
        label += f" – {row['status']}"
    return label


def _jsonable(row):
    out = {}
    for key, value in dict(row).items():
        if isinstance(value, (date, datetime)):
            value = value.isoformat()
        out[key] = value
    return out


def snapshot_for(product_id):
    """(version, snapshot) of the product as it is now, for storing on a record."""
    product = get_product(product_id)
    if not product:
        return None, None
    snapshot = {k: product.get(k) for k in ["product_code", *REUSABLE, "registration_number", "market", "status", "rsi_id"]}
    return product["version"], _jsonable(snapshot)


def changed_since(record_version, product):
    """Whether the catalogue entry changed after the record took its snapshot."""
    return bool(product and record_version and product.get("version", 0) > record_version)


# --------------------------------------------------------------------------
# Saving
# --------------------------------------------------------------------------

def find_duplicates(product, exclude_id=None):
    """Existing products that look like the same product."""
    rows = query_all(
        """
        SELECT product_id, product_code, brand_name, strength, dosage_form, market, registration_number
        FROM pv.product_catalogue WHERE product_id <> COALESCE(%s, -1)
        """,
        (exclude_id,),
    )
    return _duplicates_in(product, rows)


def _duplicates_in(product, rows):
    found = []
    for row in rows:
        reasons = []
        if product.get("product_code") and _key(row["product_code"]) == _key(product["product_code"]):
            reasons.append("same product identifier")
        if product.get("registration_number") and _key(row.get("registration_number")) == _key(product["registration_number"]) \
                and _key(row.get("market")) == _key(product.get("market")):
            reasons.append("same registration number and market")
        if _key(row.get("brand_name")) == _key(product.get("brand_name")) and _key(row.get("strength")) == _key(product.get("strength")) \
                and _key(row.get("dosage_form")) == _key(product.get("dosage_form")) and _key(row.get("market")) == _key(product.get("market")):
            reasons.append("same brand, strength, form and market")
        if reasons:
            found.append(dict(row, reasons=reasons))
    return found


def save_product(values, user_id, product_id=None, change_note=None, cursor=None):
    """Create or update; every save keeps the previous version. Returns (id, version, changes)."""
    product, problems = clean_product(values)
    if problems:
        raise ValueError(" ".join(problems))

    def run(cur):
        cols = [k for k, _ in FIELDS] + ["rsi_id"]
        if product_id:
            cur.execute("SELECT * FROM pv.product_catalogue WHERE product_id = %s FOR UPDATE", (product_id,))
            before = cur.fetchone()
            if not before:
                raise ValueError("The product no longer exists.")
            changes = [
                f"{LABELS.get(c, 'Reference safety information')}: {before.get(c) or 'blank'} → {product.get(c) or 'blank'}"
                for c in cols if (before.get(c) or None) != (product.get(c) or None)
            ]
            if not changes:
                return product_id, before["version"], []
            version = before["version"] + 1
            cur.execute(
                f"UPDATE pv.product_catalogue SET {', '.join(f'{c} = %s' for c in cols)}, version = %s, "
                "updated_by = %s, updated_at = NOW() WHERE product_id = %s",
                [product.get(c) for c in cols] + [version, user_id, product_id],
            )
            new_id = product_id
        else:
            cur.execute(
                f"INSERT INTO pv.product_catalogue ({', '.join(cols)}, created_by, updated_by) "
                f"VALUES ({', '.join(['%s'] * len(cols))}, %s, %s) RETURNING product_id",
                [product.get(c) for c in cols] + [user_id, user_id],
            )
            new_id = cur.fetchone()["product_id"]
            version = 1
            changes = ["Product added"]
        cur.execute("SELECT * FROM pv.product_catalogue WHERE product_id = %s", (new_id,))
        snapshot = _jsonable(cur.fetchone())
        cur.execute(
            "INSERT INTO pv.product_catalogue_versions (product_id, version, snapshot, change_note, changed_by) "
            "VALUES (%s, %s, %s::jsonb, %s, %s)",
            (new_id, version, json.dumps(snapshot), (change_note or "; ".join(changes))[:4000], user_id),
        )
        return new_id, version, changes

    if cursor is not None:
        return run(cursor)
    with transaction() as cur:
        return run(cur)


# --------------------------------------------------------------------------
# Bulk import (preview → reviewed apply)
# --------------------------------------------------------------------------

def map_headers(headers):
    """Which file column feeds which catalogue field."""
    mapping = {}
    for field, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in headers and alias not in mapping.values():
                mapping[field] = alias
                break
    return mapping


def build_preview(headers, rows, existing):
    """Validate every row, detect duplicates and propose an action."""
    mapping = map_headers(headers)
    missing = [LABELS[f] for f in REQUIRED if f not in mapping]
    preview = []
    seen_codes = {}
    by_code = {_key(r["product_code"]): r for r in existing}
    for raw in rows:
        values = {field: raw.get(column, "") for field, column in mapping.items()}
        product, problems = clean_product(values)
        code_key = _key(product.get("product_code"))
        if code_key and code_key in seen_codes:
            problems.append(f"The identifier {product['product_code']} appears again on line {seen_codes[code_key]}.")
        elif code_key:
            seen_codes[code_key] = raw["__line"]
        match = by_code.get(code_key) if code_key else None
        similar = [d for d in _duplicates_in(product, existing) if not match or d["product_id"] != match["product_id"]]
        entry = {"line": raw["__line"], "product": product, "problems": problems,
                 "match_id": match["product_id"] if match else None, "similar": similar, "differences": []}
        if problems:
            entry["action"] = "error"
        elif match:
            diffs = [f"{LABELS[k]}: {match.get(k) or 'blank'} → {product.get(k) or 'blank'}"
                     for k, _ in FIELDS if k != "product_code" and k in mapping and (match.get(k) or None) != (product.get(k) or None)]
            entry["differences"] = diffs
            entry["action"] = "update" if diffs else "unchanged"
        elif similar:
            entry["action"] = "review"          # looks like an existing product: a person decides
        else:
            entry["action"] = "create"
        preview.append(entry)
    summary = {
        "rows": len(preview),
        "create": sum(1 for p in preview if p["action"] == "create"),
        "update": sum(1 for p in preview if p["action"] == "update"),
        "unchanged": sum(1 for p in preview if p["action"] == "unchanged"),
        "review": sum(1 for p in preview if p["action"] == "review"),
        "error": sum(1 for p in preview if p["action"] == "error"),
        "mapping": mapping,
        "unmapped": [h for h in headers if h and h not in mapping.values()],
        "missing_columns": missing,
    }
    return preview, summary


def start_import(filename, data, user_id):
    from app.services.tabular_import import read_table

    digest = hashlib.sha256(data).hexdigest()
    pending = query_one(
        "SELECT batch_id FROM pv.product_import_batches WHERE file_sha256 = %s AND status = 'Preview' ORDER BY batch_id DESC LIMIT 1",
        (digest,),
    )
    if pending:
        return pending["batch_id"], True       # the same file is already waiting for review
    headers, rows = read_table(filename, data)
    existing = query_all("SELECT * FROM pv.product_catalogue")
    preview, summary = build_preview(headers, rows, existing)
    with transaction() as cur:
        cur.execute(
            "INSERT INTO pv.product_import_batches (original_filename, file_sha256, rows, summary, uploaded_by) "
            "VALUES (%s, %s, %s::jsonb, %s::jsonb, %s) RETURNING batch_id",
            (filename[:255], digest, json.dumps(preview, default=str), json.dumps(summary, default=str), user_id),
        )
        return cur.fetchone()["batch_id"], False


def get_batch(batch_id):
    row = query_one(
        """
        SELECT b.*, u.full_name AS uploaded_by_name
        FROM pv.product_import_batches AS b
        LEFT JOIN pv.users AS u ON u.user_id = b.uploaded_by
        WHERE b.batch_id = %s
        """,
        (batch_id,),
    )
    if row:
        for key in ("rows", "summary"):
            if isinstance(row[key], str):
                row[key] = json.loads(row[key])
    return row


def apply_import(batch_id, decisions, user_id):
    """Apply the reviewed rows. ``decisions``: {line: 'create'|'update'|'skip'|'update:<id>'}."""
    batch = get_batch(batch_id)
    if not batch or batch["status"] != "Preview":
        raise ValueError("This import has already been applied or discarded.")
    created = updated = skipped = 0
    with transaction() as cur:
        cur.execute("SELECT status FROM pv.product_import_batches WHERE batch_id = %s FOR UPDATE", (batch_id,))
        if cur.fetchone()["status"] != "Preview":
            raise ValueError("This import has already been applied.")
        for entry in batch["rows"]:
            decision = decisions.get(str(entry["line"]), "skip")
            if entry["action"] == "error" or decision == "skip":
                skipped += 1
                continue
            note = f"Imported from {batch['original_filename']} (import {batch_id}, line {entry['line']})."
            if decision == "create":
                if entry["action"] in ("update", "unchanged"):
                    skipped += 1        # identifier already exists: never create a second copy
                    continue
                save_product(entry["product"], user_id, change_note=note, cursor=cur)
                created += 1
            elif decision.startswith("update"):
                target = entry.get("match_id")
                if ":" in decision:
                    target = int(decision.split(":", 1)[1])
                if not target:
                    skipped += 1
                    continue
                values = dict(entry["product"])
                cur.execute("SELECT product_code FROM pv.product_catalogue WHERE product_id = %s", (target,))
                current = cur.fetchone()
                if not current:
                    skipped += 1
                    continue
                values["product_code"] = current["product_code"]   # identifiers are not renamed by an import
                _, _, changes = save_product(values, user_id, product_id=target, change_note=note, cursor=cur)
                updated += 1 if changes else 0
                skipped += 0 if changes else 1
        summary = dict(batch["summary"], applied={"created": created, "updated": updated, "skipped": skipped})
        cur.execute(
            "UPDATE pv.product_import_batches SET status = 'Applied', applied_by = %s, applied_at = NOW(), summary = %s::jsonb "
            "WHERE batch_id = %s",
            (user_id, json.dumps(summary, default=str), batch_id),
        )
    return created, updated, skipped


def discard_import(batch_id):
    with transaction() as cur:
        cur.execute("UPDATE pv.product_import_batches SET status = 'Discarded' WHERE batch_id = %s AND status = 'Preview'", (batch_id,))


TEMPLATE_CSV = (
    "product_code,brand_name,generic_name,strength,dosage_form,routes,manufacturer,mah,registration_number,market,status,notes\n"
    "EX-001,Example brand,Paracetamol,1 g/100 mL,Solution for infusion,Intravenous,Example Manufacturer Ltd,"
    "Example MAH Ltd,REG/2026/001,Uganda,Active,\n"
)


# --------------------------------------------------------------------------
# Records: choosing a product
# --------------------------------------------------------------------------

def attach_to_record(cursor, table, key_column, record_id, product_id):
    """Store the catalogue reference and snapshot on a record (cases' drugs,
    complaints, signals). A record keeps its snapshot even if the catalogue
    changes later; it is only refreshed when a person picks the product again."""
    if not product_id:
        cursor.execute(
            f"UPDATE pv.{table} SET catalogue_product_id = NULL, catalogue_version = NULL, catalogue_snapshot = NULL "
            f"WHERE {key_column} = %s",
            (record_id,),
        )
        return
    cursor.execute(f"SELECT catalogue_product_id, catalogue_version FROM pv.{table} WHERE {key_column} = %s", (record_id,))
    current = cursor.fetchone() or {}
    if current.get("catalogue_product_id") == int(product_id) and current.get("catalogue_version"):
        return
    cursor.execute("SELECT * FROM pv.product_catalogue WHERE product_id = %s", (product_id,))
    product = cursor.fetchone()
    if not product:
        return
    snapshot = {k: product.get(k) for k in ["product_code", *REUSABLE, "registration_number", "market", "status", "rsi_id"]}
    cursor.execute(
        f"UPDATE pv.{table} SET catalogue_product_id = %s, catalogue_version = %s, catalogue_snapshot = %s::jsonb "
        f"WHERE {key_column} = %s",
        (product["product_id"], product["version"], json.dumps(_jsonable(snapshot)), record_id),
    )


def apply_record_choice(table, key_column, record_id, chosen):
    """Store a picker choice on a complaint or signal. Returns True if it changed."""
    chosen_id = int(chosen) if str(chosen or "").strip().isdigit() else None
    current = query_one(f"SELECT catalogue_product_id FROM pv.{table} WHERE {key_column} = %s", (record_id,))
    if current is None or (current.get("catalogue_product_id") or None) == chosen_id:
        return False
    with transaction() as cur:
        attach_to_record(cur, table, key_column, record_id, chosen_id)
    return True
