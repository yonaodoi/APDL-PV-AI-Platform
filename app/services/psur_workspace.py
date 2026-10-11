"""PSUR Workspace counts: report records and their reporting obligations.

A PSUR record is the report itself; its reporting obligation is the
submission deadline worked out from the data lock point
(:func:`app.services.psur_rules.submission_status`). Both are counted from
the same records, so nothing is duplicated and no due date is lost.
"""

from app.services.psur_rules import submission_status

IN_PROGRESS = ("Draft", "Under review")
VIEWS = {
    "in_progress": "Draft or under review",
    "completed": "Completed (finalised or submitted)",
}
DUE = {
    "soon": "Submission due within 30 days",
    "overdue": "Submission overdue",
}


def annotate(reports, today=None):
    """Add the submission deadline state to each report."""
    out = []
    for report in reports:
        report = dict(report)
        report["submission"] = submission_status(report, today)
        out.append(report)
    return out


def is_completed(report):
    return bool(report.get("submitted_date")) or report.get("status") == "Finalised"


def matches(report, view=None, due=None):
    if view == "in_progress" and report.get("status") not in IN_PROGRESS:
        return False
    if view == "completed" and not is_completed(report):
        return False
    state = (report.get("submission") or {}).get("state")
    if due == "soon" and state != "due_soon":
        return False
    if due == "overdue" and state != "overdue":
        return False
    return True


def counts(reports):
    """``reports`` must already be annotated."""
    return {
        "total": len(reports),
        "in_progress": sum(1 for r in reports if matches(r, view="in_progress")),
        "due_soon": sum(1 for r in reports if matches(r, due="soon")),
        "overdue": sum(1 for r in reports if matches(r, due="overdue")),
        "completed": sum(1 for r in reports if matches(r, view="completed")),
    }


def load_counts(query_all):
    """Counts straight from the database (for the dashboard and analytics)."""
    rows = query_all(
        """
        SELECT psur_id, status, reporting_period_start, reporting_period_end,
               data_lock_point, submitted_date
        FROM pv.psur_reports
        """
    )
    return counts(annotate(rows))
