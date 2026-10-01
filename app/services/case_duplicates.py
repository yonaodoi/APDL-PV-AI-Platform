import re
from datetime import timedelta

from app.db import query_all, query_one, transaction


VALID_REVIEW_STATUSES = {
    "Confirmed duplicate",
    "Not a duplicate",
}


def _normalize_initials(value):
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def find_possible_duplicates(case, products):
    initials = _normalize_initials(case.get("patient_initials"))
    birth_date = case.get("patient_date_of_birth")
    reporter_email = (case.get("reporter_email") or "").strip().lower()
    onset_date = case.get("event_onset_date")

    conditions = []
    parameters = []
    if initials and birth_date:
        conditions.append(
            """
            (
                safety_cases.patient_date_of_birth = %s
                AND regexp_replace(
                    upper(coalesce(safety_cases.patient_initials, '')),
                    '[^A-Z0-9]', '', 'g'
                ) = %s
            )
            """
        )
        parameters.extend((birth_date, initials))
    if reporter_email and onset_date:
        conditions.append(
            """
            (
                lower(trim(safety_cases.reporter_email)) = %s
                AND safety_cases.event_onset_date BETWEEN %s AND %s
            )
            """
        )
        parameters.extend(
            (reporter_email, onset_date - timedelta(days=7),
             onset_date + timedelta(days=7))
        )
    if initials and onset_date:
        conditions.append(
            """
            (
                regexp_replace(
                    upper(coalesce(safety_cases.patient_initials, '')),
                    '[^A-Z0-9]', '', 'g'
                ) = %s
                AND safety_cases.event_onset_date BETWEEN %s AND %s
            )
            """
        )
        parameters.extend(
            (initials, onset_date - timedelta(days=3),
             onset_date + timedelta(days=3))
        )
    if not conditions:
        return []

    candidates = query_all(
        f"""
        SELECT safety_cases.case_id,
               safety_cases.case_number,
               safety_cases.patient_initials,
               safety_cases.patient_date_of_birth,
               safety_cases.reporter_email,
               safety_cases.event_onset_date,
               safety_cases.workflow_status,
               ARRAY(
                   SELECT DISTINCT lower(trim(
                       coalesce(
                           nullif(trim(case_products.generic_name), ''),
                           case_products.product_name
                       )
                   ))
                   FROM pv.case_products AS case_products
                   WHERE case_products.case_id = safety_cases.case_id
                     AND case_products.product_role = 'Suspect'
               ) AS suspect_products
        FROM pv.safety_cases AS safety_cases
        WHERE safety_cases.case_id <> %s
          AND ({' OR '.join(conditions)})
        ORDER BY safety_cases.received_date DESC
        """,
        tuple([case["case_id"], *parameters]),
    )

    current_products = {
        (product.get("generic_name") or product.get("product_name") or "")
        .strip()
        .lower()
        for product in products
        if product.get("product_role", "Suspect") == "Suspect"
    }
    result = []
    for candidate in candidates:
        candidate_products = set(candidate.get("suspect_products") or [])
        shared_products = current_products.intersection(candidate_products)
        reasons = []

        exact_patient_match = (
            birth_date
            and case.get("patient_date_of_birth")
            == candidate.get("patient_date_of_birth")
            and initials
            and initials
            == _normalize_initials(candidate.get("patient_initials"))
        )
        if exact_patient_match:
            reasons.append(
                "Patient initials and date of birth match exactly."
            )

        same_reporter = (
            reporter_email
            and reporter_email
            == (candidate.get("reporter_email") or "").strip().lower()
        )
        onset_difference = (
            abs((onset_date - candidate["event_onset_date"]).days)
            if onset_date and candidate.get("event_onset_date")
            else None
        )
        if same_reporter and onset_difference is not None \
                and onset_difference <= 7 and shared_products:
            reasons.append(
                "Reporter email, suspected product, and event onset "
                "within 7 days match."
            )

        if (
            initials
            and initials
            == _normalize_initials(candidate.get("patient_initials"))
            and onset_difference is not None
            and onset_difference <= 3
            and shared_products
            and not exact_patient_match
        ):
            reasons.append(
                "Patient initials and suspected product match, with event "
                "onset within 3 days."
            )

        if not reasons:
            continue
        review = query_one(
            """
            SELECT review_status
            FROM pv.case_duplicate_reviews
            WHERE case_id_low = LEAST(%s, %s)
              AND case_id_high = GREATEST(%s, %s)
            """,
            (
                case["case_id"],
                candidate["case_id"],
                case["case_id"],
                candidate["case_id"],
            ),
        )
        candidate["match_reasons"] = reasons
        candidate["review_status"] = (
            review["review_status"] if review else "Needs review"
        )
        result.append(candidate)
    return result


def save_duplicate_review(case_id, candidate_case_id, status, user_id):
    if status not in VALID_REVIEW_STATUSES or case_id == candidate_case_id:
        raise ValueError("Invalid duplicate review decision.")

    lower_id, higher_id = sorted((case_id, candidate_case_id))
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.case_duplicate_reviews (
                case_id_low, case_id_high, review_status, reviewed_by,
                reviewed_at
            )
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (case_id_low, case_id_high)
            DO UPDATE SET
                review_status = EXCLUDED.review_status,
                reviewed_by = EXCLUDED.reviewed_by,
                reviewed_at = CURRENT_TIMESTAMP
            """,
            (lower_id, higher_id, status, user_id),
        )
        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (
                case_id, action, details, performed_by
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                "Duplicate screening reviewed",
                f"Case {candidate_case_id} marked as {status.lower()}.",
                user_id,
            ),
        )
