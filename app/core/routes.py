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

    signal_metrics = query_one(
        """
        SELECT COUNT(*) AS open_signals
        FROM pv.safety_signals
        WHERE status IN ('New', 'Under evaluation')
        """
    )

    complaint_metrics = query_one(
        """
        SELECT
            COUNT(*) AS total_complaints,
            COUNT(*) FILTER (
                WHERE status IN ('New', 'Under investigation')
            ) AS open_complaints
        FROM pv.product_complaints
        """
    )

    psur_metrics = query_one(
        """
        SELECT
            COUNT(*) AS total_psurs,
            COUNT(*) FILTER (
                WHERE status IN ('Draft', 'Under review')
                  AND data_lock_point <= CURRENT_DATE
            ) AS reports_due
        FROM pv.psur_reports
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

    recent_activity = query_all(
        """
        SELECT
            audit_log.action,
            audit_log.details,
            audit_log.occurred_at,
            users.full_name
        FROM pv.audit_log AS audit_log
        LEFT JOIN pv.users AS users
            ON users.user_id = audit_log.actor_user_id

        UNION ALL

        SELECT
            case_audit_log.action,
            case_audit_log.details,
            case_audit_log.performed_at AS occurred_at,
            users.full_name
        FROM pv.case_audit_log AS case_audit_log
        LEFT JOIN pv.users AS users
            ON users.user_id = case_audit_log.performed_by

        ORDER BY occurred_at DESC
        LIMIT 5
        """
    )

    metrics["open_signals"] = signal_metrics["open_signals"]
    metrics["total_complaints"] = complaint_metrics["total_complaints"]
    metrics["open_complaints"] = complaint_metrics["open_complaints"]
    metrics["total_psurs"] = psur_metrics["total_psurs"]
    metrics["reports_due"] = psur_metrics["reports_due"]

    return flask.render_template(
        "dashboard.html",
        metrics=metrics,
        recent_cases=recent_cases,
        recent_activity=recent_activity,
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

@bp.get("/summary")
@login_required
def portfolio_summary():
    case_statuses = query_all(
        """
        SELECT
            workflow_status AS status,
            COUNT(*) AS total
        FROM pv.safety_cases
        GROUP BY workflow_status
        ORDER BY workflow_status
        """
    )

    complaint_statuses = query_all(
        """
        SELECT
            status,
            COUNT(*) AS total
        FROM pv.product_complaints
        GROUP BY status
        ORDER BY status
        """
    )

    signal_statuses = query_all(
        """
        SELECT
            status,
            COUNT(*) AS total
        FROM pv.safety_signals
        GROUP BY status
        ORDER BY status
        """
    )

    psur_statuses = query_all(
        """
        SELECT
            status,
            COUNT(*) AS total
        FROM pv.psur_reports
        GROUP BY status
        ORDER BY status
        """
    )

    return flask.render_template(
        "portfolio_summary.html",
        case_statuses=case_statuses,
        complaint_statuses=complaint_statuses,
        signal_statuses=signal_statuses,
        psur_statuses=psur_statuses,
    )