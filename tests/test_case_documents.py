from datetime import date

import app.services.case_documents as docs
from app import create_app
from config import TestingConfig


COUNTRIES = [{"country_id": 4, "country_name": "Malawi"}, {"country_id": 5, "country_name": "Uganda"}]
CASE = {
    "case_id": 9,
    "country_id": 4,
    "reporter_name": "Pharmacist A",
    "patient_sex": None,
    "patient_age_years": 45,
    "laboratory_results": "ALT normal",
    "event_outcome": "Recovering/resolving",
    "event_onset_date": date(2026, 9, 1),
}
PRODUCT = {"product_name": "ABPARA", "batch_number": None}


def test_normalise_keeps_valid_values_and_drops_invalid_ones():
    values = docs.normalise_extracted(
        {
            "country": "uganda",
            "patient_sex": "female",
            "patient_age_years": "abc",
            "event_onset_date": "2026-09-03",
            "event_end_date": "3rd Sept",
            "event_outcome": "Healed",
            "batch_number": " B123 ",
            "laboratory_results": "",
            "uncertain_fields": ["x"],
        },
        COUNTRIES,
    )
    assert values == {
        "country_id": "5",
        "patient_sex": "Female",
        "event_onset_date": "2026-09-03",
        "batch_number": "B123",
    }


def test_suggestions_fill_change_and_add_without_repeating_known_values():
    values = {
        "reporter_name": "Pharmacist A",          # same -> no suggestion
        "patient_sex": "Female",                   # empty -> fill
        "event_outcome": "Recovered/resolved",     # differs -> change
        "laboratory_results": "ALT 3x ULN on 5 Sep",  # text -> add
        "batch_number": "B123",                    # product, empty -> fill
        "country_id": "5",                         # differs -> change, shown by name
    }
    suggestions = docs.build_suggestions(
        CASE, PRODUCT, values, "follow-up response", date(2026, 10, 8), COUNTRIES
    )
    by_field = {s["field"]: s for s in suggestions}

    assert "reporter_name" not in by_field
    assert by_field["patient_sex"]["kind"] == "fill"
    assert by_field["event_outcome"]["kind"] == "change"
    assert by_field["event_outcome"]["current"] == "Recovering/resolving"
    assert by_field["laboratory_results"]["kind"] == "add"
    assert by_field["laboratory_results"]["new_value"] == (
        "ALT normal\n\n[From follow-up response, 08 Oct 2026] ALT 3x ULN on 5 Sep"
    )
    assert by_field["batch_number"]["table"] == "product"
    assert by_field["country_id"]["current"] == "Malawi"
    assert by_field["country_id"]["proposed"] == "Uganda"


def test_text_already_in_the_case_is_not_suggested_again():
    suggestions = docs.build_suggestions(
        CASE, PRODUCT, {"laboratory_results": "alt normal"}, "source report", date(2026, 10, 8)
    )
    assert suggestions == []


def test_typed_values_match_database_types():
    assert docs.typed_value("event_onset_date", "2026-09-03") == date(2026, 9, 3)
    assert docs.typed_value("patient_age_years", "45") == 45
    assert docs.typed_value("country_id", "5") == 5
    assert docs.typed_value("batch_number", "B123") == "B123"
    assert docs.typed_value("batch_number", "") is None


def test_rsi_attachment_goes_to_reference_library(monkeypatch):
    statuses = []
    monkeypatch.setattr(docs, "_set_status", lambda *a, **k: statuses.append(a))
    monkeypatch.setattr(
        docs, "add_rsi_from_attachment", lambda *a: (12, "Added to the reference library.")
    )
    app = create_app(TestingConfig)
    with app.test_request_context():
        outcome = docs.process_case_attachment(
            {"attachment_id": 3, "original_filename": "smpc.pdf", "document_type": "rsi_smpc"},
            9, "/tmp/smpc.pdf", 1,
        )
    assert outcome == ("Added to the reference library.", "success")
    assert statuses[-1][1] == docs.STATUS_RSI


def test_rsi_attachment_must_be_pdf_or_word(monkeypatch):
    monkeypatch.setattr(docs, "_set_status", lambda *a, **k: None)
    app = create_app(TestingConfig)
    with app.test_request_context():
        message, category = docs.process_case_attachment(
            {"attachment_id": 3, "original_filename": "label.png", "document_type": "rsi_innovator"},
            9, "/tmp/label.png", 1,
        )
    assert category == "error"


def test_follow_up_response_is_read_in_the_background(monkeypatch):
    statuses, threads = [], []
    monkeypatch.setattr(docs, "_set_status", lambda *a, **k: statuses.append(a))

    class FakeThread:
        def __init__(self, target, args, daemon):
            threads.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr(docs, "Thread", FakeThread)
    app = create_app(TestingConfig)
    with app.test_request_context():
        message, category = docs.process_case_attachment(
            {"attachment_id": 3, "original_filename": "reply.docx", "document_type": "follow_up"},
            9, "/tmp/reply.docx", 1,
        )
    assert statuses[-1][1] == docs.STATUS_READING
    assert threads[0][0] is docs.read_document_for_suggestions
    assert threads[0][1][1:] == (3, 9, "/tmp/reply.docx", "follow-up response")
    assert category == "info"


def test_other_documents_are_just_attached(monkeypatch):
    app = create_app(TestingConfig)
    with app.test_request_context():
        assert docs.process_case_attachment(
            {"attachment_id": 3, "original_filename": "photo.jpg", "document_type": "other"},
            9, "/tmp/photo.jpg", 1,
        ) is None


def test_apply_updates_only_ticked_fields_and_logs_them(monkeypatch):
    executed = []

    class Cursor:
        def execute(self, sql, params):
            executed.append((" ".join(sql.split()), params))

    class Tx:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(docs, "transaction", lambda: Tx())
    attachment = {
        "attachment_id": 3,
        "original_filename": "reply.docx",
        "suggested_updates": [
            {"field": "patient_sex", "table": "case", "label": "Patient sex", "kind": "fill",
             "current": "", "proposed": "Female", "new_value": "Female"},
            {"field": "event_onset_date", "table": "case", "label": "Event onset date",
             "kind": "change", "current": "2026-09-01", "proposed": "2026-09-03",
             "new_value": "2026-09-03"},
            {"field": "batch_number", "table": "product", "label": "Batch number",
             "kind": "fill", "current": "", "proposed": "B123", "new_value": "B123"},
            {"field": "case_id", "table": "case", "label": "Injected", "kind": "fill",
             "current": "", "proposed": "1", "new_value": "1"},
        ],
    }

    applied = docs.apply_suggestions(
        9, attachment, {"patient_sex", "batch_number", "case_id"}, actor_user_id=1
    )

    assert applied == ["Patient sex", "Batch number"]
    case_sql = [e for e in executed if e[0].startswith("UPDATE pv.safety_cases")][0]
    assert "patient_sex = %s" in case_sql[0]
    assert "event_onset_date" not in case_sql[0]
    assert "case_id = %s, updated_at" not in case_sql[0]
    assert case_sql[1] == ("Female", 9)
    product_sql = [e for e in executed if e[0].startswith("UPDATE pv.case_products")][0]
    assert product_sql[1] == ("B123", 9)
    audit = [e for e in executed if "case_audit_log" in e[0]][0]
    assert "Patient sex" in audit[1][2] and "Batch number" in audit[1][2]
    assert "Injected" not in audit[1][2]
    status = [e for e in executed if "record_attachments" in e[0]][0]
    assert status[1][0] == docs.STATUS_READY
    assert "2 update(s) applied; 2 suggestion(s) still to review" in status[1][1]
    remaining = [s["field"] for s in __import__("json").loads(status[1][2])]
    assert remaining == ["event_onset_date", "case_id"]


def _signed_in_client(monkeypatch):
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0
    )
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = "System Administrator"
    return client


ATTACHMENT = {
    "attachment_id": 3,
    "record_id": 9,
    "original_filename": "reply.docx",
    "document_type": "follow_up",
    "processing_status": "Suggestions ready",
    "processing_note": "2 suggested update(s) found.",
    "suggested_updates": [
        {"field": "patient_sex", "table": "case", "label": "Patient sex", "kind": "fill",
         "current": "", "proposed": "Female", "new_value": "Female"},
        {"field": "event_outcome", "table": "case", "label": "Event outcome", "kind": "change",
         "current": "Recovering/resolving", "proposed": "Recovered/resolved",
         "new_value": "Recovered/resolved"},
    ],
}


def test_review_page_lists_suggestions_with_changes_unticked(monkeypatch):
    import app.cases.routes as case_routes

    monkeypatch.setattr(
        case_routes,
        "query_one",
        lambda sql, params=(): ATTACHMENT if "record_attachments" in sql
        else {"case_id": 9, "case_number": "APDL-ICSR-26-012"},
    )
    client = _signed_in_client(monkeypatch)

    page = client.get("/cases/9/attachments/3/suggestions").get_data(as_text=True)

    assert "Suggested updates for APDL-ICSR-26-012" in page
    assert 'value="patient_sex" id="field-patient_sex" checked' in page
    assert 'value="event_outcome" id="field-event_outcome" >' in page
    assert "Changes the current value" in page


def test_applying_suggestions_updates_case_and_rechecks(monkeypatch):
    import app.cases.routes as case_routes

    calls = []
    monkeypatch.setattr(case_routes, "query_one", lambda sql, params=(): ATTACHMENT)
    monkeypatch.setattr(
        case_routes, "apply_suggestions",
        lambda case_id, attachment, selected, user: calls.append(("apply", selected)) or ["Patient sex"],
    )
    monkeypatch.setattr(case_routes, "write_audit_log", lambda **k: calls.append(("audit", k["action"])))
    monkeypatch.setattr(
        case_routes, "run_automatic_listedness",
        lambda case_id, actor_user_id=None: calls.append(("listedness",)) or None,
    )
    monkeypatch.setattr(
        case_routes, "screen_case_for_signals",
        lambda case_id, actor_user_id=None: calls.append(("screen",)) or ([], False),
    )

    class Cursor:
        def __init__(self):
            self.rows = [{"case_id": 9}]

        def execute(self, sql, params):
            calls.append(("sql", " ".join(sql.split())[:40]))

        def fetchone(self):
            return self.rows.pop(0) if self.rows else None

    class Tx:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(case_routes, "transaction", lambda: Tx())
    client = _signed_in_client(monkeypatch)

    response = client.post(
        "/cases/9/attachments/3/suggestions",
        data={"action": "apply", "field": ["patient_sex"]},
    )

    assert response.headers["Location"].endswith("/cases/9#attachments")
    assert calls[:2] == [
        ("apply", {"patient_sex"}),
        ("audit", "Case updated from attached document"),
    ]
    # A follow-up response moves the case from Follow-up requested to Medical review.
    assert any(
        c[0] == "sql" and c[1].startswith("UPDATE pv.safety_cases SET workflow")
        for c in calls
    )
    assert calls[-2:] == [("listedness",), ("screen",)]


def test_dismissing_suggestions_changes_nothing_in_the_case(monkeypatch):
    import app.cases.routes as case_routes

    executed = []

    class Cursor:
        def execute(self, sql, params):
            executed.append(" ".join(sql.split()))

    class Tx:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(case_routes, "query_one", lambda sql, params=(): ATTACHMENT)
    monkeypatch.setattr(case_routes, "transaction", lambda: Tx())
    client = _signed_in_client(monkeypatch)

    client.post("/cases/9/attachments/3/suggestions", data={"action": "dismiss"})

    assert not any("pv.safety_cases" in sql for sql in executed)
    assert any("suggested updates were dismissed" in sql for sql in executed)


def test_upload_saves_document_type_and_starts_processing(monkeypatch):
    import app.attachments.routes as attachment_routes
    from io import BytesIO

    inserted, processed = [], []

    class Cursor:
        def execute(self, sql, params):
            inserted.append(params)

        def fetchone(self):
            return {"attachment_id": 21}

    class Tx:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(attachment_routes, "query_one", lambda sql, params=(): {"case_id": 9})
    monkeypatch.setattr(attachment_routes, "transaction", lambda: Tx())
    monkeypatch.setattr(attachment_routes, "write_audit_log", lambda **k: None)
    monkeypatch.setattr(
        attachment_routes, "process_case_attachment",
        lambda attachment, case_id, path, user: processed.append((attachment, case_id)) or ("Reading.", "info"),
    )
    client = _signed_in_client(monkeypatch)
    app = client.application
    import tempfile, pathlib
    app.config["UPLOAD_ROOT"] = pathlib.Path(tempfile.mkdtemp())

    client.post(
        "/records/case/9/attachments",
        data={
            "document_type": "follow_up",
            "return_to": "/cases/9#attachments",
            "attachment": (BytesIO(b"Patient recovered."), "reply.txt"),
        },
        content_type="multipart/form-data",
    )

    assert inserted[0][-1] == "follow_up"
    assert processed[0][0]["document_type"] == "follow_up"
    assert processed[0][0]["attachment_id"] == 21
    assert processed[0][1] == 9


def test_product_details_from_a_different_product_start_unticked():
    values = {"product_name": "oral antihistamine", "route": "oral", "patient_sex": "Female"}
    mismatch = docs.product_mismatch({"product_name": "fffff", "generic_name": None}, values)
    suggestions = docs.build_suggestions(
        CASE, {"product_name": "fffff"}, values, "follow-up response", date(2026, 10, 8),
        mismatched_product=mismatch,
    )
    by_field = {s["field"]: s for s in suggestions}

    assert mismatch == "oral antihistamine"
    assert "oral antihistamine" in by_field["route"]["check_reason"]
    assert by_field["product_name"]["check_reason"]
    assert by_field["patient_sex"]["check_reason"] is None


def test_same_product_named_differently_is_not_a_mismatch():
    product = {"product_name": "ABPARA", "generic_name": "paracetamol"}
    assert docs.product_mismatch(product, {"product_name": "Abpara 500 mg tablets"}) is None
    assert docs.product_mismatch(product, {"product_name": "Paracetamol"}) is None
    assert docs.product_mismatch(product, {}) is None


def test_values_the_ai_was_unsure_about_are_flagged():
    suggestions = docs.build_suggestions(
        CASE, PRODUCT, {"patient_sex": "Female", "country_id": "5"}, "source report",
        date(2026, 10, 8), COUNTRIES, uncertain=["patient_sex", "country"],
    )
    assert all(s["check_reason"] == "The AI marked this value as uncertain." for s in suggestions)


def test_weight_with_units_becomes_a_number():
    assert docs.weight_in_kg("62 kg") == "62"
    assert docs.weight_in_kg("62.5kg") == "62.5"
    assert docs.weight_in_kg("62,5 kg") == "62.5"
    assert docs.weight_in_kg("about sixty") is None
    assert docs.normalise_extracted({"patient_weight_kg": "62 kg"}, COUNTRIES) == {"patient_weight_kg": "62"}
    assert docs.typed_value("patient_weight_kg", "62 kg") == "62"


def test_apply_failure_shows_message_and_changes_nothing(monkeypatch):
    import app.cases.routes as case_routes

    def broken(*a):
        raise ValueError("invalid input syntax for type numeric")

    monkeypatch.setattr(case_routes, "query_one", lambda sql, params=(): ATTACHMENT)
    monkeypatch.setattr(case_routes, "apply_suggestions", broken)
    client = _signed_in_client(monkeypatch)

    response = client.post(
        "/cases/9/attachments/3/suggestions",
        data={"action": "apply", "field": ["patient_sex"]},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/cases/9/attachments/3/suggestions")
