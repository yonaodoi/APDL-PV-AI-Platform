from app.db import query_all


def _display_date(value):
    if value is None:
        return "Not recorded"

    return value.strftime("%d %b %Y")


def _case_lines(cases):
    if not cases:
        return "No ADR cases were recorded for this product and period."

    return "\n".join(
        (
            f"- {case['case_number']} | "
            f"{_display_date(case['received_date'])} | "
            f"{'Serious' if case['seriousness'] else 'Non-serious'} | "
            f"{case['workflow_status']} | "
            f"{case['event_description']}"
        )
        for case in cases
    )


def _complaint_lines(complaints):
    if not complaints:
        return (
            "No market complaints were recorded for this product "
            "and period."
        )

    return "\n".join(
        (
            f"- {complaint['complaint_number']} | "
            f"{_display_date(complaint['date_received'])} | "
            f"{complaint['severity']} | "
            f"{complaint['status']} | "
            f"{complaint['complaint_description']}"
        )
        for complaint in complaints
    )


def _signal_lines(signals):
    if not signals:
        return (
            "No safety signals were recorded for this product "
            "and period."
        )

    return "\n".join(
        (
            f"- {signal['signal_number']} | "
            f"{signal['priority']} | {signal['status']} | "
            f"{signal['event_term']} | "
            f"{signal['supporting_case_count']} supporting ADR case(s)"
        )
        for signal in signals
    )

def _rsi_lines(rsi_documents):
    if not rsi_documents:
        return (
            "No reference safety information changes were recorded "
            "for this product during the reporting interval."
        )

    return "\n".join(
        (
            f"- {document['document_type']} | "
            f"Version {document['document_version'] or 'Not recorded'} | "
            f"Market: {document['market'] or 'Not recorded'} | "
            f"Effective: {_display_date(document['effective_date'])}"
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
    assessment_summary = "\n".join(
        (
            f"- {assessment['listedness_status']} / "
            f"{assessment['expectedness_status']}: "
            f"{assessment['total_cases']} case(s)"
        )
        for assessment in case_assessments
    )

    if not assessment_summary:
        assessment_summary = (
            "No ADR listedness or expectedness assessments were "
            "recorded for this product and period."
        )

    period = (
        f"{_display_date(start_date)} to {_display_date(end_date)}"
    )

    executive_summary = (
        f"This draft PSUR covers {product_name} for the reporting "
        f"period {period}. The system identified {total_cases} ADR "
        f"case(s), including {serious_cases} serious case(s); "
        f"{len(complaints)} market complaint(s); and "
        f"{len(signals)} safety signal(s), of which "
        f"{automated_signals} were generated through ADR screening. "
        "This text is a data-based draft and requires QPPV/medical "
        "review before approval."
    )

    safety_actions = (
        "Market complaint evidence for the reporting interval:\n"
        f"{_complaint_lines(complaints)}\n\n"
        "Review any complaint with a potential safety implication "
        "and document the safety action, CAPA, authority "
        "communication or rationale for no action."
    )
    reference_safety_information = (
        "Reference safety information changes recorded during "
        "the reporting interval:\n"
        f"{_rsi_lines(rsi_documents)}\n\n"
        "QPPV/medical reviewer to assess the regulatory and "
        "safety impact of each change and document any action."
    )

    signal_evaluation = (
        "Automatically compiled signal evidence:\n"
        f"{_signal_lines(signals)}\n\n"
        "ADR listedness and expectedness assessment summary:\n"
        f"{assessment_summary}\n\n"
        "Supporting ADR case register:\n"
        f"{_case_lines(safety_cases)}\n\n"
        "QPPV/medical reviewer to document validation, assessment, "
        "risk characterisation and any required action for each "
        "potential signal."
    )

    exposure = (
        "No patient exposure or utilisation denominator is currently "
        "available in the platform. Enter verified sales, "
        "distribution, prescription or exposure information here "
        "where applicable."
    )

    benefit_risk = (
        "Draft for QPPV/medical review: assess the cumulative ADR, "
        "signal and market-complaint evidence above against the "
        "known benefit-risk profile of the product. Document the "
        "final conclusion and any required risk-minimisation action."
    )

    conclusion = (
        "Draft for QPPV approval: state whether the benefit-risk "
        "balance remains favourable for the authorised indications, "
        "identify required actions, and record the responsible "
        "owner and due date."
    )

    return {
        "executive_summary": executive_summary,
        "safety_actions": safety_actions,
        "reference_safety_information": (
            reference_safety_information
        ),
        "exposure": exposure,
        "signals": signal_evaluation,
        "benefit_risk": benefit_risk,
        "conclusion": conclusion,
    }