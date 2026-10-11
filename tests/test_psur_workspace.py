from datetime import date

from app.services import psur_workspace


def _r(status, dlp, submitted=None, start=None):
    return {"status": status, "data_lock_point": dlp, "reporting_period_start": start or date(dlp.year, 1, 1),
            "reporting_period_end": dlp, "submitted_date": submitted}


def test_counts_keep_records_and_deadlines_apart():
    today = date(2026, 10, 11)
    reports = psur_workspace.annotate([
        _r("Finalised", date(2025, 6, 30), submitted=date(2025, 8, 20)),
        _r("Under review", date(2025, 12, 31)),
        _r("Draft", date(2026, 8, 31)),
        _r("Approved", date(2026, 9, 30)),
    ], today)
    counts = psur_workspace.counts(reports)
    assert counts == {"total": 4, "in_progress": 2, "due_soon": 1, "overdue": 1, "completed": 1}
    # A submitted report is never counted as due, whatever its dates.
    assert not psur_workspace.matches(reports[0], due="overdue")
    assert [r["status"] for r in reports if psur_workspace.matches(r, due="soon")] == ["Draft"]
