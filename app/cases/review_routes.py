from datetime import datetime

from flask import Blueprint, abort, flash, redirect, request, session, url_for

from app.db import query_one, transaction
from app.security import login_required


bp = Blueprint("case_review", __name__, url_prefix="/cases")


VALID_STATUSES = {
    "New",
    "Triage",
    "Follow-up requested",
    "Medical review",
    "Ready for submission",
    "Submitted",
    "Closed",
}


@bp.post("/<int:case_id>/review")
@login_required
def review_case(case_id):
    case = query_one(
        """
        SELECT case_id, case_number
        FROM pv.safety_cases
        WHERE case_id = %s
        """,
        (case_id,),
    )

    if case is None:
        abort(404)

    workflow_status = request.form.get("workflow_status", "")
    causality_assessment = request.form.get(
        "causality_assessment",
        "",
    ).strip()
    review_notes = request.form.get("review_notes", "").strip()
    follow_up_required = request.form.get("follow_up_required") == "on"
    follow_up_due_date = request.form.get("follow_up_due_date", "").strip()

    if workflow_status not in VALID_STATUSES:
        abort(400)

    try:
        parsed_follow_up_date = (
            datetime.strptime(follow_up_due_date, "%Y-%m-%d").date()
            if follow_up_due_date
            else None
        )
    except ValueError:
        flash("Follow-up due date is not valid.", "error")
        return redirect(url_for("cases.case_detail", case_id=case_id))

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.safety_cases
            SET workflow_status = %s,
                causality_assessment = %s,
                follow_up_required = %s,
                follow_up_due_date = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE case_id = %s
            """,
            (
                workflow_status,
                causality_assessment or None,
                follow_up_required,
                parsed_follow_up_date,
                case_id,
            ),
        )

        details = (
            f"Status changed to {workflow_status}."
            + (f" Review note: {review_notes}" if review_notes else "")
        )

        cursor.execute(
            """
            INSERT INTO pv.case_audit_log (
                case_id,
                action,
                details,
                performed_by
            )
            VALUES (%s, %s, %s, %s)
            """,
            (
                case_id,
                "Case reviewed",
                details,
                session["user_id"],
            ),
        )

    flash(f"Case {case['case_number']} was updated.", "success")
    return redirect(url_for("cases.case_detail", case_id=case_id))