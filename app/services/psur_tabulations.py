"""Interval and cumulative summary tabulations of adverse reactions for PSURs.

Cases are counted once per preferred term (a case reporting the same term
twice counts once). Terms are grouped by system organ class (SOC). Reported
terms that have not been coded are listed under UNCODED_SOC with their
original wording so the reviewer can code them before the PSUR is finalised.
"""

from app.db import query_all
from app.services.company_profile import platform as _co_platform, platform_name as _co_pv, short_name as _co_short


UNCODED_SOC = "Uncoded reported terms (code before finalising)"


def tabulate_reactions(rows, period_start, period_end):
    """Build the tabulation from flat rows.

    Each row: case_id, received_date, seriousness, system_organ_class,
    preferred_term. Rows are expected to be on or before period_end; any row
    received in [period_start, period_end] also counts in the interval.
    """
    terms = {}
    for row in rows:
        received = row["received_date"]
        if received is None or received > period_end:
            continue
        soc = row.get("system_organ_class") or UNCODED_SOC
        term = row["preferred_term"]
        entry = terms.setdefault(
            (soc, term),
            {
                "interval_serious": set(),
                "interval_non_serious": set(),
                "cumulative_serious": set(),
                "cumulative_non_serious": set(),
            },
        )
        kind = "serious" if row.get("seriousness") else "non_serious"
        entry[f"cumulative_{kind}"].add(row["case_id"])
        if received >= period_start:
            entry[f"interval_{kind}"].add(row["case_id"])

    groups = {}
    for (soc, term), counts in terms.items():
        groups.setdefault(soc, []).append(
            {"preferred_term": term, **{k: len(v) for k, v in counts.items()}}
        )

    def soc_order(soc):
        return (soc == UNCODED_SOC, soc.lower())

    result = []
    for soc in sorted(groups, key=soc_order):
        rows_for_soc = sorted(groups[soc], key=lambda r: r["preferred_term"].lower())
        result.append(
            {
                "system_organ_class": soc,
                "terms": rows_for_soc,
                "totals": _sum(rows_for_soc),
            }
        )

    case_totals = {
        "interval_cases": len(
            {
                row["case_id"]
                for row in rows
                if row["received_date"] is not None
                and period_start <= row["received_date"] <= period_end
            }
        ),
        "cumulative_cases": len(
            {
                row["case_id"]
                for row in rows
                if row["received_date"] is not None
                and row["received_date"] <= period_end
            }
        ),
    }
    return {
        "groups": result,
        "totals": _sum([group["totals"] for group in result]),
        "case_totals": case_totals,
        "has_uncoded": UNCODED_SOC in groups,
    }


def _sum(items):
    keys = (
        "interval_serious",
        "interval_non_serious",
        "cumulative_serious",
        "cumulative_non_serious",
    )
    return {key: sum(item[key] for item in items) for key in keys}


def load_reaction_rows(product_name, period_end, cumulative_from=None):
    """Coded (and uncoded) event terms for a product's cases."""
    return query_all(
        """
        SELECT DISTINCT
            safety_cases.case_id,
            safety_cases.received_date,
            safety_cases.seriousness,
            event_terms.system_organ_class,
            COALESCE(
                event_terms.preferred_term,
                case_event_terms.verbatim_term
            ) AS preferred_term
        FROM pv.safety_cases AS safety_cases
        INNER JOIN pv.case_products AS case_products
            ON case_products.case_id = safety_cases.case_id
        INNER JOIN pv.case_event_terms AS case_event_terms
            ON case_event_terms.case_id = safety_cases.case_id
        LEFT JOIN pv.event_terms AS event_terms
            ON event_terms.term_id = case_event_terms.term_id
        WHERE LOWER(TRIM(case_products.product_name)) = LOWER(TRIM(%s))
          AND safety_cases.received_date <= %s
          AND (%s::date IS NULL OR safety_cases.received_date >= %s::date)
        """,
        (product_name, period_end, cumulative_from, cumulative_from),
    )


def build_report_tabulation(report):
    """Tabulation for a PSUR record (dict with product and period fields)."""
    cumulative_from = report.get("international_birth_date")
    rows = load_reaction_rows(
        report["product_name"],
        report["reporting_period_end"],
        cumulative_from,
    )
    tabulation = tabulate_reactions(
        rows,
        report["reporting_period_start"],
        report["reporting_period_end"],
    )
    tabulation["cumulative_from"] = cumulative_from
    return tabulation


def tabulation_summary(report, tabulation):
    """Narrative summary of the tabulation for the PSUR section text."""
    totals = tabulation["totals"]
    cases = tabulation["case_totals"]
    period = (
        f"{report['reporting_period_start']:%d %B %Y} to "
        f"{report['reporting_period_end']:%d %B %Y}"
    )
    cumulative_from = tabulation.get("cumulative_from")
    cumulative_text = (
        f"from the international birth date ({cumulative_from:%d %B %Y})"
        if cumulative_from
        else f"from the first case recorded in the {_co_platform()}"
    )
    if not tabulation["groups"]:
        return (
            f"No adverse reactions for {report['product_name']} were "
            f"recorded in the {_co_platform()} up to "
            f"{report['reporting_period_end']:%d %B %Y}."
        )

    text = (
        f"Adverse reactions for {report['product_name']} are tabulated by "
        "system organ class and preferred term below. The reporting "
        f"interval was {period}; cumulative figures were counted "
        f"{cumulative_text} to the end of the interval.\n\n"
        f"In the interval, {cases['interval_cases']} case(s) reported "
        f"{totals['interval_serious']} serious and "
        f"{totals['interval_non_serious']} non-serious reaction term(s). "
        f"Cumulatively, {cases['cumulative_cases']} case(s) reported "
        f"{totals['cumulative_serious']} serious and "
        f"{totals['cumulative_non_serious']} non-serious reaction term(s)."
    )
    if tabulation["has_uncoded"]:
        text += (
            "\n\nSome reported terms have not yet been coded and are "
            "listed separately; they must be coded before this report is "
            "finalised."
        )
    return text
