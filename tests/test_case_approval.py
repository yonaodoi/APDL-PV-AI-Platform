from datetime import date, datetime, timezone


import app.cases.review_routes as review_routes
import app.services.case_approval as approval
from app import create_app
from app.services.approval_settings import DEFAULTS, clean_emails, normalise
from config import TestingConfig

SETTINGS = normalise({})
HISTORY_SENT_BY_5 = [{"step": "Sent for review", "decided_by": 5}]
HISTORY_REVIEWED_BY_7 = HISTORY_SENT_BY_5 + [{"step": "Reviewed", "decided_by": 7}]


def check(stage, action, user, role, history=(), designated=False, settings=SETTINGS):
    approval.check_step(stage, action, user, role, designated, list(history), settings)


def raises(*args, **kwargs):
    try:
        check(*args, **kwargs)
    except approval.ApprovalError as error:
        return str(error)
    return None


def test_full_chain_with_three_different_people():
    assert raises(None, "send", 5, "PV Officer") is None
    assert raises("Pending review", "review", 7, "QPPV", HISTORY_SENT_BY_5) is None
    assert raises("Pending approval", "approve", 9, "Group Head RA & Quality", HISTORY_REVIEWED_BY_7) is None


def test_roles_are_enforced():
    assert "can do the QPPV review" in raises("Pending review", "review", 7, "PV Officer", HISTORY_SENT_BY_5)
    assert "Case approval" in raises("Pending approval", "approve", 9, "QPPV", HISTORY_REVIEWED_BY_7)
    # The designated QPPV can review whatever their system role.
    assert raises("Pending review", "review", 7, "System Administrator", HISTORY_SENT_BY_5, designated=True) is None


def test_nobody_signs_off_their_own_work():
    assert "someone else must review" in raises("Pending review", "review", 5, "QPPV", HISTORY_SENT_BY_5)
    assert "someone else must approve" in raises(
        "Pending approval", "approve", 7, "Group Head RA & Quality", HISTORY_REVIEWED_BY_7
    )


def test_steps_only_happen_at_the_right_stage():
    assert "not waiting for review" in raises("Pending approval", "review", 7, "QPPV")
    assert "already waiting" in raises("Pending review", "send", 5, "PV Officer")
    assert "already approved" in raises("Approved", "send", 5, "PV Officer")
    assert raises("Returned", "send", 5, "PV Officer") is None


def test_administrator_can_change_who_reviews_and_approves():
    custom = normalise({
        "review_title": "Medical review",
        "reviewer_roles": ["Medical Reviewer"],
        "approver_roles": ["QPPV"],
    })
    assert raises("Pending review", "review", 7, "Medical Reviewer", HISTORY_SENT_BY_5, settings=custom) is None
    assert "Medical review" in raises("Pending review", "review", 7, "QPPV", HISTORY_SENT_BY_5, settings=custom)
    assert raises("Pending approval", "approve", 9, "QPPV", HISTORY_REVIEWED_BY_7, settings=custom) is None


def test_settings_are_cleaned():
    settings = normalise({
        "review_title": "  ",
        "reviewer_roles": [],
        "review_extra_emails": "A@x.com; b@y.org,\nnot-an-email, a@x.com",
        "digest_hour": "27",
        "urgent_days": "abc",
    })
    assert settings["review_title"] == DEFAULTS["review_title"]
    assert settings["reviewer_roles"] == DEFAULTS["reviewer_roles"]
    assert settings["review_extra_emails"] == ["a@x.com", "b@y.org"]
    assert settings["digest_hour"] == 23
    assert settings["urgent_days"] == DEFAULTS["urgent_days"]
    assert clean_emails(["x@y.org", "bad", "keith@abacuspharma.com,"]) == ["x@y.org", "keith@abacuspharma.com"]


def test_daily_summary_lists_cases_and_link():
    cases = [
        {"case_number": "26-001", "seriousness": True, "product_name": "ABPARA",
         "approval_updated_at": datetime(2026, 10, 7, 9, tzinfo=timezone.utc)},
        {"case_number": "26-002", "seriousness": False, "product_name": None, "approval_updated_at": None},
    ]
    body = approval.digest_body("Yona", approval.PENDING_REVIEW, cases, "http://x/cases/approvals", SETTINGS)

    assert body.startswith("Dear Yona,")
    assert "2 safety case(s) are waiting for your QPPV review" in body
    assert "26-001 (Serious, ABPARA, waiting since 07 Oct 2026)" in body
    assert "26-002 (Non-serious, product not recorded)" in body
    assert "http://x/cases/approvals" in body


def _client(monkeypatch, role="QPPV", user_id=7):
    monkeypatch.setattr(
        "app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0
    )
    monkeypatch.setattr("app.services.case_workflow.is_designated_qppv", lambda user_id: False)
    monkeypatch.setattr("app.services.approval_settings.load_settings", lambda: dict(SETTINGS))
    app = create_app(TestingConfig)
    client = app.test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = user_id
        user_session["full_name"] = "Test User"
        user_session["role"] = role
    return client


def test_approvals_page_shows_review_actions_to_the_qppv(monkeypatch):
    row = {"case_id": 3, "case_number": "APDL-ICSR-26-003", "seriousness": True,
           "product_name": "ABPARA", "country_name": "Uganda", "approval_updated_at": None,
           "last_by": "PV Officer A", "last_comment": None}
    monkeypatch.setattr(approval, "cases_at_stage", lambda stage: [row] if stage == "Pending review" else [])
    monkeypatch.setattr(approval, "recently_decided", lambda step: [])
    client = _client(monkeypatch)

    page = client.get("/cases/approvals?tab=review").get_data(as_text=True)

    assert "APDL-ICSR-26-003" in page
    assert "Reviewed – send for Case approval" in page
    assert "Return to PV officer" in page
    assert "To review <span>1</span>" in page


def test_approvals_page_hides_actions_from_other_roles(monkeypatch):
    row = {"case_id": 3, "case_number": "APDL-ICSR-26-003", "seriousness": False,
           "product_name": None, "country_name": None, "approval_updated_at": None,
           "last_by": None, "last_comment": None}
    monkeypatch.setattr(approval, "cases_at_stage", lambda stage: [row] if stage == "Pending approval" else [])
    monkeypatch.setattr(approval, "recently_decided", lambda step: [])
    client = _client(monkeypatch, role="PV Officer")

    page = client.get("/cases/approvals?tab=approval").get_data(as_text=True)

    assert "APDL-ICSR-26-003" in page
    assert ">Approve<" not in page
    assert "can give the Case approval" in page


def test_sign_off_route_records_the_step_and_tells_the_officer(monkeypatch):
    client = _client(monkeypatch, role="Group Head RA & Quality", user_id=9)
    calls, told = [], []
    monkeypatch.setattr(
        approval, "take_step",
        lambda case_id, action, user, role, designated, comment, override_reason="": calls.append((case_id, action, user, role, comment)) or "Approved",
    )
    monkeypatch.setattr(approval, "notify_officer", lambda app, case_id, stage, comment, name: told.append(stage))

    response = client.post("/cases/3/sign-off", data={"action": "approve", "comment": "Fine"})

    assert response.status_code == 302
    assert calls == [(3, "approve", 9, "Group Head RA & Quality", "Fine")]
    assert told == ["Approved"]


def test_case_cannot_be_marked_submitted_before_approval(monkeypatch):
    client = _client(monkeypatch, role="PV Officer", user_id=5)
    case = {"case_id": 3, "case_number": "26-003", "received_date": date(2026, 10, 1),
            "workflow_status": "Ready for submission", "regulatory_submitted_date": None,
            "approval_stage": "Pending approval"}
    monkeypatch.setattr(review_routes, "query_one", lambda sql, parameters=(): case)
    updates = []
    monkeypatch.setattr(review_routes, "transaction", lambda: updates.append(1))

    response = client.post("/cases/3/mark-submitted", data={"submitted_on": "2026-10-05"})

    assert response.status_code == 302
    assert updates == []


def test_admin_can_open_sign_off_settings(monkeypatch):
    import app.administration.routes as admin_routes

    client = _client(monkeypatch, role="System Administrator", user_id=1)
    people = [
        {"user_id": 2, "full_name": "Yona Odoi", "email": "yona@example.com", "role_name": "QPPV", "is_designated_qppv": True},
        {"user_id": 3, "full_name": "Keith Aruho", "email": "", "role_name": "Group Head RA & Quality", "is_designated_qppv": False},
        {"user_id": 4, "full_name": "Agnes PV", "email": "", "role_name": "PV Officer", "is_designated_qppv": False},
    ]
    monkeypatch.setattr(
        admin_routes, "query_all",
        lambda sql, parameters=(): [{"role_name": r} for r in ("Group Head RA & Quality", "PV Officer", "QPPV")]
        if "FROM pv.roles" in sql else people,
    )

    page = client.get("/administration/sign-off-settings").get_data(as_text=True)

    assert "Case sign-off settings" in page
    assert 'name="reviewer_roles" value="QPPV" checked' in page
    assert 'name="approver_roles" value="Group Head RA &amp; Quality" checked' in page
    assert "Yona Odoi (QPPV)" in page
    assert 'name="email_2" value="yona@example.com"' in page
    assert 'name="email_4" value=""' in page
    assert "Agnes PV (PV Officer)" in page
    assert 'name="officer_roles" value="PV Officer" checked' in page
    assert 'name="notify_officer" checked' in page
    assert "no email" in page


def test_a_role_cannot_both_review_and_approve(monkeypatch):
    import app.administration.routes as admin_routes

    client = _client(monkeypatch, role="System Administrator", user_id=1)
    monkeypatch.setattr(admin_routes, "query_all", lambda sql, parameters=(): [{"role_name": "QPPV"}])
    saved = []
    monkeypatch.setattr("app.services.approval_settings.save_settings", lambda values, user: saved.append(values))

    client.post("/administration/sign-off-settings", data={
        "review_title": "Review", "approval_title": "Approval",
        "reviewer_roles": "QPPV", "approver_roles": "QPPV",
    })

    assert saved == []


def _settings_post(monkeypatch, people, form):
    import app.administration.routes as admin_routes

    client = _client(monkeypatch, role="System Administrator", user_id=1)
    monkeypatch.setattr(
        admin_routes, "query_all",
        lambda sql, parameters=(): [{"role_name": r} for r in ("Group Head RA & Quality", "PV Officer", "QPPV")]
        if "FROM pv.roles" in sql else people,
    )
    monkeypatch.setattr(admin_routes, "query_one", lambda sql, parameters=(): None)
    saved, updates, audits = [], [], []
    monkeypatch.setattr("app.services.approval_settings.save_settings",
                        lambda values, user: saved.append(values) or normalise(values))

    class Cursor:
        def execute(self, sql, parameters=()):
            updates.append(parameters)

    class Txn:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(admin_routes, "transaction", lambda: Txn())
    monkeypatch.setattr(admin_routes, "write_audit_log", lambda **kw: audits.append(kw))
    data = {"review_title": "QPPV review", "approval_title": "Case approval",
            "reviewer_roles": "QPPV", "approver_roles": "Group Head RA & Quality"}
    data.update(form)
    response = client.post("/administration/sign-off-settings", data=data)
    return client, response, saved, updates, audits


PEOPLE = [
    {"user_id": 4, "full_name": "Agnes PV", "email": "", "role_name": "PV Officer", "is_designated_qppv": False},
]


def test_saving_settings_closes_the_page_and_confirms(monkeypatch):
    client, response, saved, updates, audits = _settings_post(monkeypatch, PEOPLE, {
        "officer_roles": "PV Officer", "notify_officer": "on",
        "officer_extra_emails": "pv.box@example.com",
    })

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/administration/")
    assert saved[0]["officer_extra_emails"] == "pv.box@example.com"
    assert saved[0]["notify_officer"] is True
    with client.session_transaction() as user_session:
        flashes = user_session.get("_flashes", [])
    assert ("success", "Sign-off settings saved (1 change).") in flashes or any(
        c == "success" and m.startswith("Sign-off settings saved") for c, m in flashes
    )


def test_pv_officer_email_can_be_added_on_the_settings_page(monkeypatch):
    client, response, saved, updates, audits = _settings_post(monkeypatch, PEOPLE, {
        "officer_roles": "PV Officer", "email_4": "Agnes@Example.com",
    })

    assert response.status_code == 302
    assert updates == [("agnes@example.com", 4)]
    assert any(a["record_type"] == "user" and a["record_id"] == 4 for a in audits)


def test_bad_email_saves_nothing_and_stays_on_the_page(monkeypatch):
    client, response, saved, updates, audits = _settings_post(monkeypatch, PEOPLE, {
        "officer_roles": "PV Officer", "email_4": "not an email",
    })

    assert response.headers["Location"].endswith("/administration/sign-off-settings")
    assert saved == [] and updates == []
    with client.session_transaction() as user_session:
        flashes = user_session.get("_flashes", [])
    assert any(c == "error" and "Agnes PV" in m and "Nothing was saved" in m for c, m in flashes)


def test_returned_case_emails_officer_and_extra_addresses(monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.follow_up_automation.email_configured", lambda app=None: True)
    monkeypatch.setattr(approval, "query_one", lambda sql, parameters=(): {
        "case_number": "26-003", "full_name": "Agnes PV", "email": "agnes@example.com"})
    monkeypatch.setattr(approval, "_send", lambda app, to, subject, body: sent.append((to, subject, body)))
    app = create_app(TestingConfig)
    settings = normalise({"officer_extra_emails": "pv.box@example.com"})

    assert approval.notify_officer(app, 3, approval.RETURNED, "Fix dates", "Yona", settings) is True
    assert [s[0] for s in sent] == ["agnes@example.com", "pv.box@example.com"]
    assert "Fix dates" in sent[0][2] and sent[0][2].startswith("Dear Agnes PV,")

    sent.clear()
    quiet = normalise({"notify_officer": False, "officer_extra_emails": "pv.box@example.com"})
    approval.notify_officer(app, 3, approval.APPROVED, "", "Keith", quiet)
    assert [s[0] for s in sent] == ["pv.box@example.com"]


def _take_step_db(monkeypatch, case, blockers_inputs, can_override_role=False):
    import app.services.case_workflow as workflow

    executed = []

    class Cursor:
        def execute(self, sql, parameters=()):
            executed.append((sql, parameters))

    class Txn:
        def __enter__(self):
            return Cursor()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(approval, "query_one", lambda sql, parameters=(): case)
    monkeypatch.setattr(approval, "approval_history", lambda case_id: [])
    monkeypatch.setattr(approval, "transaction", lambda: Txn())
    monkeypatch.setattr(approval, "_settings", lambda settings=None: SETTINGS)
    monkeypatch.setattr(workflow, "load_workflow_inputs", lambda case_id: blockers_inputs)
    return executed


READY = ([{"label": "x", "status": "Pass"}], {"assessment_source": "reviewer", "listedness_status": "Listed"}, {})
NOT_READY = ([{"label": "Onset date", "status": "Review"}], None, {})


def test_sending_for_review_moves_the_case_to_qppv_review(monkeypatch):
    case = {"case_id": 3, "case_number": "26-003", "approval_stage": None, "workflow_status": "Assessment",
            "regulatory_submitted_date": None, "causality_assessment": "Possible"}
    executed = _take_step_db(monkeypatch, case, READY)

    assert approval.take_step(3, "send", 5, "PV Officer") == "Pending review"
    update = executed[0]
    assert "workflow_status = %s" in update[0]
    assert update[1] == ("Pending review", "QPPV review", 3)


def test_only_assessment_cases_can_be_sent(monkeypatch):
    case = {"case_id": 3, "case_number": "26-003", "approval_stage": None, "workflow_status": "Triage",
            "regulatory_submitted_date": None, "causality_assessment": "Possible"}
    _take_step_db(monkeypatch, case, READY)
    try:
        approval.take_step(3, "send", 5, "PV Officer")
    except approval.ApprovalError as error:
        assert "Only a case at Assessment" in str(error)
    else:
        raise AssertionError("expected an error")


def test_not_ready_case_needs_an_override_with_reason(monkeypatch):
    case = {"case_id": 3, "case_number": "26-003", "approval_stage": None, "workflow_status": "Assessment",
            "regulatory_submitted_date": None, "causality_assessment": ""}
    executed = _take_step_db(monkeypatch, case, NOT_READY)
    for role, reason in (("PV Officer", "deadline"), ("QPPV", "")):
        try:
            approval.take_step(3, "send", 5, role, False, "", reason)
        except approval.ApprovalError as error:
            assert "Not ready to send for review" in str(error)
        else:
            raise AssertionError("expected an error")
    assert executed == []

    assert approval.take_step(3, "send", 5, "QPPV", False, "", "Deadline today") == "Pending review"
    audit = [e for e in executed if "case_audit_log" in e[0]][0]
    assert "Readiness check overridden. Reason: Deadline today" in audit[1][2]


def test_returning_a_case_puts_it_back_at_assessment(monkeypatch):
    case = {"case_id": 3, "case_number": "26-003", "approval_stage": "Pending approval",
            "workflow_status": "Case approval", "regulatory_submitted_date": None}
    executed = _take_step_db(monkeypatch, case, READY)

    assert approval.take_step(3, "return_approval", 9, "Group Head RA & Quality", False, "Fix dates") == "Returned"
    assert executed[0][1] == ("Returned", "Assessment", 3)
