"""Regulatory reporting clock for individual case safety reports.

Day 0 is the date APDL first received the minimum case information
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
