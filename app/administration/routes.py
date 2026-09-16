from flask import Blueprint, abort, flash, redirect, render_template, session, url_for
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
            r.role_name
        FROM pv.users AS u
        JOIN pv.roles AS r ON r.role_id = u.role_id
        ORDER BY u.is_active DESC, u.full_name
        """
    )

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
                    password_hash
                )
                VALUES (%s, %s, %s, %s, %s)
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

@bp.get("/audit-log")
@roles_required("System Administrator", "Auditor")
def audit_log():
    entries = query_all(
        """
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

        ORDER BY occurred_at DESC
        LIMIT 250
        """
    )

    return render_template(
        "administration/audit_log.html",
        entries=entries,
    )