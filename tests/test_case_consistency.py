from datetime import date

from app.services.case_consistency import evaluate_case_consistency


TODAY = date(2026, 10, 7)


def make_case(**overrides):
    case = {
        "received_date": date(2026, 9, 16),
        "event_onset_date": date(2026, 9, 1),
        "event_end_date": None,
        "event_outcome": "Recovered/resolved",
        "seriousness": False,
        "seriousness_criteria": None,
        "patient_date_of_birth": None,
        "patient_age_years": 45,
        "patient_age_unit": "Years",
        "patient_sex": "Female",
        "patient_pregnancy_status": None,
    }
    case.update(overrides)
    return case


def checks_by_code(case, products=None):
    return {
        check["code"]: check
        for check in evaluate_case_consistency(
            case, products or [], today=TODAY
        )
    }


def test_consistent_case_passes_all_checks():
    checks = checks_by_code(make_case())

    assert {check["status"] for check in checks.values()} == {"Pass"}


def test_death_criterion_with_recovered_outcome_needs_review():
    checks = checks_by_code(
        make_case(seriousness=True, seriousness_criteria="DEATH")
    )

    check = checks["fatal_outcome_consistency"]
    assert check["status"] == "Review"
    assert "Recovered/resolved" in check["message"]


def test_fatal_outcome_must_be_serious_with_death_criterion():
    not_serious = checks_by_code(make_case(event_outcome="Fatal"))
    no_death_criterion = checks_by_code(
        make_case(
            event_outcome="Fatal",
            seriousness=True,
            seriousness_criteria="Hospitalisation",
        )
    )
    correct = checks_by_code(
        make_case(
            event_outcome="Fatal",
            seriousness=True,
            seriousness_criteria="Results in death",
        )
    )

    assert not_serious["fatal_outcome_consistency"]["status"] == "Review"
    assert no_death_criterion["fatal_outcome_consistency"]["status"] == "Review"
    assert correct["fatal_outcome_consistency"]["status"] == "Pass"


def test_death_word_inside_other_word_is_not_matched():
    checks = checks_by_code(
        make_case(seriousness=True, seriousness_criteria="Deathly ill, hospitalised")
    )

    assert checks["fatal_outcome_consistency"]["status"] == "Pass"


def test_onset_after_receipt_needs_review():
    checks = checks_by_code(make_case(event_onset_date=date(2026, 9, 20)))

    assert checks["date_sequence"]["status"] == "Review"
    assert "after the case was received" in checks["date_sequence"]["message"]


def test_onset_before_therapy_start_needs_review():
    products = [
        {
            "product_name": "ABPARA",
            "therapy_start_date": date(2026, 9, 5),
            "therapy_end_date": None,
        }
    ]

    checks = checks_by_code(make_case(), products)

    assert checks["date_sequence"]["status"] == "Review"
    assert "ABPARA" in checks["date_sequence"]["message"]


def test_age_conflicting_with_date_of_birth_needs_review():
    checks = checks_by_code(
        make_case(patient_date_of_birth=date(1990, 1, 1), patient_age_years=45)
    )

    assert checks["patient_consistency"]["status"] == "Review"


def test_age_matching_date_of_birth_passes():
    checks = checks_by_code(
        make_case(patient_date_of_birth=date(1981, 3, 2), patient_age_years=45)
    )

    assert checks["patient_consistency"]["status"] == "Pass"


def test_male_pregnant_patient_needs_review():
    checks = checks_by_code(
        make_case(patient_sex="Male", patient_pregnancy_status="Yes")
    )

    assert checks["patient_consistency"]["status"] == "Review"
