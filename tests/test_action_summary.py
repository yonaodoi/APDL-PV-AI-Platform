import app.services.action_summary as actions
from app import create_app
from config import TestingConfig


def _rows_for(mapping):
    def fake(sql, parameters=()):
        for marker, rows in mapping.items():
            if marker in sql:
                return rows
        return []
    return fake


def _items(monkeypatch, mapping):
    monkeypatch.setattr(actions, "query_all", _rows_for(mapping))
    app = create_app(TestingConfig)
    with app.test_request_context("/"):
        return {item["key"]: item for item in actions.get_action_items()}


COMPLAINT = {
    "complaint_id": 4, "complaint_number": "PQC-4", "status": "Under investigation",
    "severity": "Non-serious", "complaint_category": "Packaging",
    "investigation_summary": "Supplier defect confirmed.", "corrective_action": None,
    "linked_case_id": None, "linked_case_number": None,
}


def test_complaints_ready_and_adverse_event_without_case(monkeypatch):
    adverse = {**COMPLAINT, "complaint_id": 5, "complaint_number": "PQC-5",
               "complaint_category": "Adverse event", "investigation_summary": None}
    items = _items(monkeypatch, {"complaints.status <> 'Investigation complete'": [COMPLAINT, adverse]})

    assert items["complaints-ready"]["records"][0]["label"] == "PQC-4"
    assert items["complaints-no-case"]["urgent"]
    assert list(items)[0] == "complaints-no-case"


def test_signals_grouped_by_next_step(monkeypatch):
    signals = [
        {"signal_id": 1, "signal_number": "SIG-1", "status": "New", "owner_name": None,
         "assessment_summary": None, "decision_summary": None},
        {"signal_id": 2, "signal_number": "SIG-2", "status": "Under evaluation", "owner_name": "PV",
         "assessment_summary": "Plausible.", "decision_summary": None},
        {"signal_id": 3, "signal_number": "SIG-3", "status": "Validated", "owner_name": "PV",
         "assessment_summary": "Confirmed.", "decision_summary": "Label update."},
    ]
    items = _items(monkeypatch, {"FROM pv.safety_signals": signals})

    assert items["signals-new"]["records"][0]["label"] == "SIG-1"
    assert items["signals-decide"]["records"][0]["label"] == "SIG-2"
    assert items["signals-close"]["records"][0]["label"] == "SIG-3"


def test_cases_and_documents_and_more_link(monkeypatch):
    ready = [{"case_id": i, "case_number": f"26-0{i:02d}"} for i in range(1, 8)]
    docs = [{"record_type": "complaint", "record_id": 4, "original_filename": "letter.pdf",
             "case_number": None, "complaint_number": "PQC-4"}]
    items = _items(monkeypatch, {
        "workflow_status = 'Ready for submission'": ready,
        "processing_status = 'Suggestions ready'": docs,
    })

    assert items["cases-ready"]["count"] == 7
    assert len(items["cases-ready"]["records"]) == 5
    assert items["cases-ready"]["more"] == 2
    assert "status=Ready" in items["cases-ready"]["list_url"]
    assert items["documents"]["records"][0]["label"] == "PQC-4 · letter.pdf"


def test_failed_query_hides_only_its_item(monkeypatch):
    def fake(sql, parameters=()):
        if "pv.safety_signals" in sql:
            raise RuntimeError("permission denied")
        if "Ready for submission" in sql:
            return [{"case_id": 1, "case_number": "26-001"}]
        return []
    monkeypatch.setattr(actions, "query_all", fake)
    monkeypatch.setattr(actions, "get_db", lambda: type("D", (), {"rollback": lambda self: None})())
    app = create_app(TestingConfig)
    with app.test_request_context("/"):
        keys = [item["key"] for item in actions.get_action_items()]

    assert keys == ["cases-ready"]
