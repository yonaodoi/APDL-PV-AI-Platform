"""Permanent deletion of a safety case, signal, product complaint or PSUR.

Only the System Administrator, QPPV or Group Head RA & Quality may delete. Before anything is removed, a full
snapshot of the record and of every row that depends on it (products,
events, follow-ups, approvals, attachments metadata, the case's own audit
log, …) is saved in ``pv.deleted_records``. The platform-wide audit trail
(``pv.audit_log``) is never touched, so the history of the record, including
its deletion, stays readable. Uploaded files are left on disk and listed in
the snapshot.

Dependent rows are found from the database's own foreign keys, so tables
added later are included automatically.
"""

import json
from datetime import date, datetime
from decimal import Decimal

from app.db import query_all, query_one, transaction

DELETE_ROLES = ("System Administrator", "QPPV", "Group Head RA & Quality")

RECORD_TYPES = {
    "case": {
        "table": "safety_cases", "key": "case_id", "number": "case_number",
        "label": "safety case", "list_endpoint": "cases.case_list",
    },
    "signal": {
        "table": "safety_signals", "key": "signal_id", "number": "signal_number",
        "label": "safety signal", "list_endpoint": "signals.signal_list",
    },
    "complaint": {
        "table": "product_complaints", "key": "complaint_id", "number": "complaint_number",
        "label": "product complaint", "list_endpoint": "complaints.complaint_list",
    },
    "psur": {
        "table": "psur_reports", "key": "psur_id", "number": "report_number",
        "label": "PSUR record", "list_endpoint": "psur.psur_list",
    },
}

FOREIGN_KEYS_SQL = """
    SELECT child_ns.nspname AS child_schema,
           child.relname AS child_table,
           child_col.attname AS child_column,
           parent_col.attname AS parent_column,
           con.confdeltype AS on_delete
    FROM pg_constraint AS con
    JOIN pg_class AS child ON child.oid = con.conrelid
    JOIN pg_namespace AS child_ns ON child_ns.oid = child.relnamespace
    JOIN pg_class AS parent ON parent.oid = con.confrelid
    JOIN pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
    JOIN pg_attribute AS child_col
      ON child_col.attrelid = con.conrelid AND child_col.attnum = con.conkey[1]
    JOIN pg_attribute AS parent_col
      ON parent_col.attrelid = con.confrelid AND parent_col.attnum = con.confkey[1]
    WHERE con.contype = 'f'
      AND array_length(con.conkey, 1) = 1
      AND parent_ns.nspname = %s
      AND parent.relname = %s
    ORDER BY child.relname, child_col.attname
"""

PRIMARY_KEY_SQL = """
    SELECT att.attname AS column_name
    FROM pg_index AS idx
    JOIN pg_class AS cls ON cls.oid = idx.indrelid
    JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
    JOIN pg_attribute AS att ON att.attrelid = cls.oid AND att.attnum = ANY(idx.indkey)
    WHERE idx.indisprimary AND ns.nspname = %s AND cls.relname = %s
"""

# Deleting a case must not delete other cases, signals or complaints that
# merely point at it; those links are cleared or removed instead.
NEVER_DESCEND_INTO = {"safety_cases", "safety_signals", "product_complaints", "psur_reports", "users"}

FILE_COLUMNS = ("stored_filename", "file_path", "storage_path", "stored_path", "original_filename")


class DeletionError(Exception):
    pass


def _ident(name):
    if not name.replace("_", "").isalnum():
        raise DeletionError(f"Unexpected table or column name: {name}")
    return '"' + name + '"'


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, memoryview)):
        return "<binary data not copied>"
    return str(value)


def load_record(kind, record_id):
    spec = RECORD_TYPES.get(kind)
    if not spec:
        return None
    return query_one(
        f"SELECT * FROM pv.{_ident(spec['table'])} WHERE {_ident(spec['key'])} = %s",
        (record_id,),
    )


def _plan(cursor, schema, table, column, values, plan, seen, depth=0):
    """Collect every row that depends on ``table.column IN values``."""
    if not values or depth > 6:
        return
    cursor.execute(FOREIGN_KEYS_SQL, (schema, table))
    for fk in cursor.fetchall():
        if fk["parent_column"] != column:
            parent_values = _column_values(cursor, schema, table, column, values, fk["parent_column"])
        else:
            parent_values = values
        if not parent_values:
            continue
        child_schema, child_table, child_column = fk["child_schema"], fk["child_table"], fk["child_column"]
        key = (child_schema, child_table, child_column)
        if key in seen:
            continue
        seen.add(key)
        cursor.execute(
            f"SELECT to_jsonb(t) AS row FROM {_ident(child_schema)}.{_ident(child_table)} AS t "
            f"WHERE t.{_ident(child_column)} = ANY(%s)",
            (list(parent_values),),
        )
        rows = [r["row"] for r in cursor.fetchall()]
        if not rows:
            continue
        action = fk["on_delete"]
        step = {
            "schema": child_schema, "table": child_table, "column": child_column,
            "values": list(parent_values), "rows": rows, "action": action, "depth": depth,
        }
        plan.append(step)
        if action == "n" or child_table in NEVER_DESCEND_INTO:
            # ON DELETE SET NULL, or a link from another main record: only the
            # link is affected (cleared below), never the other record.
            continue
        cursor.execute(PRIMARY_KEY_SQL, (child_schema, child_table))
        pk = [r["column_name"] for r in cursor.fetchall()]
        if len(pk) == 1:
            child_ids = [row.get(pk[0]) for row in rows if row.get(pk[0]) is not None]
            _plan(cursor, child_schema, child_table, pk[0], child_ids, plan, seen, depth + 1)


def _column_values(cursor, schema, table, column, values, wanted):
    cursor.execute(
        f"SELECT DISTINCT t.{_ident(wanted)} AS v FROM {_ident(schema)}.{_ident(table)} AS t "
        f"WHERE t.{_ident(column)} = ANY(%s) AND t.{_ident(wanted)} IS NOT NULL",
        (list(values),),
    )
    return [r["v"] for r in cursor.fetchall()]


def deletion_preview(kind, record_id):
    """What deleting the record would remove, as [(table, count)], without changing anything."""
    spec = RECORD_TYPES[kind]
    plan = []
    with transaction() as cursor:
        _plan(cursor, "pv", spec["table"], spec["key"], [record_id], plan, set())
        cursor.execute(
            "SELECT COUNT(*) AS n FROM pv.record_attachments WHERE record_type = %s AND record_id = %s",
            (kind, record_id),
        )
        attachments = cursor.fetchone()["n"]
    summary = {}
    for step in plan:
        label = step["table"].replace("_", " ")
        if step["action"] == "n" or step["table"] in NEVER_DESCEND_INTO:
            label += " (link cleared)"
        summary[label] = summary.get(label, 0) + len(step["rows"])
    if attachments:
        summary["record attachments"] = summary.get("record attachments", 0) + attachments
    return sorted(summary.items())


def delete_record(kind, record_id, reason, user_id, confirm_number):
    """Delete the record and everything that belongs to it. Returns the record number."""
    spec = RECORD_TYPES.get(kind)
    if not spec:
        raise DeletionError("Unknown record type.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise DeletionError("Give a reason for the deletion (at least a few words).")

    with transaction() as cursor:
        cursor.execute(
            f"SELECT to_jsonb(t) AS row FROM pv.{_ident(spec['table'])} AS t "
            f"WHERE t.{_ident(spec['key'])} = %s FOR UPDATE",
            (record_id,),
        )
        found = cursor.fetchone()
        if not found:
            raise DeletionError(f"This {spec['label']} no longer exists.")
        record = found["row"]
        number = str(record.get(spec["number"]) or record_id)
        if (confirm_number or "").strip() != number:
            raise DeletionError(f"Type the number {number} exactly to confirm.")

        plan = []
        _plan(cursor, "pv", spec["table"], spec["key"], [record_id], plan, set())

        cursor.execute(
            "SELECT to_jsonb(t) AS row FROM pv.record_attachments AS t WHERE record_type = %s AND record_id = %s",
            (kind, record_id),
        )
        attachments = [r["row"] for r in cursor.fetchall()]

        files = []
        for row in attachments + [r for step in plan for r in step["rows"]]:
            for col in FILE_COLUMNS[:4]:
                if row.get(col):
                    files.append(str(row[col]))

        snapshot = {
            "record": record,
            "dependent_rows": [
                {"table": f"{s['schema']}.{s['table']}", "via": s["column"], "on_delete": s["action"], "rows": s["rows"]}
                for s in plan
            ],
            "record_attachments": attachments,
            "files_left_on_disk": sorted(set(files)),
        }
        cursor.execute(
            """
            INSERT INTO pv.deleted_records
                (record_type, record_id, record_number, reason, snapshot, deleted_by)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s)
            """,
            (kind, record_id, number, reason, json.dumps(snapshot, default=_json_default), user_id),
        )

        # Links from other main records (and SET NULL links) are cleared;
        # rows that would block the delete are removed deepest first.
        for step in plan:
            if step["table"] in NEVER_DESCEND_INTO and step["action"] not in ("c", "n"):
                cursor.execute(
                    f"UPDATE {_ident(step['schema'])}.{_ident(step['table'])} "
                    f"SET {_ident(step['column'])} = NULL WHERE {_ident(step['column'])} = ANY(%s)",
                    (step["values"],),
                )
        for step in sorted(plan, key=lambda s: -s["depth"]):
            if step["table"] in NEVER_DESCEND_INTO or step["action"] in ("n",):
                continue
            if step["action"] in ("a", "r", "c"):
                cursor.execute(
                    f"DELETE FROM {_ident(step['schema'])}.{_ident(step['table'])} "
                    f"WHERE {_ident(step['column'])} = ANY(%s)",
                    (step["values"],),
                )
        cursor.execute(
            "DELETE FROM pv.record_attachments WHERE record_type = %s AND record_id = %s",
            (kind, record_id),
        )
        cursor.execute(
            f"DELETE FROM pv.{_ident(spec['table'])} WHERE {_ident(spec['key'])} = %s",
            (record_id,),
        )
        cursor.execute(
            """
            INSERT INTO pv.audit_log (record_type, record_id, action, details, actor_user_id)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                kind, record_id, "Record deleted",
                f"{spec['label'].capitalize()} {number} was permanently deleted. Reason: {reason}. "
                "A full copy is kept under Administration > Deleted records.",
                user_id,
            ),
        )
    return number


def deleted_records(limit=200):
    return query_all(
        """
        SELECT d.deleted_id, d.record_type, d.record_id, d.record_number, d.reason,
               d.deleted_at, u.full_name AS deleted_by_name
        FROM pv.deleted_records AS d
        LEFT JOIN pv.users AS u ON u.user_id = d.deleted_by
        ORDER BY d.deleted_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def deleted_snapshot(deleted_id):
    return query_one("SELECT * FROM pv.deleted_records WHERE deleted_id = %s", (deleted_id,))
