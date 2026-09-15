from flask import Blueprint, abort, flash, redirect, render_template, session, url_for

from app.db import query_all, query_one, transaction
from app.security import login_required
from app.signals.forms import SafetySignalForm, SignalEvaluationForm

bp = Blueprint("signals", __name__, url_prefix="/signals")


@bp.get("/")
@login_required
def signal_list():
    signals = query_all(
        """
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
        ORDER BY date_detected DESC, signal_id DESC
        """
    )
    return render_template("signals/signal_list.html", signals=signals)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_signal():
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

    return render_template(
        "signals/signal_detail.html",
        signal=signal,
        evaluation_form=evaluation_form,
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