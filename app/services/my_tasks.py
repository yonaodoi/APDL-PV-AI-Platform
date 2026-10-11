"""Everything waiting for the signed-in person, in one list."""

from datetime import date

from app.db import get_db, query_all


def _safe(sql, params=()):
    try:
        return query_all(sql, params)
    except Exception:
        try:
            get_db().rollback()
        except Exception:
            pass
        return []


def collect(user_id, role, designated_qppv=False):
    from app.services.case_approval import can_approve, can_review

    sections = []
    today = date.today()

    cases = _safe(
        """
        SELECT case_id, case_number, workflow_status, seriousness, received_date, validity_decision
        FROM pv.safety_cases
        WHERE assigned_to = %s AND regulatory_submitted_date IS NULL
          AND workflow_status IN ('New', 'Triage', 'Follow-up requested', 'Assessment', 'Approved')
        ORDER BY seriousness DESC NULLS LAST, received_date
        LIMIT 100
        """,
        (user_id,),
    )
    sections.append({"key": "cases", "title": "My safety cases", "empty": "No open cases assigned to you.",
                     "items": [{"label": c["case_number"], "detail": f"{c['workflow_status']}"
                                + (" · serious" if c.get("seriousness") else "")
                                + (f" · received {c['received_date']:%d %b %Y}" if c.get("received_date") else ""),
                                "href": ("cases.case_detail", {"case_id": c["case_id"]}, "#pathway"),
                                "tone": "bad" if c.get("seriousness") else ""} for c in cases]})

    stages = []
    if can_review(role, designated_qppv):
        stages.append(("QPPV review", "Waiting for your review"))
    if can_approve(role, designated_qppv=designated_qppv):
        stages.append(("Case approval", "Waiting for your approval"))
    for status, title in stages:
        rows = _safe(
            "SELECT case_id, case_number, seriousness, approval_updated_at FROM pv.safety_cases "
            "WHERE workflow_status = %s ORDER BY seriousness DESC NULLS LAST, approval_updated_at LIMIT 100",
            (status,),
        )
        sections.append({"key": status, "title": title, "empty": "Nothing waiting.",
                         "items": [{"label": r["case_number"], "detail": "serious" if r.get("seriousness") else "",
                                    "href": ("cases.case_detail", {"case_id": r["case_id"]}, "#approval"),
                                    "tone": "bad" if r.get("seriousness") else ""} for r in rows]})

    tasks = _safe(
        """
        SELECT t.task_id, t.task_title, t.due_date, t.status, c.case_id, c.case_number
        FROM pv.case_follow_up_tasks AS t JOIN pv.safety_cases AS c ON c.case_id = t.case_id
        WHERE t.assigned_to = %s AND t.status IN ('Open', 'In progress')
        ORDER BY t.due_date LIMIT 100
        """,
        (user_id,),
    )
    sections.append({"key": "follow_up", "title": "My follow-up tasks", "empty": "No open follow-up tasks.",
                     "items": [{"label": f"{t['case_number']}: {t['task_title']}",
                                "detail": f"due {t['due_date']:%d %b %Y}" + (" · overdue" if t["due_date"] < today else ""),
                                "href": ("case_review.follow_up_tasks", {}, f"#case-{t['case_id']}"),
                                "tone": "bad" if t["due_date"] < today else ""} for t in tasks]})

    from app.services.narratives import REVIEW_ROLES

    if role in REVIEW_ROLES:
        rows = _safe(
            """
            SELECT n.case_id, c.case_number, n.version FROM pv.case_narratives AS n
            JOIN pv.safety_cases AS c ON c.case_id = n.case_id
            WHERE n.status = 'Current' AND COALESCE(n.created_by, 0) <> %s
            ORDER BY n.created_at LIMIT 100
            """,
            (user_id,),
        )
        sections.append({"key": "narratives", "title": "Narratives to review", "empty": "No narratives waiting for review.",
                         "items": [{"label": r["case_number"], "detail": f"version {r['version']}",
                                    "href": ("cases.case_narrative", {"case_id": r["case_id"]}, ""), "tone": ""} for r in rows]})

    rows = _safe(
        """
        SELECT document_id, original_filename, status FROM pv.intake_documents
        WHERE uploaded_by = %s AND status IN ('Ready', 'Failed')
          AND EXISTS (SELECT 1 FROM pv.intake_proposals p WHERE p.document_id = intake_documents.document_id AND p.status = 'Proposed')
        ORDER BY uploaded_at DESC LIMIT 50
        """,
        (user_id,),
    )
    sections.append({"key": "intake", "title": "Documents to review", "empty": "No uploaded documents waiting for you.",
                     "items": [{"label": r["original_filename"], "detail": "patient reports to confirm",
                                "href": ("cases.intake_document", {"document_id": r["document_id"]}, ""), "tone": ""} for r in rows]})

    if role in ("System Administrator", "QPPV", "Deputy QPPV", "Group Head RA & Quality"):
        rows = _safe("SELECT local_code, preferred_term AS name, 'event' AS kind FROM pv.event_terms WHERE status = 'Proposed' "
                     "UNION ALL SELECT local_code, name, 'product' FROM pv.local_product_terms WHERE status = 'Proposed' LIMIT 100")
        sections.append({"key": "terms", "title": "Local terms to approve", "empty": "No local terms waiting.",
                         "items": [{"label": f"{r['local_code']} {r['name']}", "detail": r["kind"],
                                    "href": ("terminology.local_terms", {"kind": r["kind"], "status": "Proposed"}, ""), "tone": ""}
                                   for r in rows]})
    return sections
