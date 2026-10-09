"""Regulatory reporting clock for individual case safety reports.

Day 0 is the date the company first received the minimum case information
(``received_date``). The clock stops when the case is submitted to the
regulator (``regulatory_submitted_date``).

Default timelines follow ICH E2D post-authorisation expedited reporting:
serious cases within 15 calendar days, non-serious cases within 90 days.
National authorities can require shorter timelines, so confirm each market's
requirement and record it in ``COUNTRY_RULES``.
"""

from datetime import date


DEFAULT_RULES = {
    "serious": 15,
    "non_serious": 90,
}

# Per-market overrides, keyed by country name exactly as stored in
# pv.countries. Only list markets whose requirement differs from the default
# and has been confirmed against the national authority's guidance, e.g.
#   "Kenya": {"serious": 7, "non_serious": 90},
COUNTRY_RULES = {}

DUE_SOON_DAYS = 3

CLOCK_STOPPED_STATUSES = {"Submitted", "Closed"}


def reporting_timeline_days(seriousness, country_name=None):
    rules = {**DEFAULT_RULES, **COUNTRY_RULES.get(country_name or "", {})}
    return rules["serious"] if seriousness else rules["non_serious"]


def evaluate_reporting_clock(case, today=None):
    """Return the reporting clock for a case as a plain dictionary.

    ``state`` is one of: overdue, due_soon, on_track, submitted_on_time,
    submitted_late, closed_unsubmitted.
    """
    today = today or date.today()
    received = case.get("received_date")
    if received is None:
        return None

    timeline = reporting_timeline_days(
        case.get("seriousness"),
        case.get("country_name"),
    )
    due_date = date.fromordinal(received.toordinal() + timeline)
    submitted = case.get("regulatory_submitted_date")
    status = case.get("workflow_status")

    clock = {
        "timeline_days": timeline,
        "due_date": due_date,
        "submitted_date": submitted,
        "days_remaining": None,
        "days_late": 0,
    }

    if submitted:
        late = (submitted - due_date).days
        clock["days_late"] = max(late, 0)
        clock["state"] = "submitted_late" if late > 0 else "submitted_on_time"
        clock["label"] = (
            f"Submitted {late} day{'s' if late != 1 else ''} late"
            if late > 0
            else "Submitted on time"
        )
        return clock

    if status in CLOCK_STOPPED_STATUSES:
        clock["state"] = "closed_unsubmitted"
        clock["label"] = (
            "Submitted — date not recorded"
            if status == "Submitted"
            else "Closed — no submission recorded"
        )
        return clock

    remaining = (due_date - today).days
    clock["days_remaining"] = remaining

    if remaining < 0:
        clock["state"] = "overdue"
        clock["days_late"] = -remaining
        clock["label"] = (
            f"Overdue by {-remaining} day{'s' if remaining != -1 else ''}"
        )
    elif remaining <= DUE_SOON_DAYS:
        clock["state"] = "due_soon"
        clock["label"] = (
            "Due today"
            if remaining == 0
            else f"Due in {remaining} day{'s' if remaining != 1 else ''}"
        )
    else:
        clock["state"] = "on_track"
        clock["label"] = f"Due in {remaining} days"

    return clock


def summarise_reporting_alerts(cases, today=None, limit=5):
    """Group cases with a running clock into overdue and due-soon lists."""
    overdue, due_soon = [], []
    for case in cases:
        clock = evaluate_reporting_clock(case, today=today)
        if clock is None:
            continue
        entry = {**case, "reporting_clock": clock}
        if clock["state"] == "overdue":
            overdue.append(entry)
        elif clock["state"] == "due_soon":
            due_soon.append(entry)

    overdue.sort(key=lambda c: -c["reporting_clock"]["days_late"])
    due_soon.sort(key=lambda c: c["reporting_clock"]["days_remaining"])
    return {
        "overdue_count": len(overdue),
        "due_soon_count": len(due_soon),
        "overdue": overdue[:limit],
        "due_soon": due_soon[:limit],
    }


def get_reporting_alerts(today=None):
    """Dashboard alert data, or None if it cannot be loaded."""
    from flask import current_app

    from app.db import query_all

    try:
        cases = query_all(
            """
            SELECT
                safety_cases.case_id,
                safety_cases.case_number,
                safety_cases.received_date,
                safety_cases.seriousness,
                safety_cases.workflow_status,
                safety_cases.regulatory_submitted_date,
                countries.country_name
            FROM pv.safety_cases AS safety_cases
            LEFT JOIN pv.countries AS countries
                ON countries.country_id = safety_cases.country_id
            WHERE safety_cases.regulatory_submitted_date IS NULL
              AND safety_cases.workflow_status NOT IN %s
            """,
            (tuple(CLOCK_STOPPED_STATUSES),),
        )
    except Exception:
        current_app.logger.exception("Could not load reporting alerts")
        try:
            from app.db import get_db

            get_db().rollback()
        except Exception:
            pass
        return None
    return summarise_reporting_alerts(cases, today=today)
