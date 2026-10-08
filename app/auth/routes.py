from datetime import datetime, timedelta, timezone

import psycopg2
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from app.audit import write_audit_log
from app.db import get_db, query_one, transaction
from app.security import login_required, safe_next_path


bp = Blueprint("auth", __name__)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 10


def record_sign_in_event(action, user_id=None, details=None, actor_user_id=None):
    """Write a sign-in event to the audit trail without blocking sign-in."""
    try:
        write_audit_log(
            record_type="user",
            record_id=user_id,
            action=action,
            details=details,
            actor_user_id=actor_user_id,
        )
    except Exception:
        current_app.logger.exception("Could not record sign-in event: %s", action)
        try:
            get_db().rollback()
        except Exception:
            pass


@bp.route("/login", methods=["GET", "POST"])
def login():
    next_path = safe_next_path(request.args.get("next"))

    if session.get("user_id"):
        return redirect(next_path or url_for("core.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        submitted_password = request.form.get("password", "")

        try:
            # must_change_password is read through to_jsonb so sign-in keeps
            # working even before migration 042 has been applied.
            user = query_one(
                """
                SELECT
                    u.user_id,
                    u.username,
                    u.full_name,
                    u.password_hash,
                    u.is_active,
                    u.failed_login_attempts,
                    u.locked_until,
                    COALESCE(
                        (to_jsonb(u) ->> 'must_change_password')::boolean,
                        FALSE
                    ) AS must_change_password,
                    r.role_name
                FROM pv.users AS u
                JOIN pv.roles AS r ON r.role_id = u.role_id
                WHERE LOWER(u.username) = LOWER(%s)
                """,
                (username,),
            )
        except psycopg2.OperationalError:
            current_app.logger.exception(
                "Database connection failed during login."
            )
            flash(
                "The platform cannot connect to its database. "
                "Check the local DATABASE_URL configuration.",
                "error",
            )
            return render_template("auth/login.html"), 503

        now = datetime.now(timezone.utc)

        if not user:
            record_sign_in_event(
                "Failed sign-in attempt",
                details=f"Unknown username: {username[:60]}",
            )
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html")

        if not user["is_active"]:
            record_sign_in_event(
                "Sign-in refused: account inactive",
                user_id=user["user_id"],
                details=f"Account: {user['username']}.",
            )
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html")

        locked_until = user["locked_until"]
        if locked_until and locked_until > now:
            record_sign_in_event(
                "Sign-in refused: account locked",
                user_id=user["user_id"],
                details=f"Account: {user['username']}.",
            )
            flash(
                "This account is temporarily locked. Try again later, "
                "or ask an administrator to unlock it.",
                "error",
            )
            return render_template("auth/login.html")

        if not check_password_hash(
            user["password_hash"],
            submitted_password,
        ):
            failed_attempts = (user["failed_login_attempts"] or 0) + 1
            new_locked_until = (
                now + timedelta(minutes=LOCKOUT_MINUTES)
                if failed_attempts >= MAX_FAILED_ATTEMPTS
                else None
            )

            with transaction() as cursor:
                cursor.execute(
                    """
                    UPDATE pv.users
                    SET failed_login_attempts = %s,
                        locked_until = %s
                    WHERE user_id = %s
                    """,
                    (
                        failed_attempts,
                        new_locked_until,
                        user["user_id"],
                    ),
                )

            if new_locked_until:
                record_sign_in_event(
                    "Account locked",
                    user_id=user["user_id"],
                    details=(
                        f"Account: {user['username']}. Locked for "
                        f"{LOCKOUT_MINUTES} minutes after "
                        f"{failed_attempts} failed sign-in attempts."
                    ),
                )
            else:
                record_sign_in_event(
                    "Failed sign-in attempt",
                    user_id=user["user_id"],
                    details=(
                        f"Account: {user['username']}. Wrong password "
                        f"(attempt {failed_attempts} of {MAX_FAILED_ATTEMPTS})."
                    ),
                )

            flash("Invalid username or password.", "error")
            return render_template("auth/login.html")

        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.users
                SET failed_login_attempts = 0,
                    locked_until = NULL,
                    last_login_at = %s
                WHERE user_id = %s
                """,
                (now, user["user_id"]),
            )

        session.clear()
        session.permanent = True
        session["user_id"] = user["user_id"]
        session["username"] = user["username"]
        session["full_name"] = user["full_name"]
        session["role"] = user["role_name"]

        record_sign_in_event(
            "Signed in",
            user_id=user["user_id"],
            details=f"Account: {user['username']}.",
            actor_user_id=user["user_id"],
        )

        if user["must_change_password"]:
            session["must_change_password"] = True
            flash(
                "You signed in with a temporary password. Choose a new "
                "password to continue.",
                "info",
            )
            return redirect(url_for("auth.change_password"))

        return redirect(next_path or url_for("core.dashboard"))

    return render_template("auth/login.html")


@bp.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        user = query_one(
            """
            SELECT user_id, username, password_hash
            FROM pv.users
            WHERE user_id = %s
            """,
            (session["user_id"],),
        )

        if not user:
            session.clear()
            flash("Your session is no longer valid. Please sign in again.", "error")
            return redirect(url_for("auth.login"))

        if not check_password_hash(user["password_hash"], current_password):
            flash("Your current password is incorrect.", "error")
            return render_template("auth/change_password.html")

        if len(new_password) < MIN_PASSWORD_LENGTH:
            flash(
                f"Your new password must contain at least "
                f"{MIN_PASSWORD_LENGTH} characters.",
                "error",
            )
            return render_template("auth/change_password.html")

        if new_password != confirm_password:
            flash("The new passwords do not match.", "error")
            return render_template("auth/change_password.html")

        if new_password == current_password:
            flash(
                "Choose a new password that is different from the "
                "current one.",
                "error",
            )
            return render_template("auth/change_password.html")

        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.users
                SET
                    password_hash = %s,
                    failed_login_attempts = 0,
                    locked_until = NULL,
                    must_change_password = FALSE,
                    updated_at = NOW()
                WHERE user_id = %s
                """,
                (
                    generate_password_hash(new_password),
                    session["user_id"],
                ),
            )

        session.pop("must_change_password", None)
        record_sign_in_event(
            "Password changed",
            user_id=user["user_id"],
            details=f"Account: {user['username']}. Changed by the user.",
            actor_user_id=user["user_id"],
        )

        flash("Your password was changed successfully.", "success")
        return redirect(url_for("core.dashboard"))

    return render_template("auth/change_password.html")


@bp.post("/logout")
def logout():
    user_id = session.get("user_id")
    username = session.get("username")
    if user_id:
        record_sign_in_event(
            "Signed out",
            user_id=user_id,
            details=f"Account: {username}." if username else None,
            actor_user_id=user_id,
        )
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
