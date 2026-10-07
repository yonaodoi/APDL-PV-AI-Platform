from datetime import date

from app.services.psur_tabulations import (
    UNCODED_SOC,
    tabulate_reactions,
    tabulation_summary,
)


START = date(2026, 7, 1)
END = date(2026, 12, 31)


def row(case_id, received, serious, soc, term):
    return {
        "case_id": case_id,
        "received_date": received,
        "seriousness": serious,
        "system_organ_class": soc,
        "preferred_term": term,
    }


SKIN = "Skin and subcutaneous tissue disorders"
IMMUNE = "Immune system disorders"


def test_counts_cases_per_term_for_interval_and_cumulative():
    tabulation = tabulate_reactions(
        [
            row(2, date(2026, 9, 16), True, SKIN, "Urticaria"),
            row(2, date(2026, 9, 16), True, IMMUNE, "Hypersensitivity"),
            row(1, date(2026, 3, 1), False, SKIN, "Urticaria"),
            row(4, date(2026, 10, 2), False, SKIN, "Urticaria"),
        ],
        START,
        END,
    )

    groups = {g["system_organ_class"]: g for g in tabulation["groups"]}
    urticaria = groups[SKIN]["terms"][0]
    assert urticaria == {
        "preferred_term": "Urticaria",
        "interval_serious": 1,
        "interval_non_serious": 1,
        "cumulative_serious": 1,
        "cumulative_non_serious": 2,
    }
    assert tabulation["case_totals"] == {
        "interval_cases": 2,
        "cumulative_cases": 3,
    }
    assert [g["system_organ_class"] for g in tabulation["groups"]] == [
        IMMUNE,
        SKIN,
    ]


def test_same_case_and_term_counted_once():
    tabulation = tabulate_reactions(
        [
            row(2, date(2026, 9, 16), True, SKIN, "Urticaria"),
            row(2, date(2026, 9, 16), True, SKIN, "Urticaria"),
        ],
        START,
        END,
    )

    assert tabulation["totals"]["interval_serious"] == 1


def test_cases_after_period_end_are_excluded():
    tabulation = tabulate_reactions(
        [row(9, date(2027, 1, 5), True, SKIN, "Rash")],
        START,
        END,
    )

    assert tabulation["groups"] == []
    assert tabulation["case_totals"]["cumulative_cases"] == 0


def test_uncoded_terms_listed_last_and_flagged():
    tabulation = tabulate_reactions(
        [
            row(3, date(2026, 9, 8), False, None, "ffffff"),
            row(2, date(2026, 9, 16), True, SKIN, "Urticaria"),
        ],
        START,
        END,
    )

    assert tabulation["groups"][-1]["system_organ_class"] == UNCODED_SOC
    assert tabulation["has_uncoded"] is True


def test_summary_states_period_cumulative_basis_and_uncoded_warning():
    report = {
        "product_name": "ABPARA",
        "reporting_period_start": START,
        "reporting_period_end": END,
    }
    tabulation = tabulate_reactions(
        [
            row(2, date(2026, 9, 16), True, SKIN, "Urticaria"),
            row(3, date(2026, 9, 8), False, None, "ffffff"),
        ],
        START,
        END,
    )
    tabulation["cumulative_from"] = date(2020, 1, 15)

    text = tabulation_summary(report, tabulation)

    assert "01 July 2026 to 31 December 2026" in text
    assert "international birth date (15 January 2020)" in text
    assert "2 case(s) reported 1 serious and 1 non-serious" in text
    assert "must be coded before this report is finalised" in text


def test_summary_when_no_reactions():
    report = {
        "product_name": "ABPARA",
        "reporting_period_start": START,
        "reporting_period_end": END,
    }
    tabulation = tabulate_reactions([], START, END)
    tabulation["cumulative_from"] = None

    assert tabulation_summary(report, tabulation).startswith(
        "No adverse reactions for ABPARA"
    )
