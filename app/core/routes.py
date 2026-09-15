import flask

from app.db import get_db, query_all, query_one
from app.security import login_required


bp = flask.Blueprint("core", __name__)


@bp.get("/")
@login_required
def dashboard():
    metrics = query_one(
        """
        SELECT
            COUNT(*) AS total_cases,
            COUNT(*) FILTER (
                WHERE workflow_status = 'New'
            ) AS new_cases,
            COUNT(*) FILTER (
                WHERE seriousness = TRUE
            ) AS serious_cases
        FROM pv.safety_cases
        """
    )

    recent_cases = query_all(
        """
        SELECT
            case_number,
            workflow_status,
            received_date,
            seriousness
        FROM pv.safety_cases
        ORDER BY created_at DESC
        LIMIT 5
        """
    )

    metrics["open_signals"] = 0
    metrics["reports_due"] = 0

    return flask.render_template(
        "dashboard.html",
        metrics=metrics,
        recent_cases=recent_cases,
    )


@bp.get("/health")
def health():
    return flask.jsonify(
        status="ok",
        service="APDL PV AI Platform",
    )


@bp.get("/health/database")
def database_health():
    try:
        with get_db().cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()

    except Exception:
        return flask.jsonify(
            status="unavailable",
            database="error",
        ), 503

    return flask.jsonify(
        status="ok",
        database="connected",
    )