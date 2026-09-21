from app.db import query_all


def _display_date(value):
    if value is None:
        return "Not recorded"

    return value.strftime("%d %b %Y")


def _case_lines(cases):
    if not cases:
        return (
            "No ADR cases for this product were recorded in the "
            "APDL PV platform during the reporting interval."
        )

    return "\n\n".join(
        (
            f"ADR case {case['case_number']} was received on "
            f"{_display_date(case['received_date'])}. The reported "
            f"event was {case['event_description']}. The case was "
            f"classified as "
            f"{'serious' if case['seriousness'] else 'non-serious'} "
            f"and its current workflow status is "
            f"{case['workflow_status']}."
        )
        for case in cases
    )

def _complaint_lines(complaints):
    if not complaints:
        return (
            "No product complaints for this product were recorded "
            "in the APDL PV platform during the reporting interval."
        )

    return "\n\n".join(
        (
            f"Complaint {complaint['complaint_number']} was received "
            f"on {_display_date(complaint['date_received'])}. It was "
            f"classified as {complaint['severity']} and is currently "
            f"recorded as {complaint['status']}. The complaint "
            f"description was: {complaint['complaint_description']}."
        )
        for complaint in complaints
    )

def _signal_lines(signals):
    if not signals:
        return (
            "No safety signals for this product were recorded in the "
            "APDL PV platform during the reporting interval."
        )

    return "\n\n".join(
        (
            f"Safety signal {signal['signal_number']} concerns "
            f"{signal['event_term']}. It is classified as "
            f"{signal['priority']} priority and is currently "
            f"{signal['status']}. The signal is linked to "
            f"{signal['supporting_case_count']} supporting ADR "
            f"case(s)."
        )
        for signal in signals
    )

def _rsi_lines(rsi_documents):
    if not rsi_documents:
        return (
            "No change to the reference safety information for this "
            "product was recorded in the APDL PV platform during the "
            "reporting interval. The QPPV should confirm whether any "
            "approved product-information update, safety variation or "
            "other reference-document revision applies before the "
            "PBRER is finalised."
        )

    return "\n\n".join(
        (
            f"Reference safety information for the product was "
            f"reviewed through the {document['document_type']}"
            f"{' (version ' + document['document_version'] + ')' if document['document_version'] else ''}"
            f"{' for the ' + document['market'] + ' market' if document['market'] else ''}, "
            f"effective {_display_date(document['effective_date'])}. "
            "The QPPV should assess whether this information changes "
            "the listedness, expectedness, frequency or risk "
            "characterisation of any reported adverse reaction."
        )
        for document in rsi_documents
    )
def build_psur_evidence_sections(report):
    product_name = report["product_name"]
    start_date = report["reporting_period_start"]
    end_date = report["reporting_period_end"]

    safety_cases = query_all(
        """
        SELECT DISTINCT
            safety_cases.case_id,
            safety_cases.case_number,
            safety_cases.received_date,
            safety_cases.seriousness,
            safety_cases.workflow_status,
            safety_cases.event_description
        FROM pv.safety_cases AS safety_cases
        INNER JOIN pv.case_products AS case_products
            ON case_products.case_id = safety_cases.case_id
        WHERE LOWER(case_products.product_name) = LOWER(%s)
          AND safety_cases.received_date BETWEEN %s AND %s
        ORDER BY
            safety_cases.received_date,
            safety_cases.case_number
        """,
        (product_name, start_date, end_date),
    )

    complaints = query_all(
        """
        SELECT
            complaint_number,
            date_received,
            severity,
            status,
            complaint_description
        FROM pv.product_complaints
        WHERE LOWER(product_name) = LOWER(%s)
          AND date_received BETWEEN %s AND %s
        ORDER BY date_received, complaint_number
        """,
        (product_name, start_date, end_date),
    )

    signals = query_all(
        """
        SELECT
            safety_signals.signal_id,
            safety_signals.signal_number,
            safety_signals.event_term,
            safety_signals.priority,
            safety_signals.status,
            safety_signals.auto_detected,
            COUNT(safety_signal_cases.case_id)
                AS supporting_case_count
        FROM pv.safety_signals AS safety_signals
        LEFT JOIN pv.safety_signal_cases AS safety_signal_cases
            ON safety_signal_cases.signal_id = safety_signals.signal_id
        WHERE LOWER(safety_signals.product_name) = LOWER(%s)
          AND safety_signals.date_detected BETWEEN %s AND %s
        GROUP BY
            safety_signals.signal_id,
            safety_signals.signal_number,
            safety_signals.event_term,
            safety_signals.priority,
            safety_signals.status,
            safety_signals.auto_detected
        ORDER BY
            safety_signals.date_detected,
            safety_signals.signal_number
        """,
        (product_name, start_date, end_date),
    )

    rsi_documents = query_all(
        """
        SELECT
            document_type,
            document_version,
            market,
            effective_date
        FROM pv.reference_safety_information
        WHERE LOWER(product_name) = LOWER(%s)
          AND effective_date BETWEEN %s AND %s
        ORDER BY effective_date, document_type
        """,
        (product_name, start_date, end_date),
    )
    case_assessments = query_all(
        """
        SELECT
            case_safety_assessments.listedness_status,
            case_safety_assessments.expectedness_status,
            COUNT(*) AS total_cases
        FROM pv.case_safety_assessments AS case_safety_assessments
        INNER JOIN pv.safety_cases AS safety_cases
            ON safety_cases.case_id = case_safety_assessments.case_id
        INNER JOIN pv.case_products AS case_products
            ON case_products.case_id = safety_cases.case_id
        WHERE LOWER(case_products.product_name) = LOWER(%s)
          AND safety_cases.received_date BETWEEN %s AND %s
        GROUP BY
            case_safety_assessments.listedness_status,
            case_safety_assessments.expectedness_status
        ORDER BY
            case_safety_assessments.listedness_status,
            case_safety_assessments.expectedness_status
        """,
        (product_name, start_date, end_date),
    )
    total_cases = len(safety_cases)
    serious_cases = sum(
        1 for case in safety_cases if case["seriousness"]
    )
    automated_signals = sum(
        1 for signal in signals if signal["auto_detected"]
    )

    period = (
        f"{_display_date(start_date)} to {_display_date(end_date)}"
    )
    active_substances = (
        report["active_substances"]
        or "the active substance recorded for this product"
    )
    therapeutic_indication = (
        report["therapeutic_indication"]
        or "the authorised indication recorded in the product dossier"
    )
    mechanism_of_action = (
        report["mechanism_of_action"]
        or "the mechanism of action recorded in the product dossier"
    )
    markets = report["countries_covered"] or "not recorded"

    if case_assessments:
        assessment_summary = "\n\n".join(
            (
                f"{assessment['total_cases']} ADR case(s) had been "
                f"assessed as {assessment['listedness_status'].lower()} "
                f"and {assessment['expectedness_status'].lower()}."
            )
            for assessment in case_assessments
        )
    else:
        assessment_summary = (
            "No ADR listedness or expectedness assessment had been "
            "recorded for this product during the reporting interval."
        )

    executive_summary = (
        f"This Periodic Benefit-Risk Evaluation Report covered "
        f"{product_name}, containing {active_substances}, for the "
        f"period {period}. {product_name} was recorded for "
        f"{therapeutic_indication}. The APDL PV platform contained "
        f"{total_cases} ADR case(s), including {serious_cases} serious "
        f"case(s), {len(complaints)} market complaint(s), and "
        f"{len(signals)} safety signal(s). {automated_signals} signal(s) "
        "had been identified through ADR screening."
    )

    safety_actions = (
        f"Market-complaint information for {product_name} during "
        f"{period} was reviewed as part of the periodic safety "
        "evaluation.\n\n"
        f"{_complaint_lines(complaints)}"
    )

    reference_safety_information = (
        f"Reference safety information for {product_name} was "
        f"reviewed for the reporting period {period}.\n\n"
        f"{_rsi_lines(rsi_documents)}"
    )

    signal_evaluation = (
        f"The signal and risk evaluation for {product_name} was "
        "based on the safety signals, ADR cases and case-level "
        "listedness and expectedness assessments recorded during "
        f"{period}.\n\n"
        f"{_signal_lines(signals)}\n\n"
        "The following listedness and expectedness assessments were "
        "recorded:\n\n"
        f"{assessment_summary}\n\n"
        "The supporting ADR cases were as follows:\n\n"
        f"{_case_lines(safety_cases)}"
    )

    exposure = (
        f"No validated patient-exposure, sales, distribution or "
        f"prescription denominator for {product_name} was available "
        f"in the APDL PV platform for the period {period}. "
        "Exposure-adjusted reporting rates and comparisons were "
        "therefore not calculated."
    )

    benefit_risk = (
        f"The available evidence for {product_name} comprised ADR "
        "cases, market complaints, safety signals and reference "
        "safety information recorded during the reporting interval. "
        f"The product was recorded for {therapeutic_indication}, and "
        f"its mechanism of action was recorded as {mechanism_of_action}. "
        "No final integrated benefit-risk conclusion had been entered "
        "in the APDL PV platform at the time this report was prepared."
    )

    conclusion = (
        f"The periodic review for {product_name} covered the interval "
        f"from {period}. The record contained {total_cases} ADR case(s), "
        f"{len(complaints)} market complaint(s) and {len(signals)} "
        "safety signal(s). No final product-specific conclusion or "
        "documented risk-minimisation action had been entered in the "
        "APDL PV platform at the time this report was prepared."
    )

    return {
        "executive_summary": executive_summary,
        "introduction": (
            f"This Periodic Benefit-Risk Evaluation Report presented "
            f"the interval safety evaluation for {product_name}, "
            f"containing {active_substances}, for the reporting period "
            f"{period}. The product was recorded for "
            f"{therapeutic_indication}. The evaluation incorporated "
            "ADR cases, market complaints, signal-screening outcomes "
            "and reference safety information available in the APDL "
            "PV platform."
        ),
        "marketing_authorisation_status": (
            f"{product_name} was recorded under marketing authorisation "
            f"number {report['marketing_authorisation_number'] or 'not recorded'} "
            f"and procedure "
            f"{report['marketing_authorisation_procedure'] or 'not recorded'}. "
            f"The PSUR covered the following market(s): {markets}."
        ),
        "safety_actions": safety_actions,
        "reference_safety_information": (
            reference_safety_information
        ),
        "exposure": exposure,
        "signals": signal_evaluation,
        "benefit_risk": benefit_risk,
        "conclusion": conclusion,
    }