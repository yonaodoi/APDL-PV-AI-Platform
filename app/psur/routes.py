
from datetime import datetime, timezone

import flask

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
)

from app.attachments.routes import list_record_attachments
from app.db import query_all, query_one, transaction
from app.psur.forms import PsurReportForm, PsurReviewForm
from app.audit import write_audit_log
from app.security import login_required
from app.services.psur_rules import (
    approval_updates,
    can_approve,
    is_locked,
    submission_status,
    validate_status_change,
    validate_submission_date,
)


bp = Blueprint("psur", __name__, url_prefix="/psur")


def _current_user_is_designated_qppv():
    user = query_one(
        "SELECT is_designated_qppv FROM pv.users WHERE user_id = %s",
        (session.get("user_id"),),
    )
    return bool(user and user.get("is_designated_qppv"))


def populate_psur_form(form, report):
    form.report_number.data = report["report_number"]
    form.serial_number.data = report["serial_number"]
    form.product_name.data = report["product_name"]
    form.active_substances.data = report["active_substances"]
    form.atc_codes.data = report["atc_codes"]
    form.marketing_authorisation_number.data = (
        report["marketing_authorisation_number"]
    )
    form.marketing_authorisation_date.data = (
        report["marketing_authorisation_date"]
    )
    form.marketing_authorisation_procedure.data = (
        report["marketing_authorisation_procedure"]
    )
    form.international_birth_date.data = report["international_birth_date"]
    form.eurd.data = report["eurd"]
    form.reporting_period_start.data = report["reporting_period_start"]
    form.reporting_period_end.data = report["reporting_period_end"]
    form.data_lock_point.data = report["data_lock_point"]
    form.marketing_authorisation_holder_name.data = (
        report["marketing_authorisation_holder_name"]
    )
    form.marketing_authorisation_holder_address.data = (
        report["marketing_authorisation_holder_address"]
    )
    form.qppv_name.data = report["qppv_name"]
    form.qppv_phone.data = report["qppv_phone"]
    form.qppv_email.data = report["qppv_email"]
    form.pbrer_contact_name.data = report["pbrer_contact_name"]
    form.pbrer_contact_position.data = report[
        "pbrer_contact_position"
    ]
    form.reviewer_a_name.data = report["reviewer_a_name"]
    form.reviewer_a_position.data = report[
        "reviewer_a_position"
    ]
    form.therapeutic_indication.data = report["therapeutic_indication"]
    form.mechanism_of_action.data = report["mechanism_of_action"]
    form.countries_covered.data = report["countries_covered"]
    form.prepared_by.data = report["prepared_by"]
    form.prepared_by_position.data = report[
        "prepared_by_position"
    ]
    form.approved_by.data = report["approved_by"]
    form.report_notes.data = report["report_notes"]


def psur_form_values(form):
    return (
        form.report_number.data.strip(),
        form.serial_number.data.strip() or None,
        form.product_name.data.strip(),
        form.active_substances.data.strip() or None,
        form.atc_codes.data.strip() or None,
        form.marketing_authorisation_number.data.strip() or None,
        form.marketing_authorisation_date.data,
        form.marketing_authorisation_procedure.data.strip() or None,
        form.international_birth_date.data,
        form.eurd.data,
        form.reporting_period_start.data,
        form.reporting_period_end.data,
        form.data_lock_point.data,
        form.marketing_authorisation_holder_name.data.strip() or None,
        form.marketing_authorisation_holder_address.data.strip() or None,
        form.qppv_name.data.strip() or None,
        form.qppv_phone.data.strip() or None,
        form.qppv_email.data.strip() or None,
        form.pbrer_contact_name.data.strip() or None,
        form.pbrer_contact_position.data.strip() or None,
        form.reviewer_a_name.data.strip() or None,
        form.reviewer_a_position.data.strip() or None,
        form.therapeutic_indication.data.strip() or None,
        form.mechanism_of_action.data.strip() or None,
        form.countries_covered.data.strip() or None,
        form.prepared_by.data.strip() or None,
        form.prepared_by_position.data.strip() or None,
        (form.approved_by.data or "").strip() or None,
        form.report_notes.data.strip() or None,
    )


def reporting_dates_are_valid(form):
    if form.reporting_period_end.data < form.reporting_period_start.data:
        form.reporting_period_end.errors.append(
            "The end date must be on or after the start date."
        )
        return False

    return True


@bp.get("/")
@login_required
def psur_list():
    selected_product = flask.request.args.get(
        "product",
        "",
    ).strip()
    selected_status = flask.request.args.get(
        "status",
        "",
    ).strip()
    start_date = flask.request.args.get(
        "start_date",
        "",
    ).strip()
    end_date = flask.request.args.get(
        "end_date",
        "",
    ).strip()

    filters = []
    parameters = []

    if selected_product:
        filters.append("product_name = %s")
        parameters.append(selected_product)

    if selected_status:
        filters.append("status = %s")
        parameters.append(selected_status)

    if start_date:
        filters.append("reporting_period_end >= %s")
        parameters.append(start_date)

    if end_date:
        filters.append("reporting_period_start <= %s")
        parameters.append(end_date)

    where_clause = ""
    if filters:
        where_clause = "WHERE " + " AND ".join(filters)

    reports = query_all(
        f"""
        SELECT
            psur_id,
            report_number,
            product_name,
            reporting_period_start,
            reporting_period_end,
            data_lock_point,
            status,
            prepared_by
        FROM pv.psur_reports
        {where_clause}
        ORDER BY reporting_period_end DESC, psur_id DESC
        """,
        tuple(parameters),
    )

    products = query_all(
        """
        SELECT DISTINCT product_name
        FROM pv.psur_reports
        WHERE product_name IS NOT NULL
          AND product_name <> ''
        ORDER BY product_name
        """
    )

    statuses = query_all(
        """
        SELECT DISTINCT status
        FROM pv.psur_reports
        WHERE status IS NOT NULL
          AND status <> ''
        ORDER BY status
        """
    )

    return render_template(
        "psur/psur_list.html",
        reports=reports,
        products=products,
        statuses=statuses,
        selected_product=selected_product,
        selected_status=selected_status,
        start_date=start_date,
        end_date=end_date,
    )
@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_psur():
    form = PsurReportForm()

    if form.validate_on_submit():
        # Approval is recorded through the review workflow, not typed in.
        form.approved_by.data = ""
        if not reporting_dates_are_valid(form):
            return render_template("psur/create_psur.html", form=form)

        existing_report = query_one(
            """
            SELECT psur_id
            FROM pv.psur_reports
            WHERE report_number = %s
            """,
            (form.report_number.data.strip(),),
        )

        if existing_report:
            form.report_number.errors.append("This PSUR ID already exists.")
            return render_template("psur/create_psur.html", form=form)

        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.psur_reports (
                    report_number,
                    serial_number,
                    product_name,
                    active_substances,
                    atc_codes,
                    marketing_authorisation_number,
                    marketing_authorisation_date,
                    marketing_authorisation_procedure,
                    international_birth_date,
                    eurd,
                    reporting_period_start,
                    reporting_period_end,
                    data_lock_point,
                    marketing_authorisation_holder_name,
                    marketing_authorisation_holder_address,
                    qppv_name,
                    qppv_phone,
                    qppv_email,
                    pbrer_contact_name,
                    pbrer_contact_position,
                    reviewer_a_name,
                    reviewer_a_position,
                    therapeutic_indication,
                    mechanism_of_action,
                    countries_covered,
                    prepared_by,
                    prepared_by_position,
                    approved_by,
                    report_notes,
                    created_by
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                psur_form_values(form) + (session["user_id"],),
            )
            cursor.execute(
                "SELECT psur_id FROM pv.psur_reports WHERE report_number = %s",
                (form.report_number.data.strip(),),
            )
            new_psur_id = cursor.fetchone()["psur_id"]

        write_audit_log(
            "psur",
            new_psur_id,
            "PSUR record created",
            session["user_id"],
            f"PSUR {form.report_number.data.strip()} was created.",
        )

        flash("PSUR record saved successfully.", "success")
        return redirect(url_for("psur.psur_list"))

    return render_template("psur/create_psur.html", form=form)


@bp.route("/<int:psur_id>/edit", methods=["GET", "POST"])
@login_required
def edit_psur(psur_id):
    report = query_one(
        """
        SELECT *
        FROM pv.psur_reports
        WHERE psur_id = %s
        """,
        (psur_id,),
    )

    if not report:
        abort(404)

    if is_locked(report):
        flash(
            "This PSUR is finalised and locked. A QPPV must reopen it before "
            "it can be edited.",
            "error",
        )
        return redirect(url_for("psur.psur_detail", psur_id=psur_id))

    form = PsurReportForm()

    if form.validate_on_submit():
        # Approval is recorded through the review workflow, not typed in.
        form.approved_by.data = report["approved_by"] or ""
        if not reporting_dates_are_valid(form):
            return render_template(
                "psur/create_psur.html",
                form=form,
                editing=True,
                report=report,
            )

        existing_report = query_one(
            """
            SELECT psur_id
            FROM pv.psur_reports
            WHERE report_number = %s
              AND psur_id <> %s
            """,
            (form.report_number.data.strip(), psur_id),
        )

        if existing_report:
            form.report_number.errors.append("This PSUR ID already exists.")
            return render_template(
                "psur/create_psur.html",
                form=form,
                editing=True,
                report=report,
            )

        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.psur_reports
                SET
                    report_number = %s,
                    serial_number = %s,
                    product_name = %s,
                    active_substances = %s,
                    atc_codes = %s,
                    marketing_authorisation_number = %s,
                    marketing_authorisation_date = %s,
                    marketing_authorisation_procedure = %s,
                    international_birth_date = %s,
                    eurd = %s,
                    reporting_period_start = %s,
                    reporting_period_end = %s,
                    data_lock_point = %s,
                    marketing_authorisation_holder_name = %s,
                    marketing_authorisation_holder_address = %s,
                    qppv_name = %s,
                    qppv_phone = %s,
                    qppv_email = %s,
                    pbrer_contact_name = %s,
                    pbrer_contact_position = %s,
                    reviewer_a_name = %s,
                    reviewer_a_position = %s,
                    therapeutic_indication = %s,
                    mechanism_of_action = %s,
                    countries_covered = %s,
                    prepared_by = %s,
                    prepared_by_position = %s,
                    approved_by = %s,
                    report_notes = %s,
                    updated_at = NOW()
                WHERE psur_id = %s
                """,
                psur_form_values(form) + (psur_id,),
            )

        write_audit_log(
            "psur",
            psur_id,
            "PSUR record edited",
            session["user_id"],
            "PSUR details were edited.",
        )

        flash("PSUR record updated successfully.", "success")
        return redirect(url_for("psur.psur_detail", psur_id=psur_id))

    populate_psur_form(form, report)

    return render_template(
        "psur/create_psur.html",
        form=form,
        editing=True,
        report=report,
    )


@bp.get("/<int:psur_id>")
@login_required
def psur_detail(psur_id):
    report = query_one(
        """
        SELECT
            p.*,
            u.full_name AS created_by_name,
            approver.full_name AS approved_by_user_name,
            finaliser.full_name AS finalised_by_user_name
        FROM pv.psur_reports p
        LEFT JOIN pv.users u ON u.user_id = p.created_by
        LEFT JOIN pv.users approver ON approver.user_id = p.approved_by_user_id
        LEFT JOIN pv.users finaliser ON finaliser.user_id = p.finalised_by_user_id
        WHERE p.psur_id = %s
        """,
        (psur_id,),
    )

    if not report:
        abort(404)

    review_form = PsurReviewForm()
    review_form.status.data = report["status"]
    review_form.prepared_by.data = report["prepared_by"]
    review_form.approved_by.data = report["approved_by"]
    review_form.report_notes.data = report["report_notes"]

    from app.services.psur_tabulations import build_report_tabulation

    try:
        tabulation = build_report_tabulation(report)
    except Exception:
        current_app.logger.exception(
            "Could not build PSUR tabulation for %s", report["report_number"]
        )
        tabulation = None

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
        WHERE audit_log.record_type = 'psur'
          AND audit_log.record_id = %s
        ORDER BY audit_log.occurred_at DESC
        """,
        (psur_id,),
    )

    return render_template(
        "psur/psur_detail.html",
        report=report,
        review_form=review_form,
        tabulation=tabulation,
        history=history,
        locked=is_locked(report),
        attachments=list_record_attachments("psur", psur_id),
        can_approve=can_approve(
            session.get("role"), _current_user_is_designated_qppv()
        ),
        submission=submission_status(report),
    )


@bp.post("/<int:psur_id>/submission")
@login_required
def record_psur_submission(psur_id):
    report = query_one(
        "SELECT * FROM pv.psur_reports WHERE psur_id = %s",
        (psur_id,),
    )
    if not report:
        abort(404)

    submitted_text = request.form.get("submitted_date", "").strip()
    try:
        submitted = (
            datetime.strptime(submitted_text, "%Y-%m-%d").date()
            if submitted_text
            else None
        )
    except ValueError:
        submitted = None

    errors = validate_submission_date(report, submitted)
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("psur.psur_detail", psur_id=psur_id))

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.psur_reports
            SET submitted_date = %s,
                updated_at = NOW()
            WHERE psur_id = %s
            """,
            (submitted, psur_id),
        )

    previous = report.get("submitted_date")
    write_audit_log(
        "psur",
        psur_id,
        "Submission recorded",
        session["user_id"],
        f"Submission date set to {submitted:%d %b %Y}"
        + (f" (previously {previous:%d %b %Y})." if previous else "."),
    )
    flash("PSUR submission date recorded.", "success")
    return redirect(url_for("psur.psur_detail", psur_id=psur_id))


@bp.post("/<int:psur_id>/review")
@login_required
def review_psur(psur_id):
    report = query_one(
        "SELECT * FROM pv.psur_reports WHERE psur_id = %s",
        (psur_id,),
    )

    if not report:
        abort(404)

    form = PsurReviewForm()

    if not form.validate_on_submit():
        flash("Please correct the PSUR update form.", "error")
        return redirect(url_for("psur.psur_detail", psur_id=psur_id))

    new_status = form.status.data
    has_uncoded = False
    if new_status == "Finalised":
        from app.services.psur_tabulations import build_report_tabulation

        try:
            has_uncoded = build_report_tabulation(report)["has_uncoded"]
        except Exception:
            current_app.logger.exception(
                "Could not check PSUR tabulation coding for %s",
                report["report_number"],
            )

    errors = validate_status_change(
        report,
        new_status,
        session.get("role"),
        has_uncoded,
        designated_qppv=_current_user_is_designated_qppv(),
    )
    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("psur.psur_detail", psur_id=psur_id))

    updates = approval_updates(
        report, new_status, session["user_id"], datetime.now(timezone.utc)
    )
    if updates["clear_approved_by"]:
        approved_by = None
    elif updates["approved_by_user_id"] == session["user_id"] and (
        report.get("status") not in ("Approved", "Finalised")
        or not report.get("approved_at")
    ):
        approved_by = session.get("full_name")
    else:
        approved_by = report.get("approved_by")

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.psur_reports
            SET
                status = %s,
                prepared_by = %s,
                approved_by = %s,
                approved_by_user_id = %s,
                approved_at = %s,
                finalised_by_user_id = %s,
                finalised_at = %s,
                report_notes = %s,
                updated_at = NOW()
            WHERE psur_id = %s
            """,
            (
                new_status,
                form.prepared_by.data.strip() or None,
                approved_by,
                updates["approved_by_user_id"],
                updates["approved_at"],
                updates["finalised_by_user_id"],
                updates["finalised_at"],
                form.report_notes.data.strip() or None,
                psur_id,
            ),
        )

    details = f"Status: {report.get('status')} → {new_status}."
    if (form.prepared_by.data.strip() or None) != report.get("prepared_by"):
        details += f" Prepared by set to {form.prepared_by.data.strip() or 'blank'}."
    if (form.report_notes.data.strip() or None) != report.get("report_notes"):
        details += " Preparation notes edited."
    if approved_by != report.get("approved_by"):
        details += f" Approved by: {approved_by or 'cleared'}."
    write_audit_log("psur", psur_id, "PSUR status updated", session["user_id"], details)

    flash("PSUR update saved successfully.", "success")
    return redirect(url_for("psur.psur_detail", psur_id=psur_id))

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.psur_reports
            SET
                status = %s,
                prepared_by = %s,
                approved_by = %s,
                report_notes = %s,
                updated_at = NOW()
            WHERE psur_id = %s
            """,
            (
                form.status.data,
                form.prepared_by.data.strip() or None,
                (form.approved_by.data or "").strip() or None,
                form.report_notes.data.strip() or None,
                psur_id,
            ),
        )

    flash("PSUR update saved successfully.", "success")
    return redirect(url_for("psur.psur_detail", psur_id=psur_id))