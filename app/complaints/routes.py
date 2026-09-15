from flask import Blueprint, abort, flash, redirect, render_template, session, url_for

from app.complaints.forms import ComplaintReviewForm, ProductComplaintForm
from app.db import query_all, query_one, transaction
from app.security import login_required

bp = Blueprint("complaints", __name__, url_prefix="/complaints")


@bp.get("/")
@login_required
def complaint_list():
    complaints = query_all(
        """
        SELECT
            complaint_id,
            complaint_number,
            date_received,
            product_name,
            batch_number,
            complaint_category,
            severity,
            status,
            country_name
        FROM pv.product_complaints
        LEFT JOIN pv.countries USING (country_id)
        ORDER BY date_received DESC, complaint_id DESC
        """
    )
    return render_template(
        "complaints/complaint_list.html",
        complaints=complaints,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create_complaint():
    form = ProductComplaintForm()

    countries = query_all(
        """
        SELECT country_id, country_name
        FROM pv.countries
        ORDER BY country_name
        """
    )
    form.country_id.choices = [
        (0, "Select country")
    ] + [
        (country["country_id"], country["country_name"])
        for country in countries
    ]

    if form.validate_on_submit():
        existing_complaint = query_one(
            """
            SELECT complaint_id
            FROM pv.product_complaints
            WHERE complaint_number = %s
            """,
            (form.complaint_number.data.strip(),),
        )

        if existing_complaint:
            form.complaint_number.errors.append(
                "This Complaint ID already exists."
            )
            return render_template(
                "complaints/create_complaint.html",
                form=form,
            )

        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.product_complaints (
                    complaint_number,
                    date_received,
                    country_id,
                    reporter_name,
                    reporter_contact,
                    product_name,
                    batch_number,
                    manufacturing_date,
                    expiry_date,
                    complaint_category,
                    complaint_description,
                    severity,
                    created_by
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    form.complaint_number.data.strip(),
                    form.date_received.data,
                    form.country_id.data,
                    form.reporter_name.data.strip() or None,
                    form.reporter_contact.data.strip() or None,
                    form.product_name.data.strip(),
                    form.batch_number.data.strip() or None,
                    form.manufacturing_date.data,
                    form.expiry_date.data,
                    form.complaint_category.data,
                    form.complaint_description.data.strip(),
                    form.severity.data,
                    session["user_id"],
                ),
            )

        flash("Product complaint saved successfully.", "success")
        return redirect(url_for("complaints.complaint_list"))

    return render_template(
        "complaints/create_complaint.html",
        form=form,
    )


@bp.get("/<int:complaint_id>")
@login_required
def complaint_detail(complaint_id):
    complaint = query_one(
        """
        SELECT
            pc.*,
            c.country_name,
            u.full_name AS created_by_name
        FROM pv.product_complaints pc
        LEFT JOIN pv.countries c ON c.country_id = pc.country_id
        LEFT JOIN pv.users u ON u.user_id = pc.created_by
        WHERE pc.complaint_id = %s
        """,
        (complaint_id,),
    )

    if not complaint:
        abort(404)

    review_form = ComplaintReviewForm()
    review_form.status.data = complaint["status"]
    review_form.investigation_summary.data = complaint["investigation_summary"]
    review_form.corrective_action.data = complaint["corrective_action"]
    review_form.closure_date.data = complaint["closure_date"]

    return render_template(
        "complaints/complaint_detail.html",
        complaint=complaint,
        review_form=review_form,
    )


@bp.post("/<int:complaint_id>/review")
@login_required
def review_complaint(complaint_id):
    complaint = query_one(
        """
        SELECT complaint_id
        FROM pv.product_complaints
        WHERE complaint_id = %s
        """,
        (complaint_id,),
    )

    if not complaint:
        abort(404)

    form = ComplaintReviewForm()

    if not form.validate_on_submit():
        flash("Please correct the investigation form and try again.", "error")
        return redirect(
            url_for("complaints.complaint_detail", complaint_id=complaint_id)
        )

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.product_complaints
            SET
                status = %s,
                investigation_summary = %s,
                corrective_action = %s,
                closure_date = %s,
                updated_at = NOW()
            WHERE complaint_id = %s
            """,
            (
                form.status.data,
                form.investigation_summary.data.strip() or None,
                form.corrective_action.data.strip() or None,
                form.closure_date.data,
                complaint_id,
            ),
        )

    flash("Complaint investigation update saved.", "success")
    return redirect(
        url_for("complaints.complaint_detail", complaint_id=complaint_id)
    )