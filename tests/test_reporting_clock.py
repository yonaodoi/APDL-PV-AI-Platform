from datetime import date

import pytest

from app.services import reporting_clock
from app.services.reporting_clock import evaluate_reporting_clock


TODAY = date(2026, 10, 7)


def make_case(**overrides):
    case = {
        "received_date": date(2026, 9, 16),
        "seriousness": True,
        "country_name": "Malawi",
        "workflow_status": "New",
        "regulatory_submitted_date": None,
    }
    case.update(overrides)
    return case


def test_serious_case_past_15_days_is_overdue():
    clock = evaluate_reporting_clock(make_case(), today=TODAY)

    assert clock["timeline_days"] == 15
    assert clock["due_date"] == date(2026, 10, 1)
    assert clock["state"] == "overdue"
    assert clock["days_late"] == 6
    assert clock["label"] == "Overdue by 6 days"


def test_non_serious_case_uses_90_day_timeline():
    clock = evaluate_reporting_clock(
        make_case(seriousness=False), today=TODAY
    )

    assert clock["timeline_days"] == 90
    assert clock["due_date"] == date(2026, 12, 15)
    assert clock["state"] == "on_track"


@pytest.mark.parametrize(
    "received, expected_label",
    [
        (date(2026, 9, 22), "Due today"),
        (date(2026, 9, 23), "Due in 1 day"),
        (date(2026, 9, 25), "Due in 3 days"),
    ],
)
def test_due_soon_window(received, expected_label):
    clock = evaluate_reporting_clock(
        make_case(received_date=received), today=TODAY
    )

    assert clock["state"] == "due_soon"
    assert clock["label"] == expected_label


def test_one_day_overdue_label_is_singular():
    clock = evaluate_reporting_clock(
        make_case(received_date=date(2026, 9, 21)), today=TODAY
    )

    assert clock["label"] == "Overdue by 1 day"


def test_submission_stops_the_clock_on_time():
    clock = evaluate_reporting_clock(
        make_case(
            workflow_status="Closed",
            regulatory_submitted_date=date(2026, 9, 30),
        ),
        today=TODAY,
    )

    assert clock["state"] == "submitted_on_time"
    assert clock["days_remaining"] is None


def test_late_submission_is_reported():
    clock = evaluate_reporting_clock(
        make_case(
            workflow_status="Submitted",
            regulatory_submitted_date=date(2026, 10, 4),
        ),
        today=TODAY,
    )

    assert clock["state"] == "submitted_late"
    assert clock["label"] == "Submitted 3 days late"


def test_closed_without_submission_date_stops_clock():
    clock = evaluate_reporting_clock(
        make_case(workflow_status="Closed"), today=TODAY
    )

    assert clock["state"] == "closed_unsubmitted"


def test_country_override_shortens_timeline(monkeypatch):
    monkeypatch.setattr(
        reporting_clock, "COUNTRY_RULES", {"Malawi": {"serious": 7}}
    )

    clock = evaluate_reporting_clock(make_case(), today=TODAY)

    assert clock["timeline_days"] == 7
    assert clock["due_date"] == date(2026, 9, 23)


def test_missing_received_date_returns_none():
    assert evaluate_reporting_clock(make_case(received_date=None)) is None
