"""What each report template can print, and the data behind it.

For every report type there is:
* a catalogue: the markers a template may use, with a plain description
  (shown on the marker guide and given to the AI when it maps a form);
* a sample: example data used to test-fill a template before it goes live;
* a builder: the real data for one record (or one filtered summary).
"""

from datetime import date, datetime, timedelta

from flask import current_app

from app.db import query_all, query_one

# --------------------------------------------------------------------------
# Shared field groups
# --------------------------------------------------------------------------

ORG_FIELDS = {
    "org.name": "Registered company name",
    "org.short_name": "Company short name",
    "org.letter_name": "Company name used on letters",
    "org.department": "Department",
    "org.address": "Company address",
    "org.country": "Home country",
    "org.pv_email": "Pharmacovigilance email address",
    "org.pv_phone": "Pharmacovigilance telephone number",
    "org.website": "Website",
    "org.printed_on": "Date the report is printed",
}

CASE_FIELDS = {
    "case.case_number": "Case number",
    "case.received_date": "Date received",
    "case.country_name": "Country",
    "case.source": "Report source",
    "case.report_type": "Report type (initial / follow-up)",
    "case.seriousness": "Serious or Non-serious",
    "case.seriousness_criteria": "Seriousness criteria",
    "case.workflow_status": "Case stage",
    "case.owner": "Case owner (PV officer)",
    "case.reporter_name": "Reporter name",
    "case.reporter_profession": "Reporter profession",
    "case.reporter_organisation": "Reporter organisation",
    "case.reporter_phone": "Reporter phone",
    "case.reporter_email": "Reporter email",
    "case.patient_initials": "Patient initials",
    "case.patient_date_of_birth": "Patient date of birth",
    "case.patient_age_years": "Patient age (years)",
    "case.patient_sex": "Patient sex",
    "case.patient_weight_kg": "Patient weight (kg)",
    "case.patient_pregnancy_status": "Pregnancy status",
    "case.patient_address": "Patient address",
    "case.patient_phone": "Patient phone",
    "case.medical_history": "Medical history",
    "case.concomitant_medicines": "Concomitant medicines",
    "case.event_description": "Event description",
    "case.event_onset_date": "Event onset date",
    "case.event_onset_time": "Event onset time",
    "case.event_end_date": "Event end date",
    "case.treatment_given": "Treatment given",
    "case.laboratory_results": "Laboratory results",
    "case.event_outcome": "Event outcome",
    "case.causality_assessment": "Causality assessment",
    "case.case_narrative": "Case narrative",
    "case.regulatory_submitted_date": "Date submitted to the regulator",
    "case.reporting_due_date": "Regulatory reporting due date",
}

PRODUCT_FIELDS = {
    "product_name": "Product name",
    "generic_name": "Generic name",
    "strength": "Strength",
    "dosage_form": "Dosage form",
    "batch_number": "Batch number",
    "expiry_date": "Expiry date",
    "dose": "Dose",
    "route": "Route",
    "frequency": "Frequency",
    "indication": "Indication",
    "therapy_start_date": "Therapy start date",
    "therapy_end_date": "Therapy end date",
    "action_taken": "Action taken with the product",
}

SIGN_OFF_FIELDS = {
    "signoff.reviewed_by": "QPPV review – name",
    "signoff.reviewed_on": "QPPV review – date",
    "signoff.approved_by": "Case approval – name",
    "signoff.approved_on": "Case approval – date",
}


def _prefixed(prefix, fields):
    return {f"{prefix}.{k}": v for k, v in fields.items()}


# --------------------------------------------------------------------------
# Catalogues
# --------------------------------------------------------------------------

REPORT_TYPES = {
    "case_report": {
        "label": "Individual case report (ICSR / ADR form)",
        "description": "One safety case: patient, products, events, assessment and sign-off.",
        "fields": {**ORG_FIELDS, **CASE_FIELDS, **_prefixed("product", PRODUCT_FIELDS),
                   "assessment.listedness": "Listedness (Listed / Not listed)",
                   "assessment.expectedness": "Expectedness",
                   "assessment.reference": "Reference document used",
                   **SIGN_OFF_FIELDS},
        "lists": {
            "products": {"label": "All suspect products (one row each)", "fields": PRODUCT_FIELDS},
            "events": {"label": "Reported events (one row each)",
                       "fields": {"term": "Reported term", "preferred_term": "Coded preferred term",
                                  "system_organ_class": "System organ class"}},
        },
    },
    "follow_up_form": {
        "label": "Follow-up request form",
        "description": "Questions sent to the reporter for missing information.",
        "fields": {**ORG_FIELDS,
                   "case.case_number": "Case number", "case.received_date": "Date received",
                   "case.reporter_name": "Reporter name", "case.patient_initials": "Patient initials",
                   "case.patient_age_years": "Patient age", "case.patient_sex": "Patient sex",
                   "case.event_description": "Event description",
                   "product.product_name": "Suspect product",
                   "request.due_date": "Reply requested by", "request.sent_on": "Date and time sent",
                   "request.prepared_by": "Prepared by", "request.question_count": "Number of questions"},
        "lists": {
            "questions": {"label": "Questions for the reporter (one each)",
                          "fields": {"number": "Question number", "label": "Topic", "question": "Question text",
                                     "options": "Answer options"}},
        },
    },
    "ai_assessment": {
        "label": "AI case assessment report",
        "description": "The approved AI-assisted case assessment with signatories.",
        "fields": {**ORG_FIELDS,
                   "case.case_number": "Case number", "case.event_description": "Event description",
                   "case.received_date": "Date received", "product.product_name": "Suspect product",
                   "report.text": "The full report text",
                   "report.status": "Report status", "report.generated_on": "Date generated",
                   "report.generated_by": "Generated by", "report.approved_on": "Date approved",
                   "report.prepared_by": "Prepared by – name", "report.prepared_by_designation": "Prepared by – title",
                   "report.reviewed_by": "Reviewed by – name", "report.reviewed_by_designation": "Reviewed by – title",
                   "report.authorised_by": "Authorised by – name", "report.authorised_by_designation": "Authorised by – title"},
        "lists": {
            "report_paragraphs": {"label": "Report text, one paragraph each", "fields": {"text": "Paragraph text"}},
        },
    },
    "psur": {
        "label": "PSUR / PBRER",
        "description": "Periodic safety update report for one product and period.",
        "fields": {**ORG_FIELDS,
                   **{f"psur.{k}": v for k, v in {
                       "report_number": "Report number", "serial_number": "Serial number",
                       "product_name": "Product", "active_substances": "Active substances",
                       "atc_codes": "ATC codes", "therapeutic_indication": "Therapeutic indication",
                       "mechanism_of_action": "Mechanism of action",
                       "international_birth_date": "International birth date", "eurd": "EURD",
                       "reporting_period_start": "Reporting period start", "reporting_period_end": "Reporting period end",
                       "countries_covered": "Countries covered",
                       "marketing_authorisation_number": "Marketing authorisation number",
                       "marketing_authorisation_date": "Marketing authorisation date",
                       "marketing_authorisation_holder_name": "Marketing authorisation holder",
                       "marketing_authorisation_procedure": "Authorisation procedure",
                       "qppv_name": "QPPV name", "qppv_email": "QPPV email", "qppv_phone": "QPPV phone",
                       "prepared_by": "Prepared by", "approved_by": "Approved by"}.items()},
                   "tabulation.interval_cases": "Cases in the reporting interval",
                   "tabulation.cumulative_cases": "Cumulative cases"},
        "lists": {
            "sections": {"label": "Report sections (one each)",
                         "fields": {"number": "Section number", "title": "Section title", "content": "Section text"}},
            "tabulation_rows": {"label": "Reaction tabulation (one row per term)",
                                "fields": {"system_organ_class": "System organ class", "preferred_term": "Preferred term",
                                           "interval_serious": "Interval – serious", "interval_non_serious": "Interval – non-serious",
                                           "cumulative_serious": "Cumulative – serious",
                                           "cumulative_non_serious": "Cumulative – non-serious"}},
        },
    },
    "case_summary": {
        "label": "Safety case reporting summary",
        "description": "A filtered list of safety cases with totals.",
        "fields": {**ORG_FIELDS, "filters.period": "Reporting period", "filters.product": "Product filter",
                   "filters.country": "Country filter", "filters.status": "Stage filter",
                   "filters.seriousness": "Seriousness filter",
                   "totals.cases": "Number of cases", "totals.serious": "Serious cases",
                   "totals.non_serious": "Non-serious cases"},
        "lists": {"cases": {"label": "Cases (one row each)",
                            "fields": {"case_number": "Case number", "product_name": "Product",
                                       "country_name": "Country", "event_description": "Event",
                                       "workflow_status": "Stage", "received_date": "Date received",
                                       "seriousness": "Seriousness"}}},
    },
    "signal_summary": {
        "label": "Safety signal reporting summary",
        "description": "A filtered list of safety signals with totals.",
        "fields": {**ORG_FIELDS, "filters.period": "Reporting period", "filters.product": "Product filter",
                   "filters.status": "Status filter", "filters.priority": "Priority filter",
                   "totals.signals": "Number of signals", "totals.high_priority": "High or critical priority",
                   "totals.under_evaluation": "Under evaluation"},
        "lists": {"signals": {"label": "Signals (one row each)",
                              "fields": {"signal_number": "Signal number", "product_name": "Product",
                                         "event_term": "Event term", "signal_source": "Source",
                                         "owner_name": "Owner", "status": "Status",
                                         "date_detected": "Date detected", "priority": "Priority"}}},
    },
    "complaint_summary": {
        "label": "Product complaint reporting summary",
        "description": "A filtered list of product quality complaints with totals.",
        "fields": {**ORG_FIELDS, "filters.period": "Reporting period", "filters.product": "Product filter",
                   "filters.country": "Country filter", "filters.status": "Status filter",
                   "filters.severity": "Severity filter",
                   "totals.complaints": "Number of complaints", "totals.serious": "Serious complaints"},
        "lists": {"complaints": {"label": "Complaints (one row each)",
                                 "fields": {"complaint_number": "Complaint number", "product_name": "Product",
                                            "batch_number": "Batch", "country_name": "Country",
                                            "complaint_category": "Category", "status": "Status",
                                            "date_received": "Date received", "severity": "Severity"}}},
    },
}


def all_markers(report_type):
    """Every marker a template of this type may contain."""
    spec = REPORT_TYPES[report_type]
    names = set(spec["fields"])
    for list_name, info in spec["lists"].items():
        names.add(list_name)
        for sub in info["fields"]:
            names.add(f"{list_name}.{sub}")
            names.add(sub)  # usable on its own inside the list's block or row
    return names


def unknown_markers(report_type, markers, blocks=()):
    known = all_markers(report_type)
    spec = REPORT_TYPES[report_type]
    group_prefixes = {name.split(".")[0] for name in spec["fields"]}
    bad = []
    for marker in sorted(set(markers) | set(blocks)):
        if marker in known:
            continue
        # A whole group used as a block, e.g. {{#product}}...{{/product}}
        if marker in group_prefixes:
            continue
        bad.append(marker)
    return bad


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _org():
    from app.services.company_profile import company

    profile = company()
    return {
        "name": profile["legal_name"],
        "short_name": profile["short_name"],
        "letter_name": profile["letter_name"],
        "department": profile["department"],
        "address": profile["address"],
        "country": profile["country"],
        "pv_email": profile["pv_email"],
        "pv_phone": current_app.config.get("PV_CONTACT_PHONE") or profile["pv_phone"],
        "website": profile["website"],
        "printed_on": date.today(),
    }


def _seriousness(value):
    if value is None:
        return None
    return "Serious" if value else "Non-serious"


def _clean(row):
    return dict(row) if row else {}


# --------------------------------------------------------------------------
# Real data
# --------------------------------------------------------------------------

def case_context(case_id):
    from app.services.event_coding import get_case_event_terms
    from app.services.reporting_clock import evaluate_reporting_clock

    case = _clean(query_one(
        """
        SELECT c.*, co.country_name, owner.full_name AS owner
        FROM pv.safety_cases AS c
        LEFT JOIN pv.countries AS co ON co.country_id = c.country_id
        LEFT JOIN pv.users AS owner ON owner.user_id = c.assigned_to
        WHERE c.case_id = %s
        """,
        (case_id,),
    ))
    if not case:
        return None
    clock = evaluate_reporting_clock(case) or {}
    case["reporting_due_date"] = clock.get("due_date")
    case["seriousness"] = _seriousness(case.get("seriousness"))
    products = [_clean(p) for p in query_all(
        "SELECT * FROM pv.case_products WHERE case_id = %s ORDER BY case_product_id", (case_id,)
    )]
    try:
        events = [
            {"term": e.get("verbatim_term"), "preferred_term": e.get("preferred_term"),
             "system_organ_class": e.get("system_organ_class")}
            for e in get_case_event_terms(case_id)
        ]
    except Exception:
        events = []
    assessment = _clean(query_one(
        "SELECT listedness_status, expectedness_status, reference_title FROM pv.case_safety_assessments WHERE case_id = %s",
        (case_id,),
    ))
    signoff = {}
    for row in query_all(
        """
        SELECT a.step, a.decided_at, u.full_name
        FROM pv.case_approvals AS a LEFT JOIN pv.users AS u ON u.user_id = a.decided_by
        WHERE a.case_id = %s AND a.step IN ('Reviewed', 'Approved')
        ORDER BY a.decided_at
        """,
        (case_id,),
    ):
        key = "reviewed" if row["step"] == "Reviewed" else "approved"
        signoff[f"{key}_by"] = row["full_name"]
        signoff[f"{key}_on"] = row["decided_at"].date() if row.get("decided_at") else None
    return {
        "org": _org(),
        "case": case,
        "product": products[0] if products else {},
        "products": products,
        "events": events,
        "assessment": {
            "listedness": assessment.get("listedness_status"),
            "expectedness": assessment.get("expectedness_status"),
            "reference": assessment.get("reference_title"),
        },
        "signoff": signoff,
    }


def follow_up_context(case, product, items, due_date, prepared_by=None, sent_at=None):
    from app.services.follow_up_request import format_sent

    return {
        "org": _org(),
        "case": {**_clean(case), "seriousness": _seriousness((case or {}).get("seriousness"))},
        "product": _clean(product),
        "request": {
            "due_date": due_date,
            "sent_on": format_sent(sent_at) if sent_at else None,
            "prepared_by": prepared_by,
            "question_count": len(items or []),
        },
        "questions": [
            {"number": item.get("number"), "label": item.get("label"), "question": item.get("question"),
             "options": " / ".join(item.get("options") or []) or None}
            for item in items or []
        ],
    }


def ai_assessment_context(case, product, report):
    report = _clean(report)
    text = report.get("report_text") or ""
    return {
        "org": _org(),
        "case": _clean(case),
        "product": _clean(product),
        "report": {
            "text": text,
            "status": report.get("generation_status"),
            "generated_on": report.get("created_at"),
            "generated_by": report.get("generated_by_name"),
            "approved_on": report.get("approved_at"),
            "prepared_by": report.get("prepared_by_name"),
            "prepared_by_designation": report.get("prepared_by_designation"),
            "reviewed_by": report.get("reviewed_by_name"),
            "reviewed_by_designation": report.get("reviewed_by_designation"),
            "authorised_by": report.get("authorised_by_name"),
            "authorised_by_designation": report.get("authorised_by_designation"),
        },
        "report_paragraphs": [{"text": p.strip()} for p in text.split("\n\n") if p.strip()],
    }


def psur_context(report, sections, tabulation=None):
    rows = []
    for group in (tabulation or {}).get("groups", []):
        for term in group.get("terms", []):
            rows.append({"system_organ_class": group.get("system_organ_class"), **term})
    case_totals = (tabulation or {}).get("case_totals", {})
    return {
        "org": _org(),
        "psur": _clean(report),
        "sections": [
            {"number": i, "title": s.get("section_title") or s.get("title"),
             "content": s.get("final_content") or s.get("content")}
            for i, s in enumerate(sections or [], start=1)
        ],
        "tabulation": {"interval_cases": case_totals.get("interval_cases"),
                       "cumulative_cases": case_totals.get("cumulative_cases")},
        "tabulation_rows": rows,
    }


def case_summary_context(cases, filters):
    serious = sum(1 for c in cases if c.get("seriousness"))
    return {
        "org": _org(),
        "filters": {"period": filters.get("reporting_period"), "product": filters.get("product"),
                    "country": filters.get("country"), "status": filters.get("status"),
                    "seriousness": filters.get("priority")},
        "totals": {"cases": len(cases), "serious": serious, "non_serious": len(cases) - serious},
        "cases": [{**_clean(c), "seriousness": _seriousness(c.get("seriousness"))} for c in cases],
    }


def signal_summary_context(signals, filters):
    return {
        "org": _org(),
        "filters": {"period": filters.get("reporting_period"), "product": filters.get("product"),
                    "status": filters.get("status"), "priority": filters.get("priority")},
        "totals": {"signals": len(signals),
                   "high_priority": sum(1 for s in signals if s.get("priority") in ("High", "Critical")),
                   "under_evaluation": sum(1 for s in signals if s.get("status") == "Under evaluation")},
        "signals": [_clean(s) for s in signals],
    }


def complaint_summary_context(complaints, filters):
    return {
        "org": _org(),
        "filters": {"period": filters.get("reporting_period"), "product": filters.get("product"),
                    "country": filters.get("country"), "status": filters.get("status"),
                    "severity": filters.get("severity")},
        "totals": {"complaints": len(complaints),
                   "serious": sum(1 for c in complaints if c.get("severity") == "Serious")},
        "complaints": [_clean(c) for c in complaints],
    }


# --------------------------------------------------------------------------
# Samples, for test-filling a template
# --------------------------------------------------------------------------

def sample_context(report_type):
    """Example data with every field filled, two items per list, plus a few
    empty values so the tidying is tested too."""
    spec = REPORT_TYPES[report_type]
    context = {}
    for name, label in spec["fields"].items():
        group, field = name.split(".", 1)
        context.setdefault(group, {})[field] = _sample_value(field, label)
    for list_name, info in spec["lists"].items():
        context[list_name] = [
            {sub: _sample_value(sub, label, n) for sub, label in info["fields"].items()}
            for n in (1, 2)
        ]
    # A couple of empty values, as real records have.
    if "case" in context:
        context["case"]["patient_phone"] = None
        context["case"]["laboratory_results"] = None
    return context


def _sample_value(field, label, n=1):
    if "date" in field or field.endswith("_on") or field in ("eurd", "international_birth_date"):
        return date.today() - timedelta(days=10 * n)
    if field in ("number", "question_count") or field.endswith("_cases") or field.startswith(("interval_", "cumulative_")):
        return n
    if field == "seriousness":
        return "Serious"
    if field == "text" or field == "content":
        return f"Example {label.lower()} {n}. Line one.\nLine two."
    return f"Example {label.lower()}" + (f" {n}" if n > 1 else "")
