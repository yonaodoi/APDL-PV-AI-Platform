"""Cross-field consistency checks for safety cases.

The completeness checks confirm that fields are filled in. These checks
confirm that the filled-in values agree with each other, for example that a
case with death as a seriousness criterion does not record the patient as
recovered.
"""

import re
from datetime import date


DEATH_PATTERN = re.compile(r"\b(death|died|dead|fatal|deceased)\b", re.I)
NON_FATAL_OUTCOMES = {
    "Recovered/resolved",
    "Recovering/resolving",
    "Not recovered/not resolved",
    "Recovered with sequelae",
}


def _check(code, label, problems, ok_message):
    return {
        "code": code,
        "label": label,
        "status": "Review" if problems else "Pass",
        "message": " ".join(problems) if problems else ok_message,
    }


def _fmt(value):
    return value.strftime("%d %b %Y")


def _fatal_outcome_problems(case):
    problems = []
    outcome = case.get("event_outcome") or ""
    criteria = case.get("seriousness_criteria") or ""
    criteria_mention_death = bool(DEATH_PATTERN.search(criteria))

    if outcome == "Fatal" and not case.get("seriousness"):
        problems.append(
            "The outcome is Fatal but the case is not marked serious."
        )
    if outcome == "Fatal" and case.get("seriousness") and not criteria_mention_death:
        problems.append(
            "The outcome is Fatal but death is not recorded as a "
            "seriousness criterion."
        )
    if criteria_mention_death and outcome in NON_FATAL_OUTCOMES:
        problems.append(
            f"Death is recorded as a seriousness criterion but the outcome "
            f"is {outcome}. Confirm which is correct."
        )
    return problems


def _date_sequence_problems(case, products, today):
    problems = []
    received = case.get("received_date")
    onset = case.get("event_onset_date")
    event_end = case.get("event_end_date")
    birth = case.get("patient_date_of_birth")

    if received and received > today:
        problems.append(f"The received date {_fmt(received)} is in the future.")
    if onset and received and onset > received:
        problems.append(
            f"Event onset ({_fmt(onset)}) is after the case was received "
            f"({_fmt(received)})."
        )
    if onset and event_end and event_end < onset:
        problems.append(
            f"Event end date ({_fmt(event_end)}) is before onset "
            f"({_fmt(onset)})."
        )
    if onset and birth and onset < birth:
        problems.append("Event onset is before the patient's date of birth.")

    for product in products or []:
        name = product.get("product_name") or "the suspected product"
        start = product.get("therapy_start_date")
        end = product.get("therapy_end_date")
        if start and end and end < start:
            problems.append(f"Therapy with {name} ends before it starts.")
        if start and onset and onset < start:
            problems.append(
                f"Event onset ({_fmt(onset)}) is before therapy with {name} "
                f"started ({_fmt(start)}). Confirm the temporal relationship."
            )
    return problems


def _patient_problems(case, today):
    problems = []
    birth = case.get("patient_date_of_birth")
    age = case.get("patient_age_years")
    unit = (case.get("patient_age_unit") or "Years").lower()
    reference = case.get("event_onset_date") or case.get("received_date") or today

    if birth and age is not None and unit.startswith("year"):
        derived = reference.year - birth.year - (
            (reference.month, reference.day) < (birth.month, birth.day)
        )
        if abs(derived - age) > 1:
            problems.append(
                f"Recorded age ({age} years) does not match the recorded "
                f"date of birth ({_fmt(birth)}), which gives an age of "
                f"about {derived} years at the event."
            )

    if (
        case.get("patient_sex") == "Male"
        and case.get("patient_pregnancy_status") == "Yes"
    ):
        problems.append("The patient is recorded as male and pregnant.")
    return problems


def evaluate_case_consistency(case, products, today=None):
    today = today or date.today()
    return [
        _check(
            "fatal_outcome_consistency",
            "Fatal outcome consistency",
            _fatal_outcome_problems(case),
            "Outcome and seriousness criteria agree.",
        ),
        _check(
            "date_sequence",
            "Date sequence",
            _date_sequence_problems(case, products, today),
            "Receipt, onset, and therapy dates are in a plausible order.",
        ),
        _check(
            "patient_consistency",
            "Patient details consistency",
            _patient_problems(case, today),
            "Patient age, date of birth, and sex agree.",
        ),
    ]
