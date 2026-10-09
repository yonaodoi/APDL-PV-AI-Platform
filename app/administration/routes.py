from datetime import datetime, timezone

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import generate_password_hash

from app.administration.forms import UserCreateForm
from app.audit import write_audit_log
from app.db import query_all, query_one, transaction
from app.security import roles_required
from app.services.approval_settings import valid_email
from app.services import company_profile as company_profile_service


bp = Blueprint("administration", __name__, url_prefix="/administration")


@bp.get("/")
@roles_required("System Administrator")
def user_list():
    users = query_all(
        """
        SELECT
            u.user_id,
            u.username,
            u.full_name,
            u.email,
            u.is_active,
            u.last_login_at,
            u.created_at,
            u.is_designated_qppv,
            u.locked_until,
            COALESCE(
                (to_jsonb(u) ->> 'must_change_password')::boolean,
                FALSE
            ) AS must_change_password,
            r.role_name
        FROM pv.users AS u
        JOIN pv.roles AS r ON r.role_id = u.role_id
        ORDER BY u.is_active DESC, u.full_name
        """
    )

    now = datetime.now(timezone.utc)
    users = [
        {
            **user,
            "is_locked": bool(
                user.get("locked_until") and user["locked_until"] > now
            ),
        }
        for user in users
    ]

    return render_template("administration/user_list.html", users=users)


@bp.route("/users/new", methods=["GET", "POST"])
@roles_required("System Administrator")
def create_user():
    form = UserCreateForm()

    roles = query_all(
        """
        SELECT role_id, role_name
        FROM pv.roles
        ORDER BY role_name
        """
    )
    form.role_id.choices = [
        (role["role_id"], role["role_name"])
        for role in roles
    ]

    if form.validate_on_submit():
        username = form.username.data.strip()
        email = form.email.data.strip().lower()

        existing_user = query_one(
            """
            SELECT user_id
            FROM pv.users
            WHERE LOWER(username) = LOWER(%s)
               OR LOWER(email) = LOWER(%s)
            """,
            (username, email),
        )

        if existing_user:
            form.username.errors.append(
                "That username or email address already exists."
            )
            return render_template(
                "administration/create_user.html",
                form=form,
            )

        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.users (
                    role_id,
                    username,
                    full_name,
                    email,
                    password_hash,
                    must_change_password
                )
                VALUES (%s, %s, %s, %s, %s, TRUE)
                RETURNING user_id
                """,
                (
                    form.role_id.data,
                    username,
                    form.full_name.data.strip(),
                    email,
                    generate_password_hash(form.password.data),
                ),
            )
            user_id = cursor.fetchone()["user_id"]

        write_audit_log(
            record_type="user",
            record_id=user_id,
            action="User account created",
            details=f"Created account for {username}.",
            actor_user_id=session["user_id"],
        )

        flash("User account created successfully.", "success")
        return redirect(url_for("administration.user_list"))

    return render_template(
        "administration/create_user.html",
        form=form,
    )


MIN_PASSWORD_LENGTH = 10


def _load_user_or_404(user_id):
    user = query_one(
        """
        SELECT
            u.user_id,
            u.username,
            u.full_name,
            u.email,
            u.role_id,
            u.is_active,
            u.is_designated_qppv,
            u.failed_login_attempts,
            u.locked_until,
            u.last_login_at,
            COALESCE(
                (to_jsonb(u) ->> 'must_change_password')::boolean,
                FALSE
            ) AS must_change_password,
            r.role_name
        FROM pv.users AS u
        JOIN pv.roles AS r ON r.role_id = u.role_id
        WHERE u.user_id = %s
        """,
        (user_id,),
    )
    if not user:
        abort(404)
    user["is_locked"] = bool(
        user.get("locked_until")
        and user["locked_until"] > datetime.now(timezone.utc)
    )
    return user


def validate_temporary_password(password, confirmation):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return (
            f"The temporary password must contain at least "
            f"{MIN_PASSWORD_LENGTH} characters."
        )
    if password != confirmation:
        return "The two passwords do not match."
    return None


@bp.get("/users/<int:user_id>")
@roles_required("System Administrator")
def manage_user(user_id):
    user = _load_user_or_404(user_id)
    roles = query_all(
        """
        SELECT role_id, role_name
        FROM pv.roles
        ORDER BY role_name
        """
    )
    return render_template(
        "administration/manage_user.html",
        user=user,
        roles=roles,
        is_self=user_id == session["user_id"],
    )


@bp.post("/users/<int:user_id>/role")
@roles_required("System Administrator")
def change_user_role(user_id):
    user = _load_user_or_404(user_id)
    back = url_for("administration.manage_user", user_id=user_id)

    if user_id == session["user_id"]:
        flash(
            "You cannot change your own role. Ask another administrator.",
            "error",
        )
        return redirect(back)

    try:
        role_id = int(request.form.get("role_id", ""))
    except ValueError:
        flash("Choose a role.", "error")
        return redirect(back)

    role = query_one(
        "SELECT role_id, role_name FROM pv.roles WHERE role_id = %s",
        (role_id,),
    )
    if not role:
        flash("Choose a role.", "error")
        return redirect(back)

    if role_id == user["role_id"]:
        flash("The role was not changed.", "info")
        return redirect(back)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.users
            SET role_id = %s,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (role_id, user_id),
        )

    write_audit_log(
        record_type="user",
        record_id=user_id,
        action="Role changed",
        details=(
            f"Account: {user['username']}. Role changed from "
            f"{user['role_name']} to {role['role_name']}. The new role "
            "applies from the user's next sign-in."
        ),
        actor_user_id=session["user_id"],
    )
    flash(
        f"{user['full_name']} is now {role['role_name']}. The change "
        "applies from their next sign-in.",
        "success",
    )
    return redirect(back)


@bp.post("/users/<int:user_id>/reset-password")
@roles_required("System Administrator")
def reset_user_password(user_id):
    user = _load_user_or_404(user_id)
    back = url_for("administration.manage_user", user_id=user_id)

    if user_id == session["user_id"]:
        flash(
            "Use Change password from your account menu to change your "
            "own password.",
            "error",
        )
        return redirect(back)

    error = validate_temporary_password(
        request.form.get("new_password", ""),
        request.form.get("confirm_password", ""),
    )
    if error:
        flash(error, "error")
        return redirect(back)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.users
            SET password_hash = %s,
                must_change_password = TRUE,
                failed_login_attempts = 0,
                locked_until = NULL,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (
                generate_password_hash(request.form["new_password"]),
                user_id,
            ),
        )

    write_audit_log(
        record_type="user",
        record_id=user_id,
        action="Password reset by administrator",
        details=(
            f"Account: {user['username']}. Temporary password set; the "
            "user must choose a new password at next sign-in. Any lock "
            "was cleared."
        ),
        actor_user_id=session["user_id"],
    )
    flash(
        f"Temporary password set for {user['full_name']}. Share it "
        "securely; they will be asked to choose their own at next sign-in.",
        "success",
    )
    return redirect(back)


@bp.post("/users/<int:user_id>/unlock")
@roles_required("System Administrator")
def unlock_user(user_id):
    user = _load_user_or_404(user_id)

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.users
            SET failed_login_attempts = 0,
                locked_until = NULL,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (user_id,),
        )

    write_audit_log(
        record_type="user",
        record_id=user_id,
        action="Account unlocked",
        details=f"Account: {user['username']}. Failed attempts cleared.",
        actor_user_id=session["user_id"],
    )
    flash(f"{user['full_name']}'s account is unlocked.", "success")
    return redirect(
        request.form.get("return_to") == "list"
        and url_for("administration.user_list")
        or url_for("administration.manage_user", user_id=user_id)
    )


@bp.post("/users/<int:user_id>/toggle-status")
@roles_required("System Administrator")
def toggle_user_status(user_id):
    user = query_one(
        """
        SELECT user_id, username, is_active
        FROM pv.users
        WHERE user_id = %s
        """,
        (user_id,),
    )

    if not user:
        abort(404)

    if user_id == session["user_id"]:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("administration.user_list"))

    new_status = not user["is_active"]

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.users
            SET
                is_active = %s,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (new_status, user_id),
        )

    action = "User account activated" if new_status else "User account deactivated"

    write_audit_log(
        record_type="user",
        record_id=user_id,
        action=action,
        details=f"Account: {user['username']}.",
        actor_user_id=session["user_id"],
    )

    flash(action + ".", "success")
    return redirect(url_for("administration.user_list"))

@bp.post("/users/<int:user_id>/toggle-qppv")
@roles_required("System Administrator")
def toggle_designated_qppv(user_id):
    user = query_one(
        """
        SELECT user_id, username, full_name, is_designated_qppv
        FROM pv.users
        WHERE user_id = %s
        """,
        (user_id,),
    )

    if not user:
        abort(404)

    designated = not user["is_designated_qppv"]

    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.users
            SET is_designated_qppv = %s,
                updated_at = NOW()
            WHERE user_id = %s
            """,
            (designated, user_id),
        )

    action = (
        "Designated as QPPV" if designated else "QPPV designation removed"
    )
    write_audit_log(
        record_type="user",
        record_id=user_id,
        action=action,
        details=f"Account: {user['username']} ({user['full_name']}).",
        actor_user_id=session["user_id"],
    )

    flash(f"{user['full_name']}: {action.lower()}.", "success")
    return redirect(url_for("administration.user_list"))


AUDIT_RECORD_LABELS = {
    "case": "Safety case",
    "safety_case": "Safety case",
    "case_intake": "Case intake",
    "complaint": "Product complaint",
    "signal": "Safety signal",
    "psur": "PSUR",
    "user": "User",
    "reference_safety_information": "Reference safety information",
}

AUDIT_RECORD_ENDPOINTS = {
    "case": ("cases.case_detail", "case_id"),
    "safety_case": ("cases.case_detail", "case_id"),
    "complaint": ("complaints.complaint_detail", "complaint_id"),
    "signal": ("signals.signal_detail", "signal_id"),
    "psur": ("psur.psur_detail", "psur_id"),
}


def audit_record_label(record_type):
    return AUDIT_RECORD_LABELS.get(
        record_type,
        (record_type or "Record").replace("_", " ").capitalize(),
    )


def audit_record_url(record_type, record_id):
    endpoint = AUDIT_RECORD_ENDPOINTS.get(record_type)
    if not endpoint or not record_id:
        return None
    try:
        return url_for(endpoint[0], **{endpoint[1]: record_id})
    except Exception:
        return None


@bp.get("/audit-log")
@roles_required("System Administrator", "Auditor", "Group Head RA & Quality")
def audit_log():
    filters = {
        "q": request.args.get("q", "").strip(),
        "record_type": request.args.get("record_type", "").strip(),
        "user": request.args.get("user", "").strip(),
        "action": request.args.get("action", "").strip(),
        "start": request.args.get("start", "").strip(),
        "end": request.args.get("end", "").strip(),
    }
    try:
        limit = min(max(int(request.args.get("limit", 250)), 50), 5000)
    except ValueError:
        limit = 250
    where, parameters = _audit_filters(filters)
    entries = query_all(
        f"""
        SELECT * FROM (
{AUDIT_ENTRIES_SQL}
        ) AS filtered
        {where}
        ORDER BY filtered.occurred_at DESC
        LIMIT %s
        """,
        (*parameters, limit),
    )

    entries = [
        {
            **entry,
            "record_label": audit_record_label(entry["record_type"]),
            "record_url": audit_record_url(
                entry["record_type"], entry["record_id"]
            ),
        }
        for entry in entries
    ]

    if request.args.get("export") == "csv":
        return _audit_csv(entries)

    options = _audit_filter_options()
    return render_template(
        "administration/audit_log.html",
        entries=entries,
        filters=filters,
        any_filter=any(filters.values()),
        limit=limit,
        record_types=options["record_types"],
        users=options["users"],
        actions=options["actions"],
    )


AUDIT_ENTRIES_SQL = """
        SELECT
            entries.*,
            CASE
                WHEN entries.record_type IN ('case', 'safety_case') THEN (
                    SELECT case_number FROM pv.safety_cases
                    WHERE case_id = entries.record_id
                )
                WHEN entries.record_type = 'complaint' THEN (
                    SELECT complaint_number FROM pv.product_complaints
                    WHERE complaint_id = entries.record_id
                )
                WHEN entries.record_type = 'signal' THEN (
                    SELECT signal_number FROM pv.safety_signals
                    WHERE signal_id = entries.record_id
                )
                WHEN entries.record_type = 'psur' THEN (
                    SELECT report_number FROM pv.psur_reports
                    WHERE psur_id = entries.record_id
                )
                WHEN entries.record_type = 'user' THEN (
                    SELECT full_name FROM pv.users
                    WHERE user_id = entries.record_id
                )
                WHEN entries.record_type = 'reference_safety_information' THEN (
                    SELECT product_name || ' ' || document_type
                    FROM pv.reference_safety_information
                    WHERE rsi_id = entries.record_id
                )
            END AS record_reference
        FROM (
            SELECT
                audit_log.record_type,
                audit_log.record_id,
                audit_log.action,
                audit_log.details,
                audit_log.occurred_at,
                users.full_name
            FROM pv.audit_log AS audit_log
            LEFT JOIN pv.users AS users
                ON users.user_id = audit_log.actor_user_id

            UNION ALL

            SELECT
                'case' AS record_type,
                case_audit_log.case_id AS record_id,
                case_audit_log.action,
                case_audit_log.details,
                case_audit_log.performed_at AS occurred_at,
                users.full_name
            FROM pv.case_audit_log AS case_audit_log
            LEFT JOIN pv.users AS users
                ON users.user_id = case_audit_log.performed_by
        ) AS entries
"""


def _audit_filters(filters):
    clauses, parameters = [], []
    if filters["record_type"]:
        types = ["case", "safety_case"] if filters["record_type"] == "case" else [filters["record_type"]]
        clauses.append("filtered.record_type = ANY(%s)")
        parameters.append(types)
    if filters["user"]:
        if filters["user"] == "__system__":
            clauses.append("filtered.full_name IS NULL")
        else:
            clauses.append("filtered.full_name = %s")
            parameters.append(filters["user"])
    if filters["action"]:
        clauses.append("filtered.action = %s")
        parameters.append(filters["action"])
    for key, op in (("start", ">="), ("end", "<")):
        if filters[key]:
            try:
                day = datetime.strptime(filters[key], "%Y-%m-%d").date()
            except ValueError:
                filters[key] = ""
                continue
            if key == "end":
                from datetime import timedelta

                day = day + timedelta(days=1)
            clauses.append(f"filtered.occurred_at {op} %s")
            parameters.append(day)
    if filters["q"]:
        like = "%" + filters["q"].replace("%", r"\%").replace("_", r"\_") + "%"
        clauses.append(
            "(filtered.details ILIKE %s OR filtered.action ILIKE %s "
            "OR COALESCE(filtered.record_reference, '') ILIKE %s "
            "OR COALESCE(filtered.full_name, '') ILIKE %s)"
        )
        parameters.extend([like, like, like, like])
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, parameters


def _audit_filter_options():
    try:
        rows = query_all(
            f"""
            SELECT DISTINCT
                CASE WHEN record_type = 'safety_case' THEN 'case' ELSE record_type END AS record_type,
                full_name, action
            FROM (
{AUDIT_ENTRIES_SQL}
            ) AS everything
            """
        )
    except Exception:
        return {"record_types": [], "users": [], "actions": []}
    record_types = sorted({r["record_type"] for r in rows if r["record_type"]},
                          key=lambda t: audit_record_label(t).lower())
    return {
        "record_types": [(t, audit_record_label(t)) for t in record_types],
        "users": sorted({r["full_name"] for r in rows if r["full_name"]}, key=str.lower),
        "actions": sorted({r["action"] for r in rows if r["action"]}, key=str.lower),
    }


def _audit_csv(entries):
    import csv
    from io import StringIO

    from flask import Response

    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["Date and time", "Record type", "Record", "Action", "Performed by", "Details"])
    for e in entries:
        writer.writerow([
            e["occurred_at"].strftime("%Y-%m-%d %H:%M:%S") if e.get("occurred_at") else "",
            e["record_label"], e.get("record_reference") or (f"#{e['record_id']}" if e.get("record_id") else ""),
            e.get("action") or "", e.get("full_name") or "System user", e.get("details") or "",
        ])
    return Response(
        "\ufeff" + out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={company_profile_service.file_prefix()}_audit_trail.csv"},
    )


@bp.post("/users/<int:user_id>/contact")
@roles_required("System Administrator")
def update_user_contact(user_id):
    """Change a user's display name and email (used for notifications)."""
    user = _load_user_or_404(user_id)
    back = url_for("administration.manage_user", user_id=user_id)
    full_name = " ".join((request.form.get("full_name") or "").split())
    email = (request.form.get("email") or "").strip().lower()
    if not full_name or len(full_name) > 200:
        flash("Enter the user's full name.", "error")
        return redirect(back)
    if not valid_email(email) or len(email) > 255:
        flash("Enter a valid email address.", "error")
        return redirect(back)
    clash = query_one(
        "SELECT user_id FROM pv.users WHERE LOWER(email) = %s AND user_id <> %s",
        (email, user_id),
    )
    if clash:
        flash("Another account already uses that email address.", "error")
        return redirect(back)
    if full_name == user["full_name"] and email == (user["email"] or "").lower():
        flash("Nothing was changed.", "info")
        return redirect(back)

    with transaction() as cursor:
        cursor.execute(
            "UPDATE pv.users SET full_name = %s, email = %s, updated_at = NOW() WHERE user_id = %s",
            (full_name, email, user_id),
        )
    write_audit_log(
        record_type="user",
        record_id=user_id,
        action="Contact details changed",
        details=(
            f"Account: {user['username']}. Name: {user['full_name']} -> {full_name}. "
            f"Email: {user['email']} -> {email}."
        ),
        actor_user_id=session["user_id"],
    )
    flash("Contact details saved.", "success")
    return redirect(back)


SIGN_OFF_FIELDS = {
    "review_title": "Review title",
    "approval_title": "Approval title",
    "reviewer_roles": "Reviewer roles",
    "approver_roles": "Approver roles",
    "officer_roles": "PV officer roles",
    "review_extra_emails": "Extra review emails",
    "approval_extra_emails": "Extra approval emails",
    "officer_extra_emails": "Extra PV officer emails",
    "notify_officer": "Email the PV officer",
    "digest_hour": "Daily summary hour",
    "urgent_days": "Urgent email days",
}


def _sign_off_people():
    return query_all(
        """
        SELECT u.user_id, u.full_name, u.email, r.role_name, u.is_designated_qppv
        FROM pv.users AS u JOIN pv.roles AS r ON r.role_id = u.role_id
        WHERE u.is_active
        ORDER BY r.role_name, u.full_name
        """
    )


def _email_changes(people):
    """Emails typed next to people on the settings page.

    Returns (changes, error). Each change is (person, new_email)."""
    changes, seen = [], {}
    for person in people:
        field = f"email_{person['user_id']}"
        if field not in request.form:
            continue
        current = (person.get("email") or "").strip().lower()
        # A person can be listed twice (e.g. reviewer and PV officer); use
        # whichever box was changed.
        typed = [(v or "").strip().strip(",;").strip().lower() for v in request.form.getlist(field)]
        email = next((v for v in typed if v != current), current)
        if email == current:
            continue
        if not email:
            return [], f"Enter an email address for {person['full_name']} (it cannot be left empty)."
        if not valid_email(email) or len(email) > 255:
            return [], f"The email for {person['full_name']} ({email}) is not a valid address."
        if email in seen:
            return [], f"{seen[email]} and {person['full_name']} cannot share the same email."
        seen[email] = person["full_name"]
        clash = query_one(
            "SELECT full_name FROM pv.users WHERE LOWER(email) = %s AND user_id <> %s",
            (email, person["user_id"]),
        )
        if clash:
            return [], f"{clash['full_name']} already uses {email}."
        changes.append((person, email))
    return changes, None


@bp.route("/sign-off-settings", methods=["GET", "POST"])
@roles_required("System Administrator")
def sign_off_settings():
    """Who prepares, reviews and approves safety cases, stage titles and emails."""
    from app.services.approval_settings import load_settings, save_settings

    roles = query_all("SELECT role_name FROM pv.roles ORDER BY role_name")
    role_names = [r["role_name"] for r in roles]

    if request.method == "POST":
        here = url_for("administration.sign_off_settings")
        before = load_settings()
        values = {
            "review_title": request.form.get("review_title", ""),
            "approval_title": request.form.get("approval_title", ""),
            "reviewer_roles": [r for r in request.form.getlist("reviewer_roles") if r in role_names],
            "approver_roles": [r for r in request.form.getlist("approver_roles") if r in role_names],
            "officer_roles": [r for r in request.form.getlist("officer_roles") if r in role_names],
            "review_extra_emails": request.form.get("review_extra_emails", ""),
            "approval_extra_emails": request.form.get("approval_extra_emails", ""),
            "officer_extra_emails": request.form.get("officer_extra_emails", ""),
            "notify_officer": request.form.get("notify_officer") == "on",
            "digest_hour": request.form.get("digest_hour", ""),
            "urgent_days": request.form.get("urgent_days", ""),
        }
        if not values["reviewer_roles"] or not values["approver_roles"]:
            flash("Nothing was saved. Choose at least one role for review and one for approval.", "error")
            return redirect(here)
        if set(values["reviewer_roles"]) & set(values["approver_roles"]):
            flash(
                "Nothing was saved. A role cannot both review and approve; the two "
                "sign-offs must come from different people.",
                "error",
            )
            return redirect(here)

        email_changes, error = _email_changes(_sign_off_people())
        if error:
            flash(f"Nothing was saved. {error}", "error")
            return redirect(here)

        after = save_settings(values, session["user_id"])
        changes = [
            f"{SIGN_OFF_FIELDS.get(key, key)}: {before.get(key)} -> {after[key]}"
            for key in after
            if before.get(key) != after[key]
        ]
        if email_changes:
            with transaction() as cursor:
                for person, email in email_changes:
                    cursor.execute(
                        "UPDATE pv.users SET email = %s, updated_at = NOW() WHERE user_id = %s",
                        (email, person["user_id"]),
                    )
            for person, email in email_changes:
                write_audit_log(
                    record_type="user",
                    record_id=person["user_id"],
                    action="Contact details changed",
                    details=f"Email for {person['full_name']}: {person.get('email') or 'none'} -> {email} (from sign-off settings).",
                    actor_user_id=session["user_id"],
                )
                changes.append(f"Email for {person['full_name']} -> {email}")
        write_audit_log(
            record_type="settings",
            record_id=0,
            action="Sign-off settings changed",
            details="; ".join(changes) or "Saved with no changes.",
            actor_user_id=session["user_id"],
        )
        if changes:
            count = len(changes)
            flash(f"Sign-off settings saved ({count} change{'s' if count != 1 else ''}).", "success")
        else:
            flash("Sign-off settings saved. Nothing had changed.", "success")
        return redirect(url_for("administration.user_list"))

    settings = load_settings()
    people = _sign_off_people()
    return render_template(
        "administration/sign_off_settings.html",
        settings=settings,
        role_names=role_names,
        reviewers=[p for p in people if p["role_name"] in settings["reviewer_roles"] or p["is_designated_qppv"]],
        approvers=[p for p in people if p["role_name"] in settings["approver_roles"]],
        officers=[p for p in people if p["role_name"] in settings["officer_roles"]],
    )


# --------------------------------------------------------------------------
# Company profile: makes the platform reusable for another company
# --------------------------------------------------------------------------

def _company_labels():
    from app.services.company_profile import FIELDS, PREFIX_FIELDS

    labels = {key: label for key, label, _ in FIELDS}
    labels.update({key: f"{label} number prefix" for key, label, _ in PREFIX_FIELDS})
    labels.update({"brand_colour": "Brand colour", "logo_file": "Logo"})
    return labels


@bp.route("/company-profile", methods=["GET", "POST"])
@roles_required("System Administrator")
def company_profile():
    """Company name, contacts, reference prefixes, logo and colour."""
    from app.services import company_profile as cp

    if request.method == "POST":
        before = cp.load_profile()
        values = {key: request.form.get(key, "") for key in cp.DEFAULTS if key != "logo_file"}
        values["logo_file"] = before.get("logo_file", "")
        problems = cp.problems(values)
        upload = request.files.get("logo")
        if not problems and upload and upload.filename:
            try:
                values["logo_file"] = cp.store_logo(upload)
            except ValueError as error:
                problems.append(str(error))
        if request.form.get("remove_logo") == "on":
            values["logo_file"] = ""
        if problems:
            for problem in problems:
                flash(f"Nothing was saved. {problem}", "error")
            return render_template(
                "administration/company_profile.html",
                profile={**before, **values},
                fields=cp.FIELDS,
                prefix_fields=cp.PREFIX_FIELDS,
                defaults=cp.DEFAULTS,
            )

        after = cp.save_profile(values, session["user_id"])
        labels = _company_labels()
        changes = [
            f"{labels.get(key, key)}: {before.get(key) or 'blank'} -> {after[key] or 'blank'}"
            for key in after
            if before.get(key) != after[key]
        ]
        write_audit_log(
            record_type="settings",
            record_id=0,
            action="Company profile changed",
            details="; ".join(changes) or "Saved with no changes.",
            actor_user_id=session["user_id"],
        )
        if changes:
            count = len(changes)
            flash(f"Company profile saved ({count} change{'s' if count != 1 else ''}).", "success")
        else:
            flash("Company profile saved. Nothing had changed.", "success")
        return redirect(url_for("administration.user_list"))

    return render_template(
        "administration/company_profile.html",
        profile=cp.load_profile(),
        fields=cp.FIELDS,
        prefix_fields=cp.PREFIX_FIELDS,
        defaults=cp.DEFAULTS,
    )


@bp.get("/company-logo")
def company_logo():
    """The uploaded logo (public: the sign-in page shows it)."""
    from flask import send_file

    from app.services.company_profile import LOGO_TYPES, logo_path

    path = logo_path()
    if not path:
        abort(404)
    response = send_file(path, mimetype=LOGO_TYPES.get(path.suffix.lower(), "application/octet-stream"))
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response
