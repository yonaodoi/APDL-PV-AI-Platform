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
@roles_required("System Administrator", "Auditor")
def audit_log():
    entries = query_all(
        """
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
        ORDER BY entries.occurred_at DESC
        LIMIT 250
        """
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

    return render_template(
        "administration/audit_log.html",
        entries=entries,
    )
