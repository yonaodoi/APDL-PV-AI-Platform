from datetime import date

import app.services.case_workflow as workflow
from app import create_app
from config import TestingConfig

PASS = {"label": "Identifiable patient", "status": "Pass"}
REVIEW = {"label": "Event onset date", "status": "Review"}
CONFIRMED = {"assessment_source": "reviewer", "listedness_status": "Listed"}
AUTOMATIC = {"assessment_source": "automatic", "listedness_status": "Not listed"}


def _case(**changes):
    case = {
        "workflow_status": "New",
        "regulatory_submitted_date": None,
        "causality_assessment": "Possible",
    }
    case.update(changes)
    return case


def test_ready_only_when_checklist_listedness_and_causality_are_done():
    assert workflow.readiness_blockers([PASS], CONFIRMED, "Possible") == []
    blockers = workflow.readiness_blockers([PASS, REVIEW], AUTOMATIC, "")
    assert blockers == [
        "1 completeness item(s) still need review: Event onset date.",
        "Listedness is an automatic result; a reviewer must confirm it.",
        "No causality assessment is recorded.",
    ]
    assert workflow.readiness_blockers([], None, "Possible") == [
        "Listedness has not been assessed."
    ]
    assert workflow.readiness_blockers(
        [], {"assessment_source": "reviewer", "listedness_status": "Insufficient information"}, "Possible"
    ) == ["Listedness could not be assessed (insufficient information)."]


def test_suggestions_follow_the_case_through_its_steps():
    assert workflow.suggest_status(_case(), [REVIEW], None, 1, False)["status"] == "Triage"
    assert workflow.suggest_status(_case(), [REVIEW], None, 1, True)["status"] == "Follow-up requested"
    assert workflow.suggest_status(_case(), [PASS], AUTOMATIC, 0, False)["status"] == "Medical review"
    ready = workflow.suggest_status(_case(), [PASS], CONFIRMED, 0, False)
    assert ready["status"] == "Ready for submission"
    assert ready["same"] is False
    submitted = workflow.suggest_status(
        _case(regulatory_submitted_date=date(2026, 10, 1)), [REVIEW], None, 0, False
    )
    assert submitted["status"] == "Submitted"
    assert "01 Oct 2026" in submitted["reasons"][0]


def test_no_suggestion_for_closed_and_no_going_back_after_submission():
    assert workflow.suggest_status(_case(workflow_status="Closed"), [], None, 0, False) is None
    back = workflow.suggest_status(_case(workflow_status="Submitted"), [REVIEW], None, 0, False)
    assert back["same"] is True


def test_suggestion_matching_current_status_is_marked_same():
    result = workflow.suggest_status(_case(workflow_status="Triage"), [REVIEW], None, 1, False)
    assert result["same"] is True


def test_override_rules():
    assert workflow.needs_override("Ready for submission", ["x"]) is True
    assert workflow.needs_override("Medical review", ["x"]) is False
    assert workflow.needs_override("Submitted", []) is False
    assert workflow.can_override("Medical Reviewer") is True
    assert workflow.can_override("Regulatory Affairs Officer") is False
    assert workflow.can_override("Regulatory Affairs Officer", designated_qppv=True) is True


class _Cursor:
    def __init__(self, fetch):
        self.fetch = list(fetch)
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.fetch.pop(0) if self.fetch else None


def test_auto_move_only_from_listed_statuses_and_logs_it():
    cursor = _Cursor([{"case_id": 9}])
    assert workflow.auto_move_status(
        cursor, 9, ("Follow-up requested",), "Medical review", "all tasks done.", 1
    ) is True
    assert cursor.executed[0][1] == ("Medical review", 9, ["Follow-up requested"])
    assert cursor.executed[1][1][1:3] == (
        "Status changed automatically",
        "Status moved to Medical review: all tasks done.",
    )

    cursor = _Cursor([None])
    assert workflow.auto_move_status(cursor, 9, ("Triage",), "Medical review", "x", 1) is False
    assert len(cursor.executed) == 1


def test_follow_up_flags_follow_open_tasks():
    from app.services.case_follow_up import sync_follow_up_flags

    cursor = _Cursor([{"open_tasks": 2, "earliest_due": date(2026, 10, 14)}])
    assert sync_follow_up_flags(cursor, 9) == 2
    assert "follow_up_required = TRUE" in cursor.executed[1][0]
    assert cursor.executed[1][1][0] == date(2026, 10, 14)

    cursor = _Cursor([{"open_tasks": 0, "earliest_due": None}])
    sync_follow_up_flags(cursor, 9, tasks_just_completed=1)
    assert "follow_up_required = FALSE" in cursor.executed[1][0]

    cursor = _Cursor([{"open_tasks": 0, "earliest_due": None}])
    sync_follow_up_flags(cursor, 9, tasks_just_completed=0)
    assert len(cursor.executed) == 1  # a hand-ticked follow-up is left alone


def _client(monkeypatch, role="Regulatory Affairs Officer"):
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0
    )
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 1
        user_session["full_name"] = "Test User"
        user_session["role"] = role
    return client


def _patch_review(monkeypatch, blockers_inputs):
    import app.cases.review_routes as review_routes

    saved = []
    monkeypatch.setattr(
        review_routes,
        "query_one",
        lambda sql, params=(): {
            "case_id": 9,
            "case_number": "APDL-ICSR-26-016",
            "received_date": date(2026, 9, 8),
            "regulatory_submitted_date": None,
            "workflow_status": "Medical review",
        },
    )
    monkeypatch.setattr(workflow, "load_workflow_inputs", lambda case_id: blockers_inputs)
    monkeypatch.setattr(workflow, "is_designated_qppv", lambda user_id: False)

    class Tx:
        def __enter__(self):
            return _Cursor([])

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(review_routes, "transaction", lambda: (saved.append(1), Tx())[1])
    monkeypatch.setattr(review_routes, "query_all", lambda sql, params=(): [])
    monkeypatch.setattr(review_routes, "refresh_case_completeness", lambda case, products: [])
    audits = []
    monkeypatch.setattr(review_routes, "write_audit_log", lambda **k: audits.append(k))
    return saved, audits


def test_not_ready_case_cannot_be_marked_ready(monkeypatch):
    saved, _ = _patch_review(monkeypatch, ([REVIEW], AUTOMATIC, {}))
    client = _client(monkeypatch)

    response = client.post(
        "/cases/9/review",
        data={"workflow_status": "Ready for submission", "causality_assessment": "Possible"},
    )

    assert response.status_code == 302
    assert saved == []


def test_officer_cannot_override(monkeypatch):
    saved, _ = _patch_review(monkeypatch, ([REVIEW], AUTOMATIC, {}))
    client = _client(monkeypatch)

    client.post(
        "/cases/9/review",
        data={
            "workflow_status": "Ready for submission",
            "causality_assessment": "Possible",
            "override": "on",
            "override_reason": "Deadline today",
        },
    )

    assert saved == []


def test_medical_reviewer_can_override_with_reason_and_it_is_audited(monkeypatch):
    saved, audits = _patch_review(monkeypatch, ([REVIEW], AUTOMATIC, {}))
    client = _client(monkeypatch, role="Medical Reviewer")

    client.post(
        "/cases/9/review",
        data={
            "workflow_status": "Ready for submission",
            "causality_assessment": "Possible",
            "override": "on",
            "override_reason": "Regulator deadline today; follow-up continues",
        },
    )

    assert saved
    assert audits[0]["action"] == "Readiness check overridden"
    assert "Regulator deadline today" in audits[0]["details"]


def test_override_without_reason_is_refused(monkeypatch):
    saved, _ = _patch_review(monkeypatch, ([REVIEW], AUTOMATIC, {}))
    client = _client(monkeypatch, role="Medical Reviewer")

    client.post(
        "/cases/9/review",
        data={"workflow_status": "Submitted", "causality_assessment": "Possible", "override": "on"},
    )

    assert saved == []


def test_ready_case_saves_without_override(monkeypatch):
    saved, audits = _patch_review(monkeypatch, ([PASS], CONFIRMED, {}))
    client = _client(monkeypatch)

    client.post(
        "/cases/9/review",
        data={"workflow_status": "Ready for submission", "causality_assessment": "Possible"},
    )

    assert saved
    assert audits == []


def test_unreadable_email_log_only_hides_request_sent(monkeypatch):
    def denied(sql, params=()):
        raise RuntimeError("permission denied for table case_follow_up_email_deliveries")

    monkeypatch.setattr(workflow, "query_one", denied)
    monkeypatch.setattr("app.db.get_db", lambda: type("C", (), {"rollback": lambda self: None})())
    app = create_app(TestingConfig)
    with app.app_context():
        assert workflow.follow_up_request_sent(9) is False


def test_review_note_is_drafted_from_the_case():
    note = workflow.draft_review_note(
        _case(causality_assessment="Possible"),
        [PASS, REVIEW],
        CONFIRMED | {"expectedness_status": "Expected"},
        3,
        date(2026, 10, 15),
        False,
        {"status": "Triage", "same": False},
        today=date(2026, 10, 8),
    )
    assert note == (
        "Review on 08 Oct 2026. 1 checklist item(s) need review: Event onset date. "
        "Listedness: Listed / Expected (confirmed by reviewer). Causality: Possible. "
        "Follow-up: 3 open task(s), earliest due 15 Oct 2026; no request sent yet. "
        "Next step: Triage."
    )


def test_review_note_for_a_case_with_nothing_outstanding():
    note = workflow.draft_review_note(
        _case(causality_assessment=""), [PASS], None, 0, None, False,
        {"status": "New", "same": True}, today=date(2026, 10, 8),
    )
    assert "Completeness checklist passed." in note
    assert "Listedness: not yet assessed." in note
    assert "Causality: not yet assessed." in note
    assert "Follow-up: none outstanding." in note
    assert note.endswith("Status New is appropriate.")


def test_saved_review_returns_to_panel_with_confirmation(monkeypatch):
    _patch_review(monkeypatch, ([PASS], CONFIRMED, {}))
    client = _client(monkeypatch)

    response = client.post(
        "/cases/9/review",
        data={"workflow_status": "Medical review", "causality_assessment": "Possible"},
    )

    assert response.headers["Location"].endswith("/cases/9?workflow_saved=review#workflow")


def _patch_submit(monkeypatch, status="Ready for submission"):
    import app.cases.review_routes as review_routes

    executed, audits = [], []
    monkeypatch.setattr(
        review_routes,
        "query_one",
        lambda sql, params=(): {
            "case_id": 9,
            "case_number": "APDL-ICSR-26-016",
            "received_date": date(2026, 9, 8),
            "workflow_status": status,
            "regulatory_submitted_date": None,
            "approval_stage": "Approved",
        },
    )

    class Tx:
        def __enter__(self):
            cursor = _Cursor([])
            executed.append(cursor)
            return cursor

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(review_routes, "transaction", lambda: Tx())
    monkeypatch.setattr(review_routes, "write_audit_log", lambda **k: audits.append(k))
    return executed, audits


def test_mark_submitted_records_date_and_status(monkeypatch):
    executed, audits = _patch_submit(monkeypatch)
    client = _client(monkeypatch)

    response = client.post("/cases/9/mark-submitted", data={"submitted_on": "2026-10-07"})

    update = executed[0].executed[0]
    assert "workflow_status = 'Submitted'" in update[0]
    assert update[1] == (date(2026, 10, 7), 9)
    assert audits[0]["action"] == "Case submitted to regulator"
    assert response.headers["Location"].endswith("workflow_saved=submitted#workflow")


def test_mark_submitted_rejects_future_and_early_dates(monkeypatch):
    executed, _ = _patch_submit(monkeypatch)
    client = _client(monkeypatch)

    client.post("/cases/9/mark-submitted", data={"submitted_on": "2999-01-01"})
    client.post("/cases/9/mark-submitted", data={"submitted_on": "2026-09-01"})
    client.post("/cases/9/mark-submitted", data={"submitted_on": "not a date"})

    assert executed == []


def test_only_ready_cases_can_be_marked_submitted(monkeypatch):
    executed, _ = _patch_submit(monkeypatch, status="Triage")
    client = _client(monkeypatch)

    client.post("/cases/9/mark-submitted", data={"submitted_on": "2026-10-07"})

    assert executed == []
