"""Automated capture of potential safety signals from safety cases.

A case is screened when it is created, edited, or assessed against the
reference safety information, and on demand for all cases. Screening works
per suspected product and per individual event term, and creates or updates
a potential signal when any trigger below is met:

* Case cluster: at least MINIMUM_CASES cases with the same product and event
  term received within WINDOW_DAYS of each other.
* Fatal case: the outcome is Fatal or death is a seriousness criterion.
* Serious unlisted case: a serious case whose event was assessed as
  "Not listed" in the reference safety information.

Every system-detected signal starts with status "New" and requires QPPV
validation. If an open signal (manual or automated) already covers the same
product and event, the case is linked to it instead of creating a new one.
"""

import re
from datetime import timedelta

from flask import current_app

from app.db import query_all, query_one, transaction


WINDOW_DAYS = 90
MINIMUM_CASES = 2

OPEN_SIGNAL_STATUSES = ("New", "Under evaluation", "Validated")
PRIORITY_RANK = {"Low": 0, "Medium": 1, "High": 2, "Critical": 3}
NOTIFIED_ROLES = ("QPPV", "Deputy QPPV")

# Placeholder text that is not an event.
IGNORED_TERMS = {
    "n a", "na", "nil", "none", "unknown", "not applicable",
    "not known", "not stated", "not recorded", "no", "other",
}

# Common misspellings and variants mapped to one term. Extend as needed;
# full MedDRA coding would replace this list.
EVENT_SYNONYMS = {
    "uticaria": "urticaria",
    "urticarial": "urticaria",
    "urticaria rash": "urticaria",
    "hives": "urticaria",
    "hypersensitivity reaction": "hypersensitivity",
    "allergic reaction": "hypersensitivity",
    "allergy": "hypersensitivity",
    "skin rash": "rash",
    "rashes": "rash",
    "itching": "pruritus",
    "itchiness": "pruritus",
    "chill": "chills",
    "rigors": "chills",
    "pyrexia": "fever",
    "high temperature": "fever",
    "vomitting": "vomiting",
    "head ache": "headache",
    "anaphylactic reaction": "anaphylaxis",
    "anaphylactic shock": "anaphylaxis",
    "difficulty in breathing": "dyspnoea",
    "difficulty breathing": "dyspnoea",
    "shortness of breath": "dyspnoea",
    "dyspnea": "dyspnoea",
    "diarrhoea": "diarrhea",
}

DEATH_PATTERN = re.compile(r"\b(death|died|dead|fatal|deceased)\b", re.I)
TERM_SPLIT_PATTERN = re.compile(r"[,;/\n+]|\band\b|\bwith\b", re.I)


def _normalise(value):
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def extract_event_terms(event_description):
    """Split a free-text event description into normalised event terms."""
    terms = []
    for part in TERM_SPLIT_PATTERN.split(event_description or ""):
        term = " ".join(re.findall(r"[a-z0-9]+", part.lower()))
        term = EVENT_SYNONYMS.get(term, term)
        if len(term) < 3 or term in IGNORED_TERMS:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def display_term(term):
    return term[:1].upper() + term[1:]


def is_fatal_case(case):
    return case.get("event_outcome") == "Fatal" or bool(
        DEATH_PATTERN.search(case.get("seriousness_criteria") or "")
    )


def detection_key(product_name, term):
    return f"{_normalise(product_name)}::{term}"[:600]


def _higher_priority(first, second):
    return first if PRIORITY_RANK[first] >= PRIORITY_RANK[second] else second


def build_signal_candidates(case, products, related_cases, listedness=None):
    """Return the potential signals raised by ``case``.

    ``related_cases`` holds cases (including ``case`` itself) that share a
    product with it, each with ``product_name`` set. Pure function: no
    database access.
    """
    terms = extract_event_terms(case.get("event_description"))
    if not terms:
        return []

    fatal = is_fatal_case(case)
    serious_unlisted = bool(case.get("seriousness")) and listedness == "Not listed"
    received = case.get("received_date")
    window = timedelta(days=WINDOW_DAYS)

    candidates = []
    for product_name in products:
        product_key = _normalise(product_name)
        for term in terms:
            supporting = {}
            for other in related_cases:
                if _normalise(other.get("product_name")) != product_key:
                    continue
                if received and other.get("received_date") and (
                    abs(other["received_date"] - received) > window
                ):
                    continue
                if term in extract_event_terms(other.get("event_description")):
                    supporting[other["case_id"]] = other
            supporting[case["case_id"]] = case

            triggers = []
            priority = "Low"
            if len(supporting) >= MINIMUM_CASES:
                triggers.append(
                    f"{len(supporting)} cases within {WINDOW_DAYS} days"
                )
                priority = _higher_priority(
                    priority,
                    "High"
                    if any(c.get("seriousness") for c in supporting.values())
                    else "Medium",
                )
            if fatal:
                triggers.append(f"fatal case {case['case_number']}")
                priority = "Critical"
            if serious_unlisted:
                triggers.append(
                    f"serious unlisted case {case['case_number']}"
                )
                priority = _higher_priority(priority, "High")

            if not triggers:
                continue

            ordered = sorted(
                supporting.values(),
                key=lambda c: (c.get("received_date") or received, c["case_id"]),
            )
            candidates.append(
                {
                    "product_name": product_name,
                    "event_term": display_term(term),
                    "term": term,
                    "key": detection_key(product_name, term),
                    "priority": priority,
                    "triggers": triggers,
                    "supporting_case_ids": [c["case_id"] for c in ordered],
                    "supporting_case_numbers": [
                        c["case_number"] for c in ordered
                    ],
                }
            )
    return candidates


def _signal_description(candidate):
    return (
        "System-detected potential signal from safety case screening. "
        f"Product: {candidate['product_name']}. "
        f"Event: {candidate['event_term']}. "
        f"Triggered by: {'; '.join(candidate['triggers'])}. "
        f"Supporting cases: {', '.join(candidate['supporting_case_numbers'])}. "
        "QPPV validation is required before this is treated as a "
        "confirmed safety signal."
    )


def _load_case_context(case_id):
    case = query_one(
        """
        SELECT
            case_id,
            case_number,
            received_date,
            seriousness,
            seriousness_criteria,
            event_outcome,
            event_description
        FROM pv.safety_cases
        WHERE case_id = %s
        """,
        (case_id,),
    )
    if case is None:
        return None, [], [], None

    products = [
        row["product_name"]
        for row in query_all(
            """
            SELECT DISTINCT product_name
            FROM pv.case_products
            WHERE case_id = %s
              AND product_name IS NOT NULL
              AND TRIM(product_name) <> ''
            """,
            (case_id,),
        )
    ]
    if not products:
        return case, [], [], None

    related_cases = query_all(
        """
        SELECT DISTINCT
            safety_cases.case_id,
            safety_cases.case_number,
            safety_cases.received_date,
            safety_cases.seriousness,
            safety_cases.event_description,
            case_products.product_name
        FROM pv.safety_cases AS safety_cases
        INNER JOIN pv.case_products AS case_products
            ON case_products.case_id = safety_cases.case_id
        WHERE LOWER(TRIM(case_products.product_name)) = ANY(%s)
          AND safety_cases.received_date BETWEEN
              (%s::date - %s * INTERVAL '1 day')
              AND (%s::date + %s * INTERVAL '1 day')
        """,
        (
            [_normalise(product) for product in products],
            case["received_date"],
            WINDOW_DAYS,
            case["received_date"],
            WINDOW_DAYS,
        ),
    )

    assessment = query_one(
        """
        SELECT listedness_status
        FROM pv.case_safety_assessments
        WHERE case_id = %s
        """,
        (case_id,),
    )
    listedness = assessment["listedness_status"] if assessment else None
    return case, products, related_cases, listedness


def _save_candidate(cursor, case, candidate, actor_user_id):
    cursor.execute(
        """
        SELECT signal_id, signal_number, priority, auto_detected
        FROM pv.safety_signals
        WHERE status IN %s
          AND (
              auto_detection_key = %s
              OR (
                  LOWER(TRIM(product_name)) = %s
                  AND LOWER(REGEXP_REPLACE(TRIM(event_term), '\\s+', ' ', 'g')) = %s
              )
          )
        ORDER BY auto_detected, date_detected, signal_id
        LIMIT 1
        FOR UPDATE
        """,
        (
            OPEN_SIGNAL_STATUSES,
            candidate["key"],
            _normalise(candidate["product_name"]),
            candidate["term"],
        ),
    )
    signal = cursor.fetchone()
    created = False

    if signal is None:
        cursor.execute(
            """
            WITH new_signal AS (
                SELECT nextval(
                    pg_get_serial_sequence('pv.safety_signals', 'signal_id')
                ) AS signal_id
            )
            INSERT INTO pv.safety_signals (
                signal_id,
                signal_number,
                date_detected,
                product_name,
                event_term,
                signal_source,
                signal_description,
                priority,
                status,
                created_by,
                auto_detected,
                auto_detection_key,
                last_screened_at
            )
            SELECT
                signal_id,
                'AUTO-SIG-' || LPAD(signal_id::text, 4, '0'),
                CURRENT_DATE,
                %s, %s,
                'Automated safety case screening',
                %s, %s, 'New', %s, TRUE, %s, CURRENT_TIMESTAMP
            FROM new_signal
            RETURNING signal_id, signal_number
            """,
            (
                candidate["product_name"],
                candidate["event_term"],
                _signal_description(candidate),
                candidate["priority"],
                actor_user_id,
                candidate["key"],
            ),
        )
        signal = cursor.fetchone()
        created = True
    elif signal["auto_detected"]:
        cursor.execute(
            """
            UPDATE pv.safety_signals
            SET priority = %s,
                signal_description = %s,
                last_screened_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE signal_id = %s
            """,
            (
                _higher_priority(signal["priority"], candidate["priority"]),
                _signal_description(candidate),
                signal["signal_id"],
            ),
        )
    else:
        # Manually entered signal: link the evidence but leave the
        # reviewer's priority and description untouched.
        cursor.execute(
            """
            UPDATE pv.safety_signals
            SET last_screened_at = CURRENT_TIMESTAMP
            WHERE signal_id = %s
            """,
            (signal["signal_id"],),
        )

    newly_linked = 0
    for supporting_case_id in candidate["supporting_case_ids"]:
        cursor.execute(
            """
            INSERT INTO pv.safety_signal_cases (signal_id, case_id)
            VALUES (%s, %s)
            ON CONFLICT (signal_id, case_id) DO NOTHING
            """,
            (signal["signal_id"], supporting_case_id),
        )
        newly_linked += cursor.rowcount

    if created:
        cursor.execute(
            """
            INSERT INTO pv.safety_signal_notifications (
                signal_id,
                user_id,
                message
            )
            SELECT %s, users.user_id, %s
            FROM pv.users AS users
            JOIN pv.roles AS roles ON roles.role_id = users.role_id
            WHERE users.is_active = TRUE
              AND (roles.role_name IN %s OR users.user_id = %s)
            ON CONFLICT (signal_id, user_id) DO NOTHING
            """,
            (
                signal["signal_id"],
                (
                    f"Potential safety signal {signal['signal_number']} "
                    f"({candidate['priority']}): "
                    f"{candidate['product_name']} — {candidate['event_term']}"
                ),
                NOTIFIED_ROLES,
                actor_user_id,
            ),
        )

    return {
        "signal_id": signal["signal_id"],
        "signal_number": signal["signal_number"],
        "created": created,
        "newly_linked_cases": newly_linked,
        "product_name": candidate["product_name"],
        "event_term": candidate["event_term"],
        "supporting_case_count": len(candidate["supporting_case_ids"]),
        "priority": candidate["priority"],
        "triggers": candidate["triggers"],
    }


def detect_potential_signals_for_case(case_id, actor_user_id=None):
    case, products, related_cases, listedness = _load_case_context(case_id)
    if case is None or not products:
        return []

    candidates = build_signal_candidates(
        case, products, related_cases, listedness
    )
    results = []
    for candidate in candidates:
        with transaction() as cursor:
            results.append(
                _save_candidate(cursor, case, candidate, actor_user_id)
            )
    return results


def screen_case_for_signals(case_id, actor_user_id=None):
    """Screen one case, never raising: a screening failure must not block
    saving the case. Returns (results, error_occurred)."""
    try:
        return detect_potential_signals_for_case(case_id, actor_user_id), False
    except Exception:
        current_app.logger.exception(
            "Automated signal screening failed for case %s", case_id
        )
        return [], True


def run_signal_detection_for_all_cases(actor_user_id=None):
    cases = query_all(
        """
        SELECT case_id
        FROM pv.safety_cases
        ORDER BY received_date, case_id
        """
    )

    detected_signals = []
    for case in cases:
        detected_signals.extend(
            detect_potential_signals_for_case(
                case_id=case["case_id"],
                actor_user_id=actor_user_id,
            )
        )
    return detected_signals


def summarise_screening(results):
    """Short user-facing message for a screening run, or None."""
    created = [r for r in results if r["created"]]
    linked = [r for r in results if not r["created"] and r["newly_linked_cases"]]
    parts = []
    if created:
        parts.append(
            "New potential signal(s): "
            + ", ".join(
                f"{r['signal_number']} ({r['product_name']} — "
                f"{r['event_term']}, {r['priority']})"
                for r in created
            )
            + "."
        )
    if linked:
        parts.append(
            "Case linked to existing signal(s): "
            + ", ".join(r["signal_number"] for r in linked)
            + "."
        )
    if not parts:
        return None
    return " ".join(parts) + " QPPV review is required."
