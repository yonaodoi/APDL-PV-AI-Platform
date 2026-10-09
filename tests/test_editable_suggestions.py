from datetime import date

from app.services.case_documents import APPEND_FIELDS, CHOICES, DATE_FIELDS, normalise_extracted
from app.services.suggestion_edits import annotate, apply_edits

SUGGESTIONS = [
    {"field": "event_outcome", "table": "case", "label": "Event outcome", "kind": "fill",
     "current": "", "proposed": "Recovering/resolving", "new_value": "Recovering/resolving"},
    {"field": "event_onset_date", "table": "case", "label": "Event onset date", "kind": "change",
     "current": "2026-09-01", "proposed": "2026-09-02", "new_value": "2026-09-02"},
    {"field": "patient_weight_kg", "table": "case", "label": "Patient weight (kg)", "kind": "fill",
     "current": "", "proposed": "64", "new_value": "64"},
    {"field": "medical_history", "table": "case", "label": "Medical history", "kind": "add",
     "current": "Asthma", "proposed": "Hypertension since 2019",
     "new_value": "Asthma\n\n[From follow-up response, 09 Oct 2026] Hypertension since 2019"},
    {"field": "country_id", "table": "case", "label": "Country", "kind": "fill",
     "current": "", "proposed": "Uganda", "new_value": "3"},
]
COUNTRIES = [{"country_id": 3, "country_name": "Uganda"}]


def normalise(values):
    return normalise_extracted(values, COUNTRIES)


def test_each_suggestion_gets_the_right_editor():
    editors = {s["field"]: s["editor"] for s in annotate(SUGGESTIONS, DATE_FIELDS, CHOICES, APPEND_FIELDS)}
    assert editors == {
        "event_outcome": "choice",
        "event_onset_date": "date",
        "patient_weight_kg": "text",
        "medical_history": "textarea",
        "country_id": "none",
    }


def test_unchanged_values_are_applied_as_suggested():
    form = {"value_event_outcome": "Recovering/resolving", "value_patient_weight_kg": "64"}
    updated, errors = apply_edits(SUGGESTIONS, form, normalise, {"event_outcome", "patient_weight_kg"})
    assert errors == []
    assert updated == SUGGESTIONS


def test_corrected_values_replace_the_suggestion_and_are_marked_edited():
    form = {
        "value_event_outcome": "Recovered/resolved",
        "value_event_onset_date": "2026-09-03",
        "value_patient_weight_kg": "62.5 kg",
        "value_medical_history": "Hypertension since 2018",
    }
    selected = {"event_outcome", "event_onset_date", "patient_weight_kg", "medical_history"}
    updated, errors = apply_edits(SUGGESTIONS, form, normalise, selected)
    by_field = {s["field"]: s for s in updated}

    assert errors == []
    assert by_field["event_outcome"]["new_value"] == "Recovered/resolved"
    assert by_field["event_outcome"]["label"] == "Event outcome (edited)"
    assert by_field["event_onset_date"]["new_value"] == date(2026, 9, 3).isoformat()
    assert by_field["patient_weight_kg"]["new_value"] == "62.5"
    assert by_field["medical_history"]["new_value"] == (
        "Asthma\n\n[From follow-up response, 09 Oct 2026] Hypertension since 2018"
    )


def test_invalid_corrections_are_refused():
    form = {"value_patient_weight_kg": "heavy", "value_event_outcome": "Better"}
    _, errors = apply_edits(SUGGESTIONS, form, normalise, {"patient_weight_kg", "event_outcome"})
    assert len(errors) == 2
    assert "heavy" in errors[0] or "heavy" in errors[1]


def test_unticked_rows_are_left_alone():
    updated, errors = apply_edits(SUGGESTIONS, {"value_patient_weight_kg": "heavy"}, normalise, set())
    assert errors == [] and updated == SUGGESTIONS


def test_follow_up_email_text_reviewed_in_preview_is_sent(monkeypatch):
    from datetime import datetime, timezone
    from io import BytesIO

    import app.services.follow_up_automation as automation
    from app import create_app
    from config import TestingConfig

    case = {"case_id": 7, "case_number": "APDL-ICSR-26-TEST1", "reporter_email": "nurse@clinic.org",
            "reporter_name": "Amina", "received_date": date(2026, 10, 1), "workflow_status": "Triage"}
    items = [{"code": "event_outcome", "label": "Outcome of the reaction", "number": 1}]
    tasks = [{"task_id": 1, "check_code": "event_outcome", "due_date": date(2026, 10, 15)}]
    monkeypatch.setattr(automation, "load_case_request", lambda case_id: (case, {}, [], tasks))
    monkeypatch.setattr(automation, "request_items", lambda *a, **k: items)
    monkeypatch.setattr(automation, "build_follow_up_request_docx", lambda *a, **k: BytesIO(b"doc"))
    sent = []
    monkeypatch.setattr("app.services.follow_up_email.send_follow_up_email",
                        lambda app, to, subject, body, attachment, filename: sent.append((subject, body)))

    class Cursor:
        def execute(self, sql, parameters=()):
            pass

        def fetchone(self):
            return None

    class Txn:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(automation, "transaction", lambda: Txn())
    monkeypatch.setattr("app.services.case_follow_up.sync_follow_up_flags", lambda cursor, case_id, **k: 0)
    monkeypatch.setattr("app.services.case_workflow.auto_move_status", lambda *a, **k: False)

    app = create_app(TestingConfig)
    with app.app_context():
        preview = automation.preview_case_request(7, app=app, today=date(2026, 10, 9))
        assert preview["recipient"] == "nurse@clinic.org"
        assert "Outcome of the reaction" in preview["body"]
        assert "Sent on" not in preview["body"]

        result = automation.send_case_request(
            7, 1, app=app, today=date(2026, 10, 9),
            subject_override="Please help with case APDL-ICSR-26-TEST1",
            body_override="Dear Amina,\r\nCould you tell us how the patient is now?",
        )

    assert result["sent"] is True
    subject, body = sent[0]
    assert subject == "Please help with case APDL-ICSR-26-TEST1"
    assert body.startswith("Dear Amina,\nCould you tell us how the patient is now?")
    assert "\n\nSent on " in body


def _rsi_client(monkeypatch):
    from app import create_app
    from config import TestingConfig

    monkeypatch.setattr("app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0)
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 2
        user_session["full_name"] = "Charles Ameko"
        user_session["role"] = "Deputy QPPV"
    return client


def test_extracted_term_can_be_corrected_and_is_verified(monkeypatch):
    import app.rsi.routes as rsi_routes

    executed, audits, rechecked = [], [], []

    def fake_one(sql, parameters=()):
        if "WHERE reaction_id = %s" in sql and "LOWER" not in sql:
            return {"reaction_id": 5, "rsi_id": 2, "reaction_term": "Rash pruritic"}
        return None

    class Cursor:
        def execute(self, sql, parameters=()):
            executed.append((sql, parameters))

    class Txn:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rsi_routes, "query_one", fake_one)
    monkeypatch.setattr(rsi_routes, "transaction", lambda: Txn())
    monkeypatch.setattr(rsi_routes, "write_audit_log", lambda **k: audits.append(k))
    monkeypatch.setattr(rsi_routes, "_recheck_when_terms_reviewed", lambda rsi_id: rechecked.append(rsi_id))
    client = _rsi_client(monkeypatch)

    response = client.post("/reference-safety/reactions/5/edit", data={"reaction_term": "  Pruritic   rash "})

    assert response.status_code == 302
    sql, parameters = executed[0]
    assert "review_status = 'Verified'" in sql
    assert parameters == ("Pruritic rash", 2, 5)
    assert "“Rash pruritic” changed to “Pruritic rash”" in audits[0]["details"]
    assert rechecked == [2]
