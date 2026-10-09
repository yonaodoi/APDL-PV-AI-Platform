from datetime import date, datetime

import app.administration.routes as admin_routes
from app import create_app
from config import TestingConfig

ROWS = [
    {"record_type": "case", "record_id": 7, "action": "Case reviewed", "details": "Status changed to Assessment.",
     "occurred_at": datetime(2026, 10, 9, 8, 0), "full_name": "Ezekiel Kamugisha", "record_reference": "APDL-ICSR-26-TEST1"},
]


def _client(monkeypatch, calls):
    def fake(sql, parameters=()):
        calls.append((sql, parameters))
        return ROWS
    monkeypatch.setattr(admin_routes, "query_all", fake)
    monkeypatch.setattr("app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0)
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as s:
        s["user_id"], s["full_name"], s["role"] = 1, "Yona Odoi", "System Administrator"
    return client


def test_filters_build_the_query(monkeypatch):
    calls = []
    client = _client(monkeypatch, calls)
    page = client.get("/administration/audit-log?q=TEST1&record_type=case&user=Ezekiel+Kamugisha"
                      "&action=Case+reviewed&start=2026-10-01&end=2026-10-09&limit=1000").get_data(as_text=True)
    sql, parameters = calls[0]
    assert "filtered.record_type = ANY(%s)" in sql and "filtered.full_name = %s" in sql
    assert "filtered.action = %s" in sql and "ILIKE" in sql
    assert parameters[0] == ["case", "safety_case"]
    assert date(2026, 10, 10) in parameters      # "to" date includes the whole day
    assert parameters[-1] == 1000
    assert "APDL-ICSR-26-TEST1" in page and "matching" in page and 'value="TEST1"' in page


def test_csv_export(monkeypatch):
    calls = []
    client = _client(monkeypatch, calls)
    response = client.get("/administration/audit-log?export=csv&record_type=case")
    text = response.get_data(as_text=True)
    assert response.mimetype == "text/csv"
    assert "Date and time,Record type,Record,Action,Performed by,Details" in text
    assert "APDL-ICSR-26-TEST1" in text
