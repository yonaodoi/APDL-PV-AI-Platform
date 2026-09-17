import json
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    jsonify,
    send_file,
)

from app.db import query_all, query_one, transaction
from app.security import login_required
from app.services.safety_signal_reporting_docx import (
    build_safety_signal_reporting_docx,
)
from app.signals.forms import SafetySignalForm, SignalEvaluationForm
from app.services.signal_detection import (
    run_signal_detection_for_all_cases,
)

bp = Blueprint("signals", __name__, url_prefix="/signals")


@bp.get("/")
@login_required
def signal_list():
    selected_product = request.args.get("product", "").strip()
    selected_status = request.args.get("status", "").strip()
    selected_priority = request.args.get("priority", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    filters = []
    parameters = []

    if selected_product:
        filters.append("product_name = %s")
        parameters.append(selected_product)

    if selected_status:
        filters.append("status = %s")
        parameters.append(selected_status)

    if selected_priority:
        filters.append("priority = %s")
        parameters.append(selected_priority)

    if start_date:
        filters.append("date_detected >= %s")
        parameters.append(start_date)

    if end_date:
        filters.append("date_detected <= %s")
        parameters.append(end_date)

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    signals = query_all(
        f"""
        SELECT
            signal_id,
            signal_number,
            date_detected,
            product_name,
            event_term,
            signal_source,
            priority,
            status,
            owner_name
        FROM pv.safety_signals
        {where_clause}
        ORDER BY date_detected DESC, signal_id DESC
        """,
        tuple(parameters),
    )

    products = query_all(
        """
        SELECT DISTINCT product_name
        FROM pv.safety_signals
        WHERE product_name IS NOT NULL
          AND product_name <> ''
        ORDER BY product_name
        """
    )

    statuses = [
        {"status": "New"},
        {"status": "Under evaluation"},
        {"status": "Validated"},
        {"status": "Closed"},
    ]

    priorities = [
        {"priority": "Low"},
        {"priority": "Medium"},
        {"priority": "High"},
        {"priority": "Critical"},
    ]

    return render_template(
        "signals/signal_list.html",
        signals=signals,
        products=products,
        statuses=statuses,
        priorities=priorities,
        selected_product=selected_product,
        selected_status=selected_status,
        selected_priority=selected_priority,
        start_date=start_date,
        end_date=end_date,
    )
@bp.get("/notifications")
@login_required
def signal_notifications():
    notifications = query_all(
        """
        SELECT
            notifications.notification_id,
            notifications.message,
            notifications.is_read,
            notifications.created_at,
            safety_signals.signal_id,
            safety_signals.signal_number,
            safety_signals.product_name,
            safety_signals.event_term
        FROM pv.safety_signal_notifications AS notifications
        INNER JOIN pv.safety_signals AS safety_signals
            ON safety_signals.signal_id = notifications.signal_id
        WHERE notifications.user_id = %s
        ORDER BY
            notifications.is_read,
            notifications.created_at DESC
        """,
        (session["user_id"],),
    )

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_signal_notifications
            SET is_read = TRUE
            WHERE user_id = %s
              AND is_read = FALSE
            """,
            (session["user_id"],),
        )

    return render_template(
        "signals/signal_notifications.html",
        notifications=notifications,
    )
@bp.post("/screen-adr-cases")
@login_required
def screen_adr_cases():
    detected_signals = run_signal_detection_for_all_cases(
        actor_user_id=session["user_id"],
    )

    created_count = sum(
        1 for signal in detected_signals
        if signal["created"]
    )

    if created_count:
        flash(
            f"{created_count} potential safety signal(s) "
            "were detected. QPPV review is required.",
            "warning",
        )
    else:
        flash(
            "ADR case screening completed. No new potential "
            "safety signals met the current threshold.",
            "success",
        )

    return redirect(url_for("signals.signal_list"))
@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_signal():
    flash(
        "Safety signals are generated automatically from ADR case screening.",
        "info",
    )
    return redirect(url_for("signals.signal_list"))

    form = SafetySignalForm()

    if form.validate_on_submit():
        existing_signal = query_one(
            """
            SELECT signal_id
            FROM pv.safety_signals
            WHERE signal_number = %s
            """,
            (form.signal_number.data.strip(),),
        )

        if existing_signal:
            form.signal_number.errors.append(
                "This Signal ID already exists."
            )

            return render_template("signals/create_signal.html", form=form)

        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.safety_signals (
                    signal_number,
                    date_detected,
                    product_name,
                    event_term,
                    signal_source,
                    signal_description,
                    priority,
                    owner_name,
                    auto_detected,
                    created_by
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    form.signal_number.data.strip(),
                    form.date_detected.data,
                    form.product_name.data.strip(),
                    form.event_term.data.strip(),
                    form.signal_source.data,
                    form.signal_description.data.strip(),
                    form.priority.data,
                    form.owner_name.data.strip() or None,
                    session["user_id"],
                ),
            )

        flash("Safety signal saved successfully.", "success")
        return redirect(url_for("signals.signal_list"))

    return render_template("signals/create_signal.html", form=form)


@bp.get("/<int:signal_id>")
@login_required
def signal_detail(signal_id):
    signal = query_one(
        """
        SELECT
            ss.*,
            u.full_name AS created_by_name
        FROM pv.safety_signals ss
        LEFT JOIN pv.users u ON u.user_id = ss.created_by
        WHERE ss.signal_id = %s
        """,
        (signal_id,),
    )

    if not signal:
        abort(404)

    evaluation_form = SignalEvaluationForm()
    evaluation_form.status.data = signal["status"]
    evaluation_form.assessment_summary.data = signal["assessment_summary"]
    evaluation_form.decision_summary.data = signal["decision_summary"]
    evaluation_form.owner_name.data = signal["owner_name"]
    supporting_cases = query_all(
        """
        SELECT
            safety_cases.case_id,
            safety_cases.case_number,
            safety_cases.received_date,
            safety_cases.seriousness,
            safety_cases.event_description,
            countries.country_name
        FROM pv.safety_signal_cases AS safety_signal_cases
        INNER JOIN pv.safety_cases AS safety_cases
            ON safety_cases.case_id = safety_signal_cases.case_id
        LEFT JOIN pv.countries AS countries
            ON countries.country_id = safety_cases.country_id
        WHERE safety_signal_cases.signal_id = %s
        ORDER BY
            safety_cases.received_date DESC,
            safety_cases.case_id DESC
        """,
        (signal_id,),
    )

    return render_template(
        "signals/signal_detail.html",
        signal=signal,
        evaluation_form=evaluation_form,
        supporting_cases=supporting_cases,
    )


@bp.post("/<int:signal_id>/evaluate")
@login_required
def evaluate_signal(signal_id):
    signal = query_one(
        """
        SELECT signal_id
        FROM pv.safety_signals
        WHERE signal_id = %s
        """,
        (signal_id,),
    )

    if not signal:
        abort(404)

    form = SignalEvaluationForm()

    if not form.validate_on_submit():
        flash("Please correct the signal evaluation form.", "error")
        return redirect(url_for("signals.signal_detail", signal_id=signal_id))

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_signals
            SET
                status = %s,
                assessment_summary = %s,
                decision_summary = %s,
                owner_name = %s,
                updated_at = NOW()
            WHERE signal_id = %s
            """,
            (
                form.status.data,
                form.assessment_summary.data.strip() or None,
                form.decision_summary.data.strip() or None,
                form.owner_name.data.strip() or None,
                signal_id,
            ),
        )

    flash("Signal evaluation saved successfully.", "success")
    return redirect(url_for("signals.signal_detail", signal_id=signal_id))

def _get_filtered_signals():
    selected_product = request.args.get("product", "").strip()
    selected_status = request.args.get("status", "").strip()
    selected_priority = request.args.get("priority", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    filters = []
    parameters = []

    if selected_product:
        filters.append("product_name = %s")
        parameters.append(selected_product)

    if selected_status:
        filters.append("status = %s")
        parameters.append(selected_status)

    if selected_priority:
        filters.append("priority = %s")
        parameters.append(selected_priority)

    if start_date:
        filters.append("date_detected >= %s")
        parameters.append(start_date)

    if end_date:
        filters.append("date_detected <= %s")
        parameters.append(end_date)

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    signals = query_all(
        f"""
        SELECT
            signal_id,
            signal_number,
            date_detected,
            product_name,
            event_term,
            signal_source,
            priority,
            status,
            owner_name,
            auto_detected
        FROM pv.safety_signals
        {where_clause}
        ORDER BY date_detected DESC, signal_id DESC
        """,
        tuple(parameters),
    )

    report_filters = {
        "product": selected_product,
        "status": selected_status,
        "priority": selected_priority,
        "start_date": start_date,
        "end_date": end_date,
        "reporting_period": (
            f"{start_date or 'All dates'} to "
            f"{end_date or 'All dates'}"
        ),
    }

    return signals, report_filters


@bp.get("/reporting-summary/preview")
@login_required
def preview_safety_signal_reporting_summary():
    signals, report_filters = _get_filtered_signals()
    unread_notification_count = query_one(
        """
        SELECT COUNT(*) AS total
        FROM pv.safety_signal_notifications
        WHERE user_id = %s
          AND is_read = FALSE
        """,
        (session["user_id"],),
    )["total"]
    return render_template(
        "signals/safety_signal_report_preview.html",
        signals=signals,
        unread_notification_count=unread_notification_count,
        report_filters=report_filters,
    )


@bp.get("/reporting-summary/download")
@login_required
def download_safety_signal_reporting_summary():
    signals, report_filters = _get_filtered_signals()
    draft_id = request.args.get("draft_id", type=int)
    editable_content = None

    if draft_id:
        draft = query_one(
            """
            SELECT report_content
            FROM pv.safety_signal_reporting_drafts
            WHERE draft_id = %s
              AND created_by = %s
            """,
            (draft_id, session["user_id"]),
        )

        if draft is None:
            abort(404)

        editable_content = draft["report_content"]

    report_file = build_safety_signal_reporting_docx(
        signals=signals,
        report_filters=report_filters,
        editable_content=editable_content,
    )

    return send_file(
        report_file,
        as_attachment=True,
        download_name="APDL_safety_signal_reporting_summary.docx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
    )


@bp.post("/reporting-summary/drafts")
@login_required
def save_safety_signal_reporting_draft():
    try:
        filter_state = json.loads(
            request.form.get("filter_state", "{}")
        )
        report_content = json.loads(
            request.form.get("report_content", "{}")
        )
    except json.JSONDecodeError:
        return jsonify(
            status="error",
            message="Invalid draft data.",
        ), 400

    if not isinstance(filter_state, dict):
        return jsonify(
            status="error",
            message="Invalid filter information.",
        ), 400

    if not isinstance(report_content, dict):
        return jsonify(
            status="error",
            message="Invalid report content.",
        ), 400

    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.safety_signal_reporting_drafts (
                filter_state,
                report_content,
                created_by
            )
            VALUES (%s::jsonb, %s::jsonb, %s)
            RETURNING draft_id, updated_at
            """,
            (
                json.dumps(filter_state),
                json.dumps(report_content),
                session["user_id"],
            ),
        )
        draft = cursor.fetchone()

    return jsonify(
        status="saved",
        draft_id=draft["draft_id"],
        updated_at=draft["updated_at"].isoformat(),
    )