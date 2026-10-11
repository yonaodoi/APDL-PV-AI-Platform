from app.services import record_deletion


def _client(monkeypatch, role="System Administrator"):
    from app import create_app
    from config import TestingConfig

    monkeypatch.setattr("app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0)
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as s:
        s["user_id"], s["full_name"], s["role"] = 1, "Admin", role
    return client


CASE = {"case_id": 7, "case_number": "APDL-ICSR-26-TEST1"}


def test_only_the_system_administrator_can_delete(monkeypatch):
    monkeypatch.setattr(record_deletion, "load_record", lambda kind, rid: CASE)
    for role in ("PV Officer", "QPPV", "Group Head RA & Quality", "Auditor"):
        client = _client(monkeypatch, role)
        assert client.get("/administration/delete/case/7").status_code in (302, 403)
        assert client.post("/administration/delete/case/7", data={}).status_code in (302, 403)


def test_confirmation_page_lists_what_will_be_removed(monkeypatch):
    monkeypatch.setattr(record_deletion, "load_record", lambda kind, rid: CASE)
    monkeypatch.setattr(record_deletion, "deletion_preview", lambda kind, rid: [("case products", 2), ("product complaints (link cleared)", 1)])
    page = _client(monkeypatch).get("/administration/delete/case/7").get_data(as_text=True)
    assert "Permanently delete safety case APDL-ICSR-26-TEST1" in page
    assert "Case products" in page and "link cleared" in page
    assert "Type <strong>APDL-ICSR-26-TEST1</strong> to confirm" in page


def test_deleting_needs_the_number_and_a_reason(monkeypatch):
    calls = []
    monkeypatch.setattr(record_deletion, "load_record", lambda kind, rid: CASE)

    def fake_delete(kind, rid, reason, user_id, confirm):
        calls.append((kind, rid, reason, confirm))
        if confirm != "APDL-ICSR-26-TEST1":
            raise record_deletion.DeletionError("Type the number APDL-ICSR-26-TEST1 exactly to confirm.")
        return confirm

    monkeypatch.setattr(record_deletion, "delete_record", fake_delete)
    client = _client(monkeypatch)
    wrong = client.post("/administration/delete/case/7", data={"reason": "Training record", "confirm_number": "x"})
    assert wrong.status_code == 302 and "/administration/delete/case/7" in wrong.headers["Location"]
    right = client.post("/administration/delete/case/7", data={"reason": "Training record", "confirm_number": "APDL-ICSR-26-TEST1"})
    assert right.status_code == 302 and right.headers["Location"].endswith("/cases/")
    assert calls[-1] == ("case", 7, "Training record", "APDL-ICSR-26-TEST1")


def test_delete_link_shows_only_for_administrators():
    from pathlib import Path

    for template in ("cases/case_detail.html", "signals/signal_detail.html",
                     "complaints/complaint_detail.html", "psur/psur_detail.html"):
        text = Path("app/templates", template).read_text(encoding="utf-8")
        assert "{% if session.get('role') == 'System Administrator' %}<a class=\"menu-danger\"" in text, template


class FakeCursor:
    """Enough of a database to walk foreign keys for one case."""

    def __init__(self):
        self.executed = []
        self._result = []
        self.fks = {
            ("pv", "safety_cases"): [
                {"child_schema": "pv", "child_table": "case_products", "child_column": "case_id", "parent_column": "case_id", "on_delete": "c"},
                {"child_schema": "pv", "child_table": "safety_signal_cases", "child_column": "case_id", "parent_column": "case_id", "on_delete": "a"},
                {"child_schema": "pv", "child_table": "product_complaints", "child_column": "linked_case_id", "parent_column": "case_id", "on_delete": "n"},
            ],
        }
        self.rows = {
            "case_products": [{"case_product_id": 1, "case_id": 7}],
            "safety_signal_cases": [{"signal_case_id": 3, "case_id": 7, "signal_id": 2}],
            "product_complaints": [{"complaint_id": 9, "linked_case_id": 7}],
            "safety_cases": [{"case_id": 7, "case_number": "APDL-ICSR-26-TEST1"}],
        }

    def execute(self, sql, params=()):
        self.executed.append((" ".join(sql.split()), params))
        if "FROM pg_constraint" in sql:
            self._result = self.fks.get(tuple(params), [])
        elif "pg_index" in sql:
            table = params[1]
            self._result = [{"column_name": {"case_products": "case_product_id", "safety_signal_cases": "signal_case_id"}.get(table, "id")}]
        elif "to_jsonb(t)" in sql:
            import re

            match = re.search(r'FROM (?:"?pv"?\.)"?([a-z_]+)"?', sql)
            table = match.group(1) if match else ""
            if "record_attachments" in sql:
                self._result = []
            else:
                self._result = [{"row": r} for r in self.rows.get(table, [])]
        elif "COUNT(*)" in sql:
            self._result = [{"n": 0}]
        else:
            self._result = []

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


def test_delete_keeps_a_copy_and_clears_links(monkeypatch):
    from contextlib import contextmanager

    cursor = FakeCursor()

    @contextmanager
    def fake_transaction():
        yield cursor

    monkeypatch.setattr(record_deletion, "transaction", fake_transaction)
    number = record_deletion.delete_record("case", 7, "Duplicate training case", 1, "APDL-ICSR-26-TEST1")
    assert number == "APDL-ICSR-26-TEST1"
    statements = [sql for sql, _ in cursor.executed]
    copy = next(i for i, sql in enumerate(statements) if sql.startswith("INSERT INTO pv.deleted_records"))
    removed = [i for i, sql in enumerate(statements) if sql.startswith("DELETE FROM")]
    assert removed and copy < min(removed)                       # the copy is saved before anything is removed
    assert any('DELETE FROM "pv"."safety_signal_cases"' in sql for sql in statements)
    assert not any('DELETE FROM "pv"."product_complaints"' in sql for sql in statements)   # complaint kept
    assert any(sql.startswith("INSERT INTO pv.audit_log") for sql in statements)
    assert not any("DELETE FROM pv.audit_log" in sql for sql in statements)


def test_wrong_confirmation_deletes_nothing(monkeypatch):
    from contextlib import contextmanager

    cursor = FakeCursor()

    @contextmanager
    def fake_transaction():
        yield cursor

    monkeypatch.setattr(record_deletion, "transaction", fake_transaction)
    try:
        record_deletion.delete_record("case", 7, "Duplicate training case", 1, "APDL-ICSR-26-009")
    except record_deletion.DeletionError as error:
        assert "APDL-ICSR-26-TEST1" in str(error)
    else:
        raise AssertionError("expected a DeletionError")
    assert not any(sql.startswith("DELETE") for sql, _ in cursor.executed)


def test_unsaved_case_form_explains_what_to_fix():
    from app.cases.routes import _form_problem_summary

    class Field:
        def __init__(self, text):
            self.label = type("L", (), {"text": text})()

    class Form:
        errors = {"received_date": ["This field is required."], "product_name": ["This field is required."]}
        received_date = Field("Date received")
        product_name = Field("Product name")

    assert _form_problem_summary(Form()) == "2 fields need attention: Date received, Product name."
