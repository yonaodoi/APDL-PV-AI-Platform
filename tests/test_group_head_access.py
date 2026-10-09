"""The Group Head RA & Quality can use every module except administration."""

import app.cases.routes as case_routes
import app.rsi.routes as rsi_routes
from app.services import case_workflow, psur_rules

GROUP_HEAD = "Group Head RA & Quality"


def test_group_head_can_use_reference_safety_and_listedness():
    assert GROUP_HEAD in rsi_routes.REVIEWER_ROLES
    assert GROUP_HEAD in case_routes.LISTEDNESS_ROLES


def test_group_head_can_override_workflow_and_approve_psurs():
    assert GROUP_HEAD in case_workflow.OVERRIDE_ROLES
    assert GROUP_HEAD in psur_rules.APPROVER_ROLES


def test_group_head_cannot_open_user_administration(monkeypatch):
    from app import create_app
    from config import TestingConfig

    monkeypatch.setattr("app.services.case_follow_up_reminders.get_open_reminder_count", lambda: 0)
    client = create_app(TestingConfig).test_client()
    with client.session_transaction() as user_session:
        user_session["user_id"] = 9
        user_session["full_name"] = "Keith Aruho"
        user_session["role"] = GROUP_HEAD

    for path in ("/administration/", "/administration/sign-off-settings"):
        response = client.get(path)
        assert response.status_code in (302, 403)
