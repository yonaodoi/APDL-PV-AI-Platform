import re

from app.db import query_all, query_one, transaction


WINDOW_DAYS = 90
MINIMUM_CASES = 2


def _normalise(value):
    return re.sub(
        r"\s+",
        " ",
        (value or "").strip().lower(),
    )


def detect_potential_signals_for_case(case_id, actor_user_id=None):
    case = query_one(
        """
        SELECT
            case_id,
            case_number,
            received_date,
            event_description
        FROM pv.safety_cases
        WHERE case_id = %s
        """,
        (case_id,),
    )

    if case is None:
        return []

    event_key = _normalise(case["event_description"])
    if not event_key:
        return []

    products = query_all(
        """
        SELECT DISTINCT product_name
        FROM pv.case_products
        WHERE case_id = %s
          AND product_name IS NOT NULL
          AND product_name <> ''
        """,
        (case_id,),
    )

    detected_signals = []

    for product_index, product in enumerate(products, start=1):
        product_name = product["product_name"]
        product_key = _normalise(product_name)

        candidates = query_all(
            """
            SELECT DISTINCT
                safety_cases.case_id,
                safety_cases.case_number,
                safety_cases.received_date,
                safety_cases.seriousness,
                safety_cases.event_description
            FROM pv.safety_cases AS safety_cases
            INNER JOIN pv.case_products AS case_products
                ON case_products.case_id = safety_cases.case_id
            WHERE LOWER(case_products.product_name) = LOWER(%s)
              AND safety_cases.received_date BETWEEN
                  (%s::date - INTERVAL '90 days')
                  AND %s::date
            ORDER BY safety_cases.received_date, safety_cases.case_id
            """,
            (
                product_name,
                case["received_date"],
                case["received_date"],
            ),
        )

        supporting_cases = [
            candidate
            for candidate in candidates
            if _normalise(candidate["event_description"]) == event_key
        ]

        if len(supporting_cases) < MINIMUM_CASES:
            continue

        priority = (
            "High"
            if any(candidate["seriousness"] for candidate in supporting_cases)
            else "Medium"
        )

        detection_key = (
            f"{product_key}::{event_key}"
        )[:600]

        case_numbers = ", ".join(
            candidate["case_number"]
            for candidate in supporting_cases
        )

        description = (
            "System-detected potential signal from ADR case screening. "
            f"Product: {product_name}. "
            f"Event: {case['event_description']}. "
            f"Supporting cases within {WINDOW_DAYS} days: "
            f"{case_numbers}. "
            "QPPV validation is required before this is treated as "
            "a confirmed safety signal."
        )

        with transaction() as cursor:
            cursor.execute(
                """
                SELECT signal_id, priority
                FROM pv.safety_signals
                WHERE auto_detection_key = %s
                  AND status IN ('New', 'Under evaluation')
                FOR UPDATE
                """,
                (detection_key,),
            )
            signal = cursor.fetchone()

            if signal:
                cursor.execute(
                    """
                    UPDATE pv.safety_signals
                    SET
                        priority = CASE
                            WHEN priority = 'Critical' THEN 'Critical'
                            WHEN %s = 'High' THEN 'High'
                            ELSE priority
                        END,
                        signal_description = %s,
                        last_screened_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE signal_id = %s
                    """,
                    (
                        priority,
                        description,
                        signal["signal_id"],
                    ),
                )
                signal_id = signal["signal_id"]
                created = False
            else:
                signal_number = (
                    f"AUTO-SIG-{case_id}-{product_index}"
                )

                cursor.execute(
                    """
                    INSERT INTO pv.safety_signals (
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
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, 'New',
                        %s, TRUE, %s, CURRENT_TIMESTAMP
                    )
                    RETURNING signal_id
                    """,
                    (
                        signal_number,
                        case["received_date"],
                        product_name,
                        case["event_description"],
                        "Automated ADR case screening",
                        description,
                        priority,
                        actor_user_id,
                        detection_key,
                    ),
                )
                signal_id = cursor.fetchone()["signal_id"]
                created = True

            for candidate in supporting_cases:
                cursor.execute(
                    """
                    INSERT INTO pv.safety_signal_cases (
                        signal_id,
                        case_id
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (signal_id, case_id) DO NOTHING
                    """,
                    (signal_id, candidate["case_id"]),
                )
            if created and actor_user_id:
                cursor.execute(
                    """
                    INSERT INTO pv.safety_signal_notifications (
                        signal_id,
                        user_id,
                        message
                    )
                    VALUES (%s, %s, %s)
                    ON CONFLICT (signal_id, user_id) DO NOTHING
                    """,
                    (
                        signal_id,
                        actor_user_id,
                        (
                            f"Potential safety signal identified: "
                            f"{product_name} — "
                            f"{case['event_description']}"
                        ),
                    ),
                )

        detected_signals.append(
            {
                "signal_id": signal_id,
                "created": created,
                "product_name": product_name,
                "event_term": case["event_description"],
                "supporting_case_count": len(supporting_cases),
                "priority": priority,
            }
        )

    return detected_signals

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