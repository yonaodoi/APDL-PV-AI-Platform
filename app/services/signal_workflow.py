"""Signal evaluation: what each status needs, a suggested next step, and a
drafted assessment built from the supporting cases."""

from collections import Counter
from datetime import date

STATUSES = ("New", "Under evaluation", "Validated", "Closed")

# Fields each status needs before a signal can be moved into it.
REQUIRED_FOR = {
    "Validated": (
        ("owner_name", "Signal owner"),
        ("assessment_summary", "Assessment summary"),
    ),
    "Closed": (
        ("owner_name", "Signal owner"),
        ("assessment_summary", "Assessment summary"),
        ("decision_summary", "Decision and action"),
    ),
}

DRAFT_PLACEHOLDERS = (
    "[record the medical assessment]",
    "[validated / refuted / needs more data]",
)


def _filled(value):
    return bool((value or "").strip())


def has_draft_placeholders(text):
    return any(marker in (text or "") for marker in DRAFT_PLACEHOLDERS)


def missing_for_status(status, values):
    """Labels of what is missing before ``values`` can be saved as ``status``."""
    missing = [
        label
        for field, label in REQUIRED_FOR.get(status, ())
        if not _filled(values.get(field))
    ]
    if status in REQUIRED_FOR and has_draft_placeholders(values.get("assessment_summary")):
        missing.append("Assessment summary (replace the bracketed parts of the draft)")
    return missing


def validate_signal_evaluation(status, values):
    """Error messages that block saving; empty when allowed."""
    missing = missing_for_status(status, values)
    if not missing:
        return []
    return [
        f"Before setting the signal to {status}, record: " + ", ".join(missing) + "."
    ]


def evaluation_checks(signal, supporting_cases=()):
    """Checklist shown in the evaluation panel."""
    checks = [
        {
            "label": "Signal owner",
            "ok": _filled(signal.get("owner_name")),
            "message": signal.get("owner_name") or "Assign the person responsible.",
        },
    ]
    summary = signal.get("assessment_summary")
    if not _filled(summary):
        summary_message = "Record the medical and data assessment."
    elif has_draft_placeholders(summary):
        summary_message = "Replace the bracketed parts of the drafted assessment."
    else:
        summary_message = "Recorded."
    checks.append({
        "label": "Assessment summary",
        "ok": summary_message == "Recorded.",
        "message": summary_message,
    })
    checks.append({
        "label": "Decision and action",
        "ok": _filled(signal.get("decision_summary")),
        "message": (
            "Recorded."
            if _filled(signal.get("decision_summary"))
            else "Needed before closing: the decision and any action or monitoring plan."
        ),
    })
    unassessed = [
        c for c in supporting_cases or []
        if not c.get("listedness_status")
    ]
    if supporting_cases:
        checks.append({
            "label": "Supporting cases",
            "ok": not unassessed,
            "message": (
                f"{len(supporting_cases)} linked"
                + (
                    f"; {len(unassessed)} without a listedness assessment."
                    if unassessed
                    else "; all assessed for listedness."
                )
            ),
        })
    return checks


def suggest_signal_status(signal, supporting_cases=()):
    """Suggested next status with reasons, or None when closed."""
    current = signal.get("status")
    if current == "Closed":
        return None

    assessed = _filled(signal.get("assessment_summary")) and not has_draft_placeholders(
        signal.get("assessment_summary")
    )
    decided = _filled(signal.get("decision_summary"))
    count = len(supporting_cases or [])

    if current == "New":
        reasons = [
            f"{count} supporting case(s) linked." if count else "No supporting cases linked yet.",
            "Assign an owner and start the evaluation.",
        ]
        return {"status": "Under evaluation", "reasons": reasons, "same": False, "choices": None}

    if current == "Under evaluation":
        if not assessed:
            return {
                "status": "Under evaluation",
                "reasons": ["Record the assessment summary (a drafted one is below)."],
                "same": True,
                "choices": None,
            }
        return {
            "status": None,
            "reasons": [
                "Assessment recorded. Decide: Validated if the signal is confirmed "
                "and needs action, or Closed if it is refuted or needs no action "
                "(record the decision first)."
            ],
            "same": False,
            "choices": ("Validated", "Closed"),
        }

    # Validated
    if not decided:
        return {
            "status": "Validated",
            "reasons": ["Record the decision and the action or monitoring plan."],
            "same": True,
            "choices": None,
        }
    return {
        "status": "Closed",
        "reasons": ["Decision recorded. Close the signal once the actions are complete."],
        "same": False,
        "choices": None,
    }


def _clip(text, limit=90):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def draft_signal_assessment_note(signal, supporting_cases=(), today=None):
    """A factual starting point for the assessment summary.

    Counts and dates come from the linked cases; the medical judgement is
    left as a marked gap for the reviewer.
    """
    today = today or date.today()
    cases = list(supporting_cases or [])
    parts = [
        f"Assessment drafted {today:%d %b %Y} for {signal.get('product_name') or 'the product'}"
        f" / {signal.get('event_term') or 'the event'}"
        f" (source: {signal.get('signal_source') or 'not recorded'},"
        f" priority: {signal.get('priority') or 'not set'})."
    ]

    if not cases:
        parts.append("Supporting cases: none linked to this signal.")
    else:
        serious = sum(1 for c in cases if c.get("seriousness"))
        dates = [c["received_date"] for c in cases if c.get("received_date")]
        span = (
            f", received {min(dates):%d %b %Y} to {max(dates):%d %b %Y}"
            if dates
            else ""
        )
        parts.append(
            f"Supporting cases: {len(cases)} ({serious} serious, "
            f"{len(cases) - serious} non-serious){span}."
        )

        listedness = Counter(c.get("listedness_status") or "Not assessed" for c in cases)
        parts.append(
            "Listedness: "
            + ", ".join(f"{name} {count}" for name, count in sorted(listedness.items()))
            + "."
        )

        countries = Counter(c.get("country_name") or "Not recorded" for c in cases)
        parts.append(
            "Countries: "
            + ", ".join(f"{name} {count}" for name, count in countries.most_common(5))
            + "."
        )

        fatal = [
            c.get("case_number")
            for c in cases
            if (c.get("event_outcome") or "").lower() == "fatal"
        ]
        if fatal:
            parts.append("Fatal outcome: " + ", ".join(fatal) + ".")

        examples = "; ".join(
            f"{c.get('case_number')}: {_clip(c.get('event_description'))}"
            for c in cases[:3]
        )
        parts.append(f"Examples: {examples}")

    parts.append("Medical assessment: [record the medical assessment].")
    parts.append("Conclusion: [validated / refuted / needs more data].")
    return "\n".join(parts)
