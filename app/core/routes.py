import csv
import io
from datetime import date
import flask
from app.db import get_db, query_all, query_one
from app.security import login_required
from app.services.regulatory_reporting_docx import (
    build_regulatory_reporting_docx,
)

bp = flask.Blueprint("core", __name__)

@bp.get("/")
@login_required
def dashboard():
    return flask.render_template("dashboard.html")

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

@bp.get("/regulatory-reporting/")
@login_required
def regulatory_reporting():
    today = date.today()
    default_start = today.replace(month=1, day=1)

    try:
        start_date = date.fromisoformat(
            flask.request.args.get(
                "start_date",
                default_start.isoformat(),
            )
        )
    except ValueError:
        start_date = default_start

    try:
        end_date = date.fromisoformat(
            flask.request.args.get(
                "end_date",
                today.isoformat(),
            )
        )
    except ValueError:
        end_date = today

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    selected_product = flask.request.args.get(
        "product",
        "",
    ).strip()
    selected_country = flask.request.args.get(
        "country",
        "",
    ).strip()

    selected_record_type = flask.request.args.get(
        "record_type",
        "",
    ).strip()

    selected_status = flask.request.args.get(
        "status",
        "",
    ).strip()

    products = query_all(
        """
        SELECT product_name
        FROM (
            SELECT product_name
            FROM pv.case_products

            UNION

            SELECT product_name
            FROM pv.product_complaints

            UNION

            SELECT product_name
            FROM pv.safety_signals

            UNION

            SELECT product_name
            FROM pv.psur_reports
        ) AS products
        WHERE product_name IS NOT NULL
          AND product_name <> ''
        ORDER BY product_name
        """
    )
    countries = query_all(
        """
        SELECT country_id, country_name
        FROM pv.countries
        ORDER BY country_name
        """
    )
    statuses = query_all(
        """
        SELECT status
        FROM (
            SELECT workflow_status AS status
            FROM pv.safety_cases

            UNION

            SELECT status
            FROM pv.product_complaints

            UNION

            SELECT status
            FROM pv.safety_signals

            UNION

            SELECT status
            FROM pv.psur_reports
        ) AS statuses
        ORDER BY status
        """
    )


    reporting_records = """
        WITH report_records AS (
            SELECT
                COALESCE(cp.product_name, 'Not recorded')
                    AS product_name,
                COALESCE(c.country_name, 'Not recorded')
                    AS country_name,
                sc.received_date AS report_date,
                'Safety case' AS record_type,
                sc.case_number AS record_number,
                sc.workflow_status AS status,
                sc.seriousness AS serious,
                sc.event_description AS description
            FROM pv.safety_cases AS sc
            LEFT JOIN pv.case_products AS cp
                ON cp.case_id = sc.case_id
            LEFT JOIN pv.countries AS c
                ON c.country_id = sc.country_id

            UNION ALL

            SELECT
                pc.product_name,
                COALESCE(c.country_name, 'Not recorded')
                    AS country_name,
                pc.date_received,
                'Product complaint',
                pc.complaint_number,
                pc.status,
                pc.severity IN ('Serious', 'Critical'),
                pc.complaint_description
            FROM pv.product_complaints AS pc
            LEFT JOIN pv.countries AS c
                ON c.country_id = pc.country_id

            UNION ALL

            SELECT
                ss.product_name,
                'Not recorded' AS country_name,
                ss.date_detected,
                'Safety signal',
                ss.signal_number,
                ss.status,
                ss.priority IN ('High', 'Critical'),
                ss.event_term
            FROM pv.safety_signals AS ss

            UNION ALL

            SELECT
                pr.product_name,
                'Not recorded' AS country_name,
                pr.reporting_period_end,
                'PSUR',
                pr.report_number,
                pr.status,
                FALSE,
                COALESCE(pr.report_notes, 'Periodic safety report')
            FROM pv.psur_reports AS pr
        )
    """

    filters = """
        WHERE report_date BETWEEN %s AND %s
          AND (%s = '' OR product_name = %s)
          AND (%s = '' OR country_name = %s)
          AND (%s = '' OR record_type = %s)
          AND (%s = '' OR status = %s)
    """

    filter_values = (
        start_date,
        end_date,
        selected_product,
        selected_product,
        selected_country,
        selected_country,
        selected_record_type,
        selected_record_type,
        selected_status,
        selected_status,
    )

    metrics = query_one(
        reporting_records
        + """
        SELECT
            COUNT(*) AS total_reports,
            COUNT(*) FILTER (
                WHERE record_type = 'Safety case'
            ) AS safety_cases,
            COUNT(*) FILTER (
                WHERE record_type = 'Product complaint'
            ) AS complaints,
            COUNT(*) FILTER (
                WHERE record_type = 'Safety signal'
            ) AS signals,
            COUNT(*) FILTER (
                WHERE record_type = 'PSUR'
            ) AS psurs,
            COUNT(*) FILTER (
                WHERE serious = TRUE
            ) AS high_priority_reports
        FROM report_records
        """
        + filters,
        filter_values,
    )

    product_summary = query_all(
        reporting_records
        + """
        SELECT
            product_name,
            COUNT(*) AS total_reports,
            COUNT(*) FILTER (
                WHERE record_type = 'Safety case'
            ) AS safety_cases,
            COUNT(*) FILTER (
                WHERE record_type = 'Product complaint'
            ) AS complaints,
            COUNT(*) FILTER (
                WHERE record_type = 'Safety signal'
            ) AS signals,
            COUNT(*) FILTER (
                WHERE record_type = 'PSUR'
            ) AS psurs,
            COUNT(*) FILTER (
                WHERE serious = TRUE
            ) AS high_priority_reports
        FROM report_records
        """
        + filters
        + """
        GROUP BY product_name
        ORDER BY total_reports DESC, product_name
        """,
        filter_values,
    )

    monthly_trend = query_all(
        reporting_records
        + """
        SELECT
            TO_CHAR(
                DATE_TRUNC('month', report_date),
                'Mon YYYY'
            ) AS period_label,
            DATE_TRUNC('month', report_date)::date AS period_date,
            COUNT(*) AS total_reports
        FROM report_records
        """
        + filters
        + """
        GROUP BY DATE_TRUNC('month', report_date)
        ORDER BY period_date
        """,
        filter_values,
    )

    detailed_records = query_all(
        reporting_records
        + """
        SELECT
            product_name,
            report_date,
            record_type,
            record_number,
            status,
            serious,
            description
        FROM report_records
        """
        + filters
        + """
        ORDER BY report_date DESC, record_number DESC
        LIMIT 250
        """,
        filter_values,
    )

    if flask.request.args.get("download") == "word":
        report_file = build_regulatory_reporting_docx(
            start_date=start_date,
            end_date=end_date,
            selected_product=selected_product,
            metrics=metrics,
            monthly_trend=monthly_trend,
            product_summary=product_summary,
        )

        filename = (
            "APDL_regulatory_reporting_summary_"
            f"{start_date.strftime('%Y%m%d')}_"
            f"to_{end_date.strftime('%Y%m%d')}.docx"
        )

        return flask.send_file(
            report_file,
            as_attachment=True,
            download_name=filename,
            mimetype=(
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document"
            ),
        )


    return flask.render_template(
        "regulatory_reporting.html",
        products=products,
        countries=countries,
        selected_country=selected_country,
        selected_record_type=selected_record_type,
        statuses=statuses,
        selected_status=selected_status,
        selected_product=selected_product,
        start_date=start_date,
        end_date=end_date,
        metrics=metrics,
        product_summary=product_summary,
        monthly_trend=monthly_trend,
        detailed_records=detailed_records,
    )

@bp.get("/regulatory-reporting/export.csv")
@login_required
def download_regulatory_reporting_csv():
    today = date.today()
    default_start = today.replace(month=1, day=1)

    try:
        start_date = date.fromisoformat(
            flask.request.args.get(
                "start_date",
                default_start.isoformat(),
            )
        )
    except ValueError:
        start_date = default_start

    try:
        end_date = date.fromisoformat(
            flask.request.args.get(
                "end_date",
                today.isoformat(),
            )
        )
    except ValueError:
        end_date = today

    if end_date < start_date:
        start_date, end_date = end_date, start_date

    selected_product = flask.request.args.get(
        "product",
        "",
    ).strip()
    selected_country = flask.request.args.get(
        "country",
        "",
    ).strip()
    selected_record_type = flask.request.args.get(
        "record_type",
        "",
    ).strip()
    selected_record_type = flask.request.args.get(
        "record_type",
        "",
    ).strip()

    selected_status = flask.request.args.get(
        "status",
        "",
    ).strip()

    records = query_all(
        """
        WITH report_records AS (
            SELECT
                COALESCE(cp.product_name, 'Not recorded')
                    AS product_name,
                COALESCE(c.country_name, 'Not recorded')
                    AS country_name,
                sc.received_date AS report_date,
                'Safety case' AS record_type,
                sc.case_number AS record_number,
                sc.workflow_status AS status,
                sc.seriousness AS serious,
                sc.event_description AS description
            FROM pv.safety_cases AS sc
            LEFT JOIN pv.case_products AS cp
                ON cp.case_id = sc.case_id
            LEFT JOIN pv.countries AS c
                ON c.country_id = sc.country_id

            UNION ALL

            SELECT
                pc.product_name,
                COALESCE(c.country_name, 'Not recorded')
                    AS country_name,
                pc.date_received,
                'Product complaint',
                pc.complaint_number,
                pc.status,
                pc.severity IN ('Serious', 'Critical'),
                pc.complaint_description
            FROM pv.product_complaints AS pc
            LEFT JOIN pv.countries AS c
                ON c.country_id = pc.country_id

            UNION ALL

            SELECT
                ss.product_name,
                'Not recorded' AS country_name,
                ss.date_detected,
                'Safety signal',
                ss.signal_number,
                ss.status,
                ss.priority IN ('High', 'Critical'),
                ss.event_term
            FROM pv.safety_signals AS ss

            UNION ALL

            SELECT
                pr.product_name,
                'Not recorded' AS country_name,
                pr.reporting_period_end,
                'PSUR',
                pr.report_number,
                pr.status,
                FALSE,
                COALESCE(pr.report_notes, 'Periodic safety report')
            FROM pv.psur_reports AS pr
        )
        SELECT
            product_name,
            report_date,
            record_type,
            record_number,
            status,
            serious,
            description
        FROM report_records
        WHERE report_date BETWEEN %s AND %s
          AND (%s = '' OR product_name = %s)
          AND (%s = '' OR country_name = %s)
          AND (%s = '' OR record_type = %s)
          AND (%s = '' OR status = %s)
        ORDER BY report_date DESC, record_number DESC
        """,
        (
            start_date,
            end_date,
            selected_product,
            selected_product,
            selected_country,
            selected_country,
            selected_record_type,
            selected_record_type,
            selected_status,
            selected_status,
        ),
    )

    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(
        [
            "APDL Regulatory Reporting Data Export",
            f"{start_date.isoformat()} to {end_date.isoformat()}",
            selected_product or "All products",
        ]
    )
    writer.writerow([])
    writer.writerow(
        [
            "Report date",
            "Product",
            "Record type",
            "Reference number",
            "Status",
            "Priority",
            "Description",
        ]
    )

    for row in records:
        writer.writerow(
            [
                row["report_date"].isoformat(),
                row["product_name"],
                row["record_type"],
                row["record_number"],
                row["status"],
                "High priority" if row["serious"] else "Routine",
                row["description"],
            ]
        )

    filename = (
        "APDL_regulatory_reporting_"
        f"{start_date.strftime('%Y%m%d')}_"
        f"to_{end_date.strftime('%Y%m%d')}.csv"
    )

    return flask.Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"'
        },
    )

def _analytics_metrics():
    case_metrics = query_one(
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
        SELECT COUNT(*) AS total_complaints
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

    return {
        "total_cases": case_metrics["total_cases"],
        "new_cases": case_metrics["new_cases"],
        "serious_cases": case_metrics["serious_cases"],
        "open_signals": signal_metrics["open_signals"],
        "reports_due": psur_metrics["reports_due"],
        "total_complaints": complaint_metrics["total_complaints"],
        "total_psurs": psur_metrics["total_psurs"],
    }


@bp.get("/analytics/")
@login_required
def analytics_home():
    return flask.render_template(
        "analytics/analytics_home.html",
        metrics=_analytics_metrics(),
    )


@bp.get("/analytics/<metric_key>")
@login_required
def analytics_metric_detail(metric_key):
    definitions = {
        "total-cases": {
            "title": "Total safety cases",
            "description": (
                "All safety cases recorded in the APDL PV platform."
            ),
            "sql": """
                SELECT
                    safety_cases.case_id AS record_id,
                    safety_cases.case_number AS record_number,
                    COALESCE(
                        (
                            SELECT MIN(case_products.product_name)
                            FROM pv.case_products AS case_products
                            WHERE case_products.case_id =
                                safety_cases.case_id
                        ),
                        'Not recorded'
                    ) AS product_name,
                    safety_cases.received_date AS record_date,
                    safety_cases.workflow_status AS status,
                    safety_cases.event_description AS detail
                FROM pv.safety_cases AS safety_cases
                ORDER BY safety_cases.received_date DESC,
                    safety_cases.case_id DESC
            """,
            "endpoint": "cases.case_detail",
            "id_name": "case_id",
        },
        "new-cases": {
            "title": "New safety cases",
            "description": (
                "Safety cases that remained at the New workflow stage."
            ),
            "sql": """
                SELECT
                    safety_cases.case_id AS record_id,
                    safety_cases.case_number AS record_number,
                    COALESCE(
                        (
                            SELECT MIN(case_products.product_name)
                            FROM pv.case_products AS case_products
                            WHERE case_products.case_id =
                                safety_cases.case_id
                        ),
                        'Not recorded'
                    ) AS product_name,
                    safety_cases.received_date AS record_date,
                    safety_cases.workflow_status AS status,
                    safety_cases.event_description AS detail
                FROM pv.safety_cases AS safety_cases
                WHERE safety_cases.workflow_status = 'New'
                ORDER BY safety_cases.received_date DESC,
                    safety_cases.case_id DESC
            """,
            "endpoint": "cases.case_detail",
            "id_name": "case_id",
        },
        "serious-cases": {
            "title": "Serious safety cases",
            "description": (
                "Safety cases recorded as serious in the platform."
            ),
            "sql": """
                SELECT
                    safety_cases.case_id AS record_id,
                    safety_cases.case_number AS record_number,
                    COALESCE(
                        (
                            SELECT MIN(case_products.product_name)
                            FROM pv.case_products AS case_products
                            WHERE case_products.case_id =
                                safety_cases.case_id
                        ),
                        'Not recorded'
                    ) AS product_name,
                    safety_cases.received_date AS record_date,
                    safety_cases.workflow_status AS status,
                    safety_cases.event_description AS detail
                FROM pv.safety_cases AS safety_cases
                WHERE safety_cases.seriousness = TRUE
                ORDER BY safety_cases.received_date DESC,
                    safety_cases.case_id DESC
            """,
            "endpoint": "cases.case_detail",
            "id_name": "case_id",
        },
        "open-signals": {
            "title": "Open safety signals",
            "description": (
                "Signals recorded as New or Under evaluation."
            ),
            "sql": """
                SELECT
                    safety_signals.signal_id AS record_id,
                    safety_signals.signal_number AS record_number,
                    safety_signals.product_name,
                    safety_signals.date_detected AS record_date,
                    safety_signals.status,
                    safety_signals.event_term AS detail
                FROM pv.safety_signals AS safety_signals
                WHERE safety_signals.status IN (
                    'New',
                    'Under evaluation'
                )
                ORDER BY safety_signals.date_detected DESC,
                    safety_signals.signal_id DESC
            """,
            "endpoint": "signals.signal_detail",
            "id_name": "signal_id",
        },
        "reports-due": {
            "title": "PSUR reports due",
            "description": (
                "Draft or under-review PSURs whose data lock point "
                "has been reached."
            ),
            "sql": """
                SELECT
                    psur_reports.psur_id AS record_id,
                    psur_reports.report_number AS record_number,
                    psur_reports.product_name,
                    psur_reports.data_lock_point AS record_date,
                    psur_reports.status,
                    COALESCE(
                        psur_reports.report_notes,
                        'Periodic safety report'
                    ) AS detail
                FROM pv.psur_reports AS psur_reports
                WHERE psur_reports.status IN (
                    'Draft',
                    'Under review'
                )
                  AND psur_reports.data_lock_point <= CURRENT_DATE
                ORDER BY psur_reports.data_lock_point,
                    psur_reports.psur_id DESC
            """,
            "endpoint": "psur.psur_detail",
            "id_name": "psur_id",
        },
        "product-complaints": {
            "title": "Product complaints",
            "description": (
                "All product complaints recorded in the platform."
            ),
            "sql": """
                SELECT
                    product_complaints.complaint_id AS record_id,
                    product_complaints.complaint_number AS record_number,
                    product_complaints.product_name,
                    product_complaints.date_received AS record_date,
                    product_complaints.status,
                    product_complaints.complaint_description AS detail
                FROM pv.product_complaints AS product_complaints
                ORDER BY product_complaints.date_received DESC,
                    product_complaints.complaint_id DESC
            """,
            "endpoint": "complaints.complaint_detail",
            "id_name": "complaint_id",
        },
        "psur-records": {
            "title": "PSUR records",
            "description": (
                "All periodic safety reports recorded in the platform."
            ),
            "sql": """
                SELECT
                    psur_reports.psur_id AS record_id,
                    psur_reports.report_number AS record_number,
                    psur_reports.product_name,
                    psur_reports.reporting_period_end AS record_date,
                    psur_reports.status,
                    COALESCE(
                        psur_reports.report_notes,
                        'Periodic safety report'
                    ) AS detail
                FROM pv.psur_reports AS psur_reports
                ORDER BY psur_reports.reporting_period_end DESC,
                    psur_reports.psur_id DESC
            """,
            "endpoint": "psur.psur_detail",
            "id_name": "psur_id",
        },
    }

    definition = definitions.get(metric_key)

    if definition is None:
        flask.abort(404)

    selected_product = flask.request.args.get(
        "product",
        "",
    ).strip()
    selected_status = flask.request.args.get(
        "status",
        "",
    ).strip()
    start_date_text = flask.request.args.get(
        "start_date",
        "",
    ).strip()
    end_date_text = flask.request.args.get(
        "end_date",
        "",
    ).strip()

    try:
        start_date = (
            date.fromisoformat(start_date_text)
            if start_date_text
            else None
        )
    except ValueError:
        start_date = None
        start_date_text = ""

    try:
        end_date = (
            date.fromisoformat(end_date_text)
            if end_date_text
            else None
        )
    except ValueError:
        end_date = None
        end_date_text = ""

    base_sql = definition["sql"].strip().rstrip(";")

    products = query_all(
        f"""
        SELECT DISTINCT product_name
        FROM ({base_sql}) AS metric_records
        WHERE product_name IS NOT NULL
          AND product_name <> ''
        ORDER BY product_name
        """
    )

    statuses = query_all(
        f"""
        SELECT DISTINCT status
        FROM ({base_sql}) AS metric_records
        WHERE status IS NOT NULL
          AND status <> ''
        ORDER BY status
        """
    )

    filters = []
    parameters = []

    if selected_product:
        filters.append("product_name = %s")
        parameters.append(selected_product)

    if selected_status:
        filters.append("status = %s")
        parameters.append(selected_status)

    if start_date:
        filters.append("record_date >= %s")
        parameters.append(start_date)

    if end_date:
        filters.append("record_date <= %s")
        parameters.append(end_date)

    records_sql = (
        f"SELECT * FROM ({base_sql}) AS metric_records"
    )

    if filters:
        records_sql += " WHERE " + " AND ".join(filters)

    records_sql += (
        " ORDER BY record_date DESC NULLS LAST, "
        "record_number DESC"
    )

    records = query_all(
        records_sql,
        tuple(parameters),
    )

    for record in records:
        record["record_url"] = flask.url_for(
            definition["endpoint"],
            **{definition["id_name"]: record["record_id"]},
        )

    return flask.render_template(
        "analytics/analytics_metric_detail.html",
        title=definition["title"],
        description=definition["description"],
        records=records,
        products=products,
        statuses=statuses,
        selected_product=selected_product,
        selected_status=selected_status,
        start_date=start_date_text,
        end_date=end_date_text,
    )