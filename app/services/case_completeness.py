from app.db import transaction
from app.services.case_consistency import evaluate_case_consistency
from app.services.event_coding import (
    REVIEW_METHODS,
    code_case_events,
    get_case_event_terms,
)


def evaluate_case_coding(event_terms):
    pending = [
        term for term in event_terms if term["coding_method"] in REVIEW_METHODS
    ]
    if not event_terms:
        return None
    if pending:
        names = ", ".join(f'"{term["verbatim_term"]}"' for term in pending)
        return {
            "code": "event_coding",
            "label": "Event term coding",
            "status": "Review",
            "message": (
                f"Confirm or code {len(pending)} reported term(s): {names}."
            ),
        }
    return {
        "code": "event_coding",
        "label": "Event term coding",
        "status": "Pass",
        "message": "All reported event terms are coded.",
    }


def evaluate_case_completeness(case, products, event_terms=None):
    checks = []

    checks.append(
        {
            "code": "minimum_patient",
            "label": "Identifiable patient",
            "status": (
                "Pass"
                if any(
                    case.get(field)
                    for field in (
                        "patient_initials",
                        "patient_date_of_birth",
                        "patient_age_years",
                        "patient_address",
                        "patient_phone",
                    )
                )
                else "Review"
            ),
            "message": (
                "Patient information is recorded."
                if any(
                    case.get(field)
                    for field in (
                        "patient_initials",
                        "patient_date_of_birth",
                        "patient_age_years",
                        "patient_address",
                        "patient_phone",
                    )
                )
                else "Record at least one patient identifier or demographic."
            ),
        }
    )

    checks.append(
        {
            "code": "identifiable_reporter",
            "label": "Identifiable reporter",
            "status": (
                "Pass"
                if any(
                    case.get(field)
                    for field in (
                        "reporter_name",
                        "reporter_email",
                        "reporter_phone",
                    )
                )
                else "Review"
            ),
            "message": (
                "Reporter information is recorded."
                if any(
                    case.get(field)
                    for field in (
                        "reporter_name",
                        "reporter_email",
                        "reporter_phone",
                    )
                )
                else "Record a reporter name, email, or telephone number."
            ),
        }
    )

    checks.append(
        {
            "code": "suspected_product",
            "label": "Suspected product",
            "status": "Pass" if products else "Review",
            "message": (
                "At least one suspected product is recorded."
                if products
                else "Record at least one suspected product."
            ),
        }
    )

    checks.append(
        {
            "code": "reported_event",
            "label": "Reported event",
            "status": (
                "Pass" if case.get("event_description") else "Review"
            ),
            "message": (
                "An adverse event description is recorded."
                if case.get("event_description")
                else "Record the reported adverse event."
            ),
        }
    )

    checks.append(
        {
            "code": "event_onset_date",
            "label": "Event onset date",
            "status": (
                "Pass" if case.get("event_onset_date") else "Review"
            ),
            "message": (
                "Event onset date is recorded."
                if case.get("event_onset_date")
                else "Confirm the event onset date or document why it is unavailable."
            ),
        }
    )

    checks.append(
        {
            "code": "event_outcome",
            "label": "Event outcome",
            "status": "Pass" if case.get("event_outcome") else "Review",
            "message": (
                "Event outcome is recorded."
                if case.get("event_outcome")
                else "Record the outcome or select Unknown."
            ),
        }
    )

    seriousness_ok = (
        not case.get("seriousness")
        or bool(case.get("seriousness_criteria"))
    )
    checks.append(
        {
            "code": "seriousness_basis",
            "label": "Seriousness basis",
            "status": "Pass" if seriousness_ok else "Review",
            "message": (
                "Seriousness is supported or the case is non-serious."
                if seriousness_ok
                else "Record the reason for seriousness."
            ),
        }
    )

    follow_up_ok = (
        not case.get("follow_up_required")
        or bool(case.get("follow_up_due_date"))
    )
    checks.append(
        {
            "code": "follow_up_due_date",
            "label": "Follow-up plan",
            "status": "Pass" if follow_up_ok else "Review",
            "message": (
                "Follow-up is not required or has a due date."
                if follow_up_ok
                else "Set a due date when follow-up is required."
            ),
        }
    )

    if event_terms is not None:
        coding_check = evaluate_case_coding(event_terms)
        if coding_check:
            checks.append(coding_check)

    checks.extend(evaluate_case_consistency(case, products))

    return checks


def save_case_completeness(case_id, checks):
    with transaction() as cursor:
        for check in checks:
            cursor.execute(
                """
                INSERT INTO pv.case_completeness_checks (
                    case_id,
                    check_code,
                    check_label,
                    status,
                    message,
                    checked_at
                )
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (case_id, check_code)
                DO UPDATE SET
                    check_label = EXCLUDED.check_label,
                    status = EXCLUDED.status,
                    message = EXCLUDED.message,
                    checked_at = EXCLUDED.checked_at
                """,
                (
                    case_id,
                    check["code"],
                    check["label"],
                    check["status"],
                    check["message"],
                ),
            )


def refresh_case_completeness(case, products):
    code_case_events(case["case_id"], case.get("event_description"))
    event_terms = get_case_event_terms(case["case_id"])
    checks = evaluate_case_completeness(case, products, event_terms)
    save_case_completeness(case["case_id"], checks)
    from app.services.case_follow_up import sync_case_follow_up_tasks

    sync_case_follow_up_tasks(case, checks)
    return checks
