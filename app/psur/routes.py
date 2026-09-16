from flask import Blueprint, abort, flash, redirect, render_template, session, url_for

from app.db import query_all, query_one, transaction
from app.psur.forms import PsurReportForm, PsurReviewForm
from app.security import login_required

bp = Blueprint("psur", __name__, url_prefix="/psur")


@bp.get("/")
@login_required
def psur_list():
    reports = query_all(
        """
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
        ORDER BY reporting_period_end DESC, psur_id DESC
        """
    )
    return render_template("psur/psur_list.html", reports=reports)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_psur():
    form = PsurReportForm()

    if form.validate_on_submit():
        if form.reporting_period_end.data < form.reporting_period_start.data:
            form.reporting_period_end.errors.append(
                "The end date must be on or after the start date."
            )
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
                    therapeutic_indication,
                    mechanism_of_action,
                    countries_covered,
                    prepared_by,
                    approved_by,
                    report_notes,
                    created_by
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
                """,
                (
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
                    form.therapeutic_indication.data.strip() or None,
                    form.mechanism_of_action.data.strip() or None,
                    form.countries_covered.data.strip() or None,
                    form.prepared_by.data.strip() or None,
                    form.approved_by.data.strip() or None,
                    form.report_notes.data.strip() or None,
                    session["user_id"],
                ),
            )

        flash("PSUR record saved successfully.", "success")
        return redirect(url_for("psur.psur_list"))

    return render_template("psur/create_psur.html", form=form)


@bp.get("/<int:psur_id>")
@login_required
def psur_detail(psur_id):
    report = query_one(
        """
        SELECT
            p.*,
            u.full_name AS created_by_name
        FROM pv.psur_reports p
        LEFT JOIN pv.users u ON u.user_id = p.created_by
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

    return render_template(
        "psur/psur_detail.html",
        report=report,
        review_form=review_form,
    )


@bp.post("/<int:psur_id>/review")
@login_required
def review_psur(psur_id):
    report = query_one(
        """
        SELECT psur_id
        FROM pv.psur_reports
        WHERE psur_id = %s
        """,
        (psur_id,),
    )

    if not report:
        abort(404)

    form = PsurReviewForm()

    if not form.validate_on_submit():
        flash("Please correct the PSUR update form.", "error")
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
                form.approved_by.data.strip() or None,
                form.report_notes.data.strip() or None,
                psur_id,
            ),
        )

    flash("PSUR update saved successfully.", "success")
    return redirect(url_for("psur.psur_detail", psur_id=psur_id))