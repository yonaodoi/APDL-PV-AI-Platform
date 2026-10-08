"""What needs someone's attention across all modules, for the dashboard.

Each item is a short line with a count and links to the records behind it.
A failing query hides only its own item, never the dashboard.
"""

from flask import current_app, url_for

from app.db import get_db, query_all
from app.services.complaint_workflow import closure_blockers, closure_checks
from app.services.signal_workflow import suggest_signal_status

MAX_LINKS = 5


def _safe_rows(sql, parameters=()):
    try:
        return query_all(sql, parameters)
    except Exception as error:
        current_app.logger.warning("Dashboard action query failed: %s", error)
        try:
            get_db().rollback()
        except Exception:
            pass
        return None


def _item(key, title, hint, records, list_url=None, urgent=False):
    return {
        "key": key,
        "title": title,
        "hint": hint,
        "count": len(records),
        "records": records[:MAX_LINKS],
        "more": max(len(records) - MAX_LINKS, 0),
        "list_url": list_url,
        "urgent": urgent,
    }


def _case_items():
    items = []
    ready = _safe_rows(
        """
        SELECT case_id, case_number
        FROM pv.safety_cases
        WHERE workflow_status = 'Ready for submission'
          AND regulatory_submitted_date IS NULL
        ORDER BY received_date, case_id
        """
    )
    if ready:
        items.append(_item(
            "cases-ready",
            "Safety case(s) ready to submit",
            "Submit to the regulator, then use “Mark as submitted” on the case.",
            [
                {"label": r["case_number"], "url": url_for("cases.case_detail", case_id=r["case_id"]) + "#workflow"}
                for r in ready
            ],
            url_for("cases.case_list", status="Ready for submission"),
        ))

    automatic = _safe_rows(
        """
        SELECT cases.case_id, cases.case_number
        FROM pv.case_safety_assessments AS assessments
        JOIN pv.safety_cases AS cases ON cases.case_id = assessments.case_id
        WHERE assessments.assessment_source = 'automatic'
          AND cases.workflow_status NOT IN ('Submitted', 'Closed')
        ORDER BY cases.received_date, cases.case_id
        """
    )
    if automatic:
        items.append(_item(
            "cases-listedness",
            "Automatic listedness result(s) to confirm",
            "A reviewer must confirm the automatic listedness check before submission.",
            [
                {"label": r["case_number"], "url": url_for("cases.case_detail", case_id=r["case_id"]) + "#listedness"}
                for r in automatic
            ],
        ))
    return items


def _complaint_items():
    rows = _safe_rows(
        """
        SELECT complaints.*, cases.case_number AS linked_case_number
        FROM pv.product_complaints AS complaints
        LEFT JOIN pv.safety_cases AS cases ON cases.case_id = complaints.linked_case_id
        WHERE complaints.status <> 'Investigation complete'
        ORDER BY complaints.date_received, complaints.complaint_id
        """
    )
    if not rows:
        return []

    def link(row, anchor="#investigation-panel"):
        return {
            "label": row["complaint_number"],
            "url": url_for("complaints.complaint_detail", complaint_id=row["complaint_id"]) + anchor,
        }

    no_case = [
        link(r, "")
        for r in rows
        if r.get("complaint_category") == "Adverse event" and not r.get("linked_case_id")
    ]
    ready = [link(r) for r in rows if not closure_blockers(closure_checks(r))]

    items = []
    if no_case:
        items.append(_item(
            "complaints-no-case",
            "Adverse-event complaint(s) without a safety case",
            "The reporting clock is running. Create the safety case from the complaint.",
            no_case,
            urgent=True,
        ))
    if ready:
        items.append(_item(
            "complaints-ready",
            "Complaint investigation(s) ready to close",
            "Everything needed is recorded. Check and mark the investigation complete.",
            ready,
        ))
    return items


def _signal_items():
    rows = _safe_rows(
        """
        SELECT signal_id, signal_number, status, owner_name,
               assessment_summary, decision_summary
        FROM pv.safety_signals
        WHERE status <> 'Closed'
        ORDER BY date_detected, signal_id
        """
    )
    if not rows:
        return []
    groups = {"start": [], "decide": [], "close": []}
    for row in rows:
        suggestion = suggest_signal_status(row)
        if not suggestion:
            continue
        record = {
            "label": row["signal_number"],
            "url": url_for("signals.signal_detail", signal_id=row["signal_id"]) + "#evaluation-panel",
        }
        if row["status"] == "New":
            groups["start"].append(record)
        elif suggestion.get("choices"):
            groups["decide"].append(record)
        elif suggestion["status"] == "Closed" and not suggestion["same"]:
            groups["close"].append(record)

    items = []
    if groups["start"]:
        items.append(_item(
            "signals-new",
            "New signal(s) to start evaluating",
            "Assign an owner and move each signal to Under evaluation.",
            groups["start"],
            url_for("signals.signal_list", status="New"),
        ))
    if groups["decide"]:
        items.append(_item(
            "signals-decide",
            "Signal(s) waiting for a decision",
            "The assessment is recorded. Decide Validated or Closed.",
            groups["decide"],
        ))
    if groups["close"]:
        items.append(_item(
            "signals-close",
            "Validated signal(s) ready to close",
            "A decision is recorded. Close each one once its actions are complete.",
            groups["close"],
        ))
    return items


def _document_items():
    rows = _safe_rows(
        """
        SELECT attachments.record_type, attachments.record_id,
               attachments.original_filename,
               cases.case_number, complaints.complaint_number
        FROM pv.record_attachments AS attachments
        LEFT JOIN pv.safety_cases AS cases
            ON attachments.record_type = 'case' AND cases.case_id = attachments.record_id
        LEFT JOIN pv.product_complaints AS complaints
            ON attachments.record_type = 'complaint' AND complaints.complaint_id = attachments.record_id
        WHERE attachments.processing_status = 'Suggestions ready'
        ORDER BY attachments.uploaded_at
        """
    )
    if not rows:
        return []
    records = []
    for row in rows:
        if row["record_type"] == "case" and row.get("case_number"):
            url = url_for("cases.case_detail", case_id=row["record_id"]) + "#attachments"
            label = row["case_number"]
        elif row["record_type"] == "complaint" and row.get("complaint_number"):
            url = url_for("complaints.complaint_detail", complaint_id=row["record_id"]) + "#attachments"
            label = row["complaint_number"]
        else:
            continue
        records.append({"label": f"{label} · {row['original_filename']}", "url": url})
    if not records:
        return []
    return [_item(
        "documents",
        "Attached document(s) with suggested updates",
        "The AI read these documents. Review and apply or dismiss the suggestions.",
        records,
    )]


def _follow_up_items():
    from app.services.follow_up_automation import (
        email_configured,
        reminder_status,
    )

    items = []
    config = current_app.config
    try:
        status = reminder_status(
            reminder_days=config.get("FOLLOW_UP_REMINDER_DAYS", 7),
            max_reminders=config.get("FOLLOW_UP_MAX_REMINDERS", 2),
        )
    except Exception as error:
        current_app.logger.warning("Dashboard follow-up query failed: %s", error)
        try:
            get_db().rollback()
        except Exception:
            pass
        status = {}
    exhausted = [case_id for case_id, info in status.items() if info["exhausted"]]
    if exhausted:
        rows = _safe_rows(
            "SELECT case_id, case_number FROM pv.safety_cases WHERE case_id = ANY(%s) ORDER BY case_number",
            (exhausted,),
        ) or []
        items.append(_item(
            "follow-up-no-reply",
            "Reporter(s) not replying after reminders",
            "Automatic reminders have run out. Phone the reporter, or record that "
            "the information is not available.",
            [
                {"label": r["case_number"],
                 "url": url_for("case_review.follow_up_tasks") + f"#case-{r['case_id']}"}
                for r in rows
            ],
            url_for("case_review.follow_up_tasks"),
            urgent=True,
        ))

    if config.get("FOLLOW_UP_AUTO_SEND") and not email_configured():
        waiting = _safe_rows(
            """
            SELECT DISTINCT cases.case_id, cases.case_number
            FROM pv.case_follow_up_tasks AS tasks
            JOIN pv.safety_cases AS cases ON cases.case_id = tasks.case_id
            WHERE tasks.status IN ('Open', 'In progress')
              AND cases.workflow_status NOT IN ('Submitted', 'Closed')
            ORDER BY cases.case_number
            """
        ) or []
        if waiting:
            items.append(_item(
                "follow-up-email-setup",
                "Case(s) waiting because follow-up email is not set up",
                "Set up email sending once and requests and reminders go out on their own.",
                [
                    {"label": r["case_number"],
                     "url": url_for("case_review.follow_up_tasks") + f"#case-{r['case_id']}"}
                    for r in waiting
                ],
                url_for("case_review.follow_up_tasks"),
            ))
    return items


def get_action_items():
    """All dashboard action items, urgent ones first."""
    items = []
    for builder in (_complaint_items, _follow_up_items, _case_items, _signal_items, _document_items):
        try:
            items.extend(builder())
        except Exception:
            current_app.logger.exception("Building dashboard actions failed")
    items.sort(key=lambda item: not item["urgent"])
    return items
