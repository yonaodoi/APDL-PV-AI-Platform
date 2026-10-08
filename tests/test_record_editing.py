from datetime import date

import app.complaints.routes as complaint_routes
import app.signals.routes as signal_routes
from app import create_app
from app.services.record_changes import describe_changes
from config import TestingConfig


def test_describe_changes_lists_only_changed_fields():
    text = describe_changes(
        {"a": "Old", "b": date(2026, 9, 1), "c": "same ", "d": None},
        {"a": "New", "b": date(2026, 9, 3), "c": "same", "d": ""},
        {"a": "Reporter", "b": "Date received", "c": "Product", "d": "Batch"},
    )
    assert text == 'Reporter: "Old" → "New"; Date received: 01 Sep 2026 → 03 Sep 2026'
    assert describe_changes({"a": 1}, {"a": 1}, {"a": "A"}) == ""


class _Cursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params):
        self.log.append((" ".join(sql.split()), params))


class _Tx:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return _Cursor(self.log)

    def __exit__(self, *a):
        return False


def _client(monkeypatch):
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0
    )
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"
    return client


COMPLAINT = {
    "complaint_id": 2,
    "complaint_number": "APDL-MKT-26-002",
    "date_received": date(2026, 9, 16),
    "country_id": 1,
    "country_name": "Uganda",
    "reporter_name": "Pharmacist A",
    "reporter_contact": "a@example.com",
    "product_name": "ABPARA",
    "batch_number": "B123",
    "manufacturing_date": None,
    "expiry_date": date(2027, 1, 31),
    "complaint_category": "Packaging",
    "complaint_description": "Leakage",
    "severity": "Non-serious",
    "linked_case_id": None,
}


def _patch_complaint(monkeypatch, clash=None):
    log, audits = [], []
    monkeypatch.setattr(
        complaint_routes,
        "query_one",
        lambda sql, params=(): clash if "complaint_id <> %s" in sql else dict(COMPLAINT),
    )
    monkeypatch.setattr(
        complaint_routes,
        "query_all",
        lambda sql, params=(): [
            {"country_id": 1, "country_name": "Uganda"},
            {"country_id": 2, "country_name": "Malawi"},
        ],
    )
    monkeypatch.setattr(complaint_routes, "transaction", lambda: _Tx(log))
    monkeypatch.setattr(
        complaint_routes, "write_audit_log", lambda *a, **k: audits.append(a)
    )
    return log, audits


def test_complaint_edit_page_is_prefilled(monkeypatch):
    _patch_complaint(monkeypatch)
    client = _client(monkeypatch)

    page = client.get("/complaints/2/edit?section=complaint-product").get_data(as_text=True)

    assert "Edit complaint APDL-MKT-26-002" in page
    assert 'value="B123"' in page
    assert 'name="return_section" value="complaint-product"' in page
    assert 'href="/complaints/2#detail-product"' in page


def test_complaint_edit_saves_and_records_each_change(monkeypatch):
    log, audits = _patch_complaint(monkeypatch)
    client = _client(monkeypatch)

    response = client.post(
        "/complaints/2/edit?section=complaint-product",
        data={
            "complaint_number": "APDL-MKT-26-002",
            "date_received": "2026-09-16",
            "country_id": "2",
            "reporter_name": "Pharmacist A",
            "reporter_contact": "a@example.com",
            "product_name": "ABPARA",
            "batch_number": "B124",
            "expiry_date": "2027-01-31",
            "complaint_category": "Packaging",
            "complaint_description": "Leakage",
            "severity": "Non-serious",
        },
    )

    assert response.headers["Location"].endswith("/complaints/2#detail-product")
    assert log and "UPDATE pv.product_complaints" in log[0][0]
    details = audits[0][4]
    assert 'Country: "Uganda" → "Malawi"' in details
    assert 'Batch number: "B123" → "B124"' in details
    assert "Reporter" not in details


def test_complaint_edit_rejects_an_existing_complaint_id(monkeypatch):
    log, _ = _patch_complaint(monkeypatch, clash={"complaint_id": 9})
    client = _client(monkeypatch)

    page = client.post(
        "/complaints/2/edit",
        data={
            "complaint_number": "APDL-MKT-26-009",
            "date_received": "2026-09-16",
            "country_id": "1",
            "product_name": "ABPARA",
            "complaint_category": "Packaging",
            "complaint_description": "Leakage",
            "severity": "Non-serious",
        },
    ).get_data(as_text=True)

    assert "This Complaint ID already exists." in page
    assert log == []


SIGNAL = {
    "signal_id": 2,
    "signal_number": "APDL-SIG-001",
    "date_detected": date(2026, 9, 16),
    "product_name": "ABPARA",
    "event_term": "CHILLS",
    "signal_source": "ICSR review",
    "signal_description": "Cluster of chills",
    "priority": "Medium",
    "owner_name": None,
}


def _patch_signal(monkeypatch, clash=None):
    log, audits = [], []
    monkeypatch.setattr(
        signal_routes,
        "query_one",
        lambda sql, params=(): clash if "signal_id <> %s" in sql else dict(SIGNAL),
    )
    monkeypatch.setattr(signal_routes, "transaction", lambda: _Tx(log))
    monkeypatch.setattr(
        signal_routes, "write_audit_log", lambda **k: audits.append(k)
    )
    return log, audits


def test_signal_edit_page_is_prefilled(monkeypatch):
    _patch_signal(monkeypatch)
    client = _client(monkeypatch)

    page = client.get("/signals/2/edit").get_data(as_text=True)

    assert "Edit signal APDL-SIG-001" in page
    assert 'value="CHILLS"' in page


def test_signal_edit_rejects_an_existing_signal_id(monkeypatch):
    log, _ = _patch_signal(monkeypatch, clash={"signal_id": 3})
    client = _client(monkeypatch)
    form_data = {
        "signal_number": "APDL-SIG-003",
        "date_detected": "2026-09-16",
        "product_name": "ABPARA",
        "event_term": "CHILLS",
        "signal_source": "ICSR review",
        "signal_description": "Cluster of chills",
        "priority": "Medium",
    }

    page = client.post("/signals/2/edit", data=form_data).get_data(as_text=True)

    assert log == []
    assert "This Signal ID already exists." in page or "Edit signal" in page
