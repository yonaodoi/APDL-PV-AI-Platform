import json
import re

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    jsonify,
    send_file,
)

from app.audit import write_audit_log
from app.db import query_all, query_one, transaction
from app.services.record_changes import describe_changes
from app.attachments.routes import list_record_attachments
from app.security import login_required
from app.services.safety_signal_reporting_docx import (
    build_safety_signal_reporting_docx,
)
from app.signals.forms import SafetySignalForm, SignalEvaluationForm
from app.services.signal_workflow import (
    draft_signal_assessment_note,
    evaluation_checks,
    suggest_signal_status,
    validate_signal_evaluation,
)
from app.services.signal_detection import (
    run_signal_detection_for_all_cases,
)
from app.services.safety_signal_assistance import (
    draft_safety_signal_assessment,
)
from app.services import company_profile as company_profile_service

bp = Blueprint("signals", __name__, url_prefix="/signals")

OPEN_SIGNAL_STATUSES = ("New", "Under evaluation", "Validated")


def normalise_signal_number(value):
    """Remove stray spaces around hyphens, e.g. 'APDL -SIG-003' -> 'APDL-SIG-003'."""
    value = re.sub(r"\s+", " ", (value or "").strip())
    return re.sub(r"\s*-\s*", "-", value)


def find_open_duplicate_signal(product_name, event_term):
    return query_one(
        """
        SELECT signal_id, signal_number, status, date_detected
        FROM pv.safety_signals
        WHERE LOWER(REGEXP_REPLACE(TRIM(product_name), '\\s+', ' ', 'g'))
              = LOWER(REGEXP_REPLACE(TRIM(%s), '\\s+', ' ', 'g'))
          AND LOWER(REGEXP_REPLACE(TRIM(event_term), '\\s+', ' ', 'g'))
              = LOWER(REGEXP_REPLACE(TRIM(%s), '\\s+', ' ', 'g'))
          AND status IN %s
        ORDER BY date_detected DESC, signal_id DESC
        LIMIT 1
        """,
        (product_name, event_term, OPEN_SIGNAL_STATUSES),
    )


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
        unread_notification_count=unread_notification_count,
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

    created_count = len(
        {signal["signal_id"] for signal in detected_signals if signal["created"]}
    )
    linked_count = sum(
        signal["newly_linked_cases"] for signal in detected_signals
    )

    if created_count or linked_count:
        flash(
            f"Screening complete: {created_count} new potential signal(s), "
            f"{linked_count} case link(s) added to signals. "
            "QPPV review is required.",
            "warning",
        )
    else:
        flash(
            "Screening complete. No new potential signals met the "
            "current triggers.",
            "success",
        )

    return redirect(url_for("signals.signal_list"))
@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_signal():
    form = SafetySignalForm()

    def render_form(duplicate_signal=None):
        return render_template(
            "signals/create_signal.html",
            form=form,
            duplicate_signal=duplicate_signal,
        )

    if form.validate_on_submit():
        signal_number = normalise_signal_number(form.signal_number.data)

        existing_signal = query_one(
            """
            SELECT signal_id
            FROM pv.safety_signals
            WHERE signal_number = %s
            """,
            (signal_number,),
        )

        if existing_signal:
            form.signal_number.errors.append(
                "This Signal ID already exists."
            )
            return render_form()

        duplicate_signal = find_open_duplicate_signal(
            form.product_name.data,
            form.event_term.data,
        )
        if duplicate_signal and not form.confirm_separate_signal.data:
            return render_form(duplicate_signal)

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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s)
                """,
                (
                    signal_number,
                    form.date_detected.data,
                    form.product_name.data.strip(),
                    form.event_term.data.strip(),
                    form.signal_source.data,
                    form.signal_description.data.strip(),
                    form.priority.data,
                    (form.owner_name.data or "").strip() or None,
                    session["user_id"],
                ),
            )

        flash(f"Safety signal {signal_number} saved successfully.", "success")
        return redirect(url_for("signals.signal_list"))

    return render_form()


SIGNAL_FIELD_LABELS = {
    "signal_number": "Signal ID",
    "date_detected": "Date detected",
    "product_name": "Product",
    "event_term": "Event term",
    "signal_source": "Source",
    "signal_description": "Description",
    "priority": "Priority",
    "owner_name": "Owner",
}


@bp.route("/<int:signal_id>/edit", methods=["GET", "POST"])
@login_required
def edit_signal(signal_id):
    signal = query_one(
        "SELECT * FROM pv.safety_signals WHERE signal_id = %s",
        (signal_id,),
    )
    if not signal:
        abort(404)

    form = SafetySignalForm()
    detail_url = url_for("signals.signal_detail", signal_id=signal_id) + "#detail-identification"

    def render_form():
        return render_template(
            "signals/create_signal.html",
            form=form,
            duplicate_signal=None,
            editing=True,
            signal=signal,
            cancel_url=detail_url,
        )

    if request.method == "GET":
        for field in (
            "signal_number", "date_detected", "product_name", "event_term",
            "signal_source", "signal_description", "priority", "owner_name",
        ):
            getattr(form, field).data = signal.get(field)
        return render_form()

    if not form.validate_on_submit():
        return render_form()

    signal_number = normalise_signal_number(form.signal_number.data)
    clash = query_one(
        """
        SELECT signal_id
        FROM pv.safety_signals
        WHERE signal_number = %s
          AND signal_id <> %s
        """,
        (signal_number, signal_id),
    )
    if clash:
        form.signal_number.errors.append("This Signal ID already exists.")
        return render_form()

    updated = {
        "signal_number": signal_number,
        "date_detected": form.date_detected.data,
        "product_name": form.product_name.data.strip(),
        "event_term": form.event_term.data.strip(),
        "signal_source": form.signal_source.data,
        "signal_description": form.signal_description.data.strip(),
        "priority": form.priority.data,
        "owner_name": (form.owner_name.data or "").strip() or None,
    }
    changes = describe_changes(signal, updated, SIGNAL_FIELD_LABELS)
    if not changes:
        flash("No changes were made.", "info")
        return redirect(detail_url)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_signals
            SET signal_number = %s,
                date_detected = %s,
                product_name = %s,
                event_term = %s,
                signal_source = %s,
                signal_description = %s,
                priority = %s,
                owner_name = %s
            WHERE signal_id = %s
            """,
            (*updated.values(), signal_id),
        )

    write_audit_log(
        record_type="signal",
        record_id=signal_id,
        action="Signal details edited",
        details=changes,
        actor_user_id=session["user_id"],
    )
    flash(f"Safety signal {signal_number} updated.", "success")
    return redirect(detail_url)


@bp.get("/<int:signal_id>")
@login_required
def signal_detail(signal_id):
    signal, supporting_cases = _get_signal_and_supporting_cases(signal_id)
    return _render_signal_detail(signal, supporting_cases)


def _get_signal_and_supporting_cases(signal_id):
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

    supporting_cases = query_all(
        """
        SELECT
            safety_cases.case_id,
            safety_cases.case_number,
            safety_cases.received_date,
            safety_cases.seriousness,
            safety_cases.event_description,
            safety_cases.event_outcome,
            countries.country_name,
            assessments.listedness_status
        FROM pv.safety_signal_cases AS safety_signal_cases
        INNER JOIN pv.safety_cases AS safety_cases
            ON safety_cases.case_id = safety_signal_cases.case_id
        LEFT JOIN pv.countries AS countries
            ON countries.country_id = safety_cases.country_id
        LEFT JOIN pv.case_safety_assessments AS assessments
            ON assessments.case_id = safety_cases.case_id
        WHERE safety_signal_cases.signal_id = %s
        ORDER BY
            safety_cases.received_date DESC,
            safety_cases.case_id DESC
        """,
        (signal_id,),
    )
    return signal, supporting_cases


def _render_signal_detail(
    signal,
    supporting_cases,
    ai_assistance=None,
    evaluation_values=None,
):
    evaluation_form = SignalEvaluationForm()
    evaluation_form.status.data = signal["status"]
    evaluation_form.assessment_summary.data = signal["assessment_summary"]
    evaluation_form.decision_summary.data = signal["decision_summary"]
    evaluation_form.owner_name.data = signal["owner_name"]
    if evaluation_values:
        evaluation_form.status.data = evaluation_values["status"]
        evaluation_form.assessment_summary.data = evaluation_values["assessment_summary"]
        evaluation_form.decision_summary.data = evaluation_values["decision_summary"]
        evaluation_form.owner_name.data = evaluation_values["owner_name"]
    history = query_all(
        """
        SELECT
            audit_log.action,
            audit_log.details,
            audit_log.occurred_at,
            users.full_name
        FROM pv.audit_log AS audit_log
        LEFT JOIN pv.users AS users
            ON users.user_id = audit_log.actor_user_id
        WHERE audit_log.record_type = 'signal'
          AND audit_log.record_id = %s
        ORDER BY audit_log.occurred_at DESC
        LIMIT 20
        """,
        (signal["signal_id"],),
    )
    return render_template(
        "signals/signal_detail.html",
        signal=signal,
        evaluation_form=evaluation_form,
        supporting_cases=supporting_cases,
        ai_assistance=ai_assistance,
        attachments=list_record_attachments("signal", signal["signal_id"]),
        evaluation_checks=evaluation_checks(signal, supporting_cases),
        suggestion=suggest_signal_status(signal, supporting_cases),
        drafted_assessment=draft_signal_assessment_note(signal, supporting_cases),
        history=history,
    )


@bp.post("/<int:signal_id>/ai-draft")
@login_required
def draft_signal_assessment(signal_id):
    signal, supporting_cases = _get_signal_and_supporting_cases(signal_id)
    try:
        ai_assistance = draft_safety_signal_assessment(
            signal,
            supporting_cases,
        )
    except (ValueError, RuntimeError) as exc:
        current_app.logger.warning(
            "Safety signal AI drafting failed for signal %s: %s",
            signal_id,
            exc,
        )
        flash(str(exc), "error")
        return _render_signal_detail(signal, supporting_cases)
    except Exception:
        current_app.logger.exception(
            "Unexpected safety signal AI drafting error for signal %s",
            signal_id,
        )
        flash(
            "The AI draft could not be generated. Check that the AI "
            "service is available and try again.",
            "error",
        )
        return _render_signal_detail(signal, supporting_cases), 503

    return _render_signal_detail(
        signal,
        supporting_cases,
        ai_assistance=ai_assistance,
    )


@bp.post("/<int:signal_id>/evaluate")
@login_required
def evaluate_signal(signal_id):
    signal = query_one(
        """
        SELECT signal_id, status, assessment_summary, decision_summary, owner_name
        FROM pv.safety_signals
        WHERE signal_id = %s
        """,
        (signal_id,),
    )

    if not signal:
        abort(404)

    panel_url = url_for("signals.signal_detail", signal_id=signal_id) + "#evaluation-panel"
    form = SignalEvaluationForm()

    if not form.validate_on_submit():
        flash("Please correct the signal evaluation form.", "error")
        return redirect(panel_url)

    after = {
        "status": form.status.data,
        "assessment_summary": (form.assessment_summary.data or "").strip() or None,
        "decision_summary": (form.decision_summary.data or "").strip() or None,
        "owner_name": (form.owner_name.data or "").strip() or None,
    }
    if after["status"] != signal["status"]:
        errors = validate_signal_evaluation(after["status"], after)
    else:
        errors = []
    if errors:
        for error in errors:
            flash(error, "error")
        full_signal, supporting_cases = _get_signal_and_supporting_cases(signal_id)
        return _render_signal_detail(
            full_signal, supporting_cases, evaluation_values=after
        ), 400

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
                after["status"],
                after["assessment_summary"],
                after["decision_summary"],
                after["owner_name"],
                signal_id,
            ),
        )

    changes = describe_changes(signal, after, SIGNAL_EVALUATION_LABELS)
    if changes:
        write_audit_log(
            "signal", signal_id, "Evaluation updated", session["user_id"], changes
        )
    return redirect(
        url_for("signals.signal_detail", signal_id=signal_id, saved="evaluation")
        + "#evaluation-panel"
    )


@bp.post("/<int:signal_id>/status")
@login_required
def accept_signal_status(signal_id):
    """Move the signal to a status offered by the evaluation panel."""
    signal = query_one(
        """
        SELECT signal_id, status, assessment_summary, decision_summary, owner_name
        FROM pv.safety_signals
        WHERE signal_id = %s
        """,
        (signal_id,),
    )
    if not signal:
        abort(404)

    panel_url = url_for("signals.signal_detail", signal_id=signal_id) + "#evaluation-panel"
    new_status = request.form.get("status", "")
    if new_status not in ("Under evaluation", "Validated", "Closed"):
        flash("Choose a valid status.", "error")
        return redirect(panel_url)
    if new_status == signal["status"]:
        return redirect(panel_url)

    errors = validate_signal_evaluation(new_status, signal)
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(panel_url)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_signals
            SET status = %s, updated_at = NOW()
            WHERE signal_id = %s AND status = %s
            """,
            (new_status, signal_id, signal["status"]),
        )

    write_audit_log(
        "signal",
        signal_id,
        "Status changed",
        session["user_id"],
        f"Status: {signal['status']} → {new_status}",
    )
    return redirect(
        url_for("signals.signal_detail", signal_id=signal_id, saved="status")
        + "#evaluation-panel"
    )


SIGNAL_EVALUATION_LABELS = {
    "status": "Status",
    "owner_name": "Owner",
    "assessment_summary": "Assessment summary",
    "decision_summary": "Decision and action",
}


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

    report_file = None
    if not editable_content:
        # An edited draft is printed as edited; otherwise use the template.
        from app.services.report_fields import signal_summary_context
        from app.services.report_templates import render_with_active

        report_file, notice = render_with_active(
            "signal_summary", lambda: signal_summary_context(signals, report_filters)
        )
        if notice:
            flash(notice, "warning")
    if report_file is None:
        report_file = build_safety_signal_reporting_docx(
            signals=signals,
            report_filters=report_filters,
            editable_content=editable_content,
        )

    return send_file(
        report_file,
        as_attachment=True,
        download_name=f"{company_profile_service.file_prefix()}_safety_signal_reporting_summary.docx",
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