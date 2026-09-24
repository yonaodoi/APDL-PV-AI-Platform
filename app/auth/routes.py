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

from app.db import query_one, transaction
from app.security import login_required


bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("core.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        submitted_password = request.form.get("password", "")

        try:
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
                    r.role_name
                FROM pv.users AS u
                JOIN pv.roles AS r ON r.role_id = u.role_id
                WHERE u.username = %s
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

        if not user or not user["is_active"]:
            flash("Invalid username or password.", "error")
            return render_template("auth/login.html")

        locked_until = user["locked_until"]
        if locked_until and locked_until > now:
            flash(
                "This account is temporarily locked. Try again later.",
                "error",
            )
            return render_template("auth/login.html")

        if not check_password_hash(
            user["password_hash"],
            submitted_password,
        ):
            failed_attempts = user["failed_login_attempts"] + 1
            new_locked_until = (
                now + timedelta(minutes=15)
                if failed_attempts >= 5
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

        return redirect(url_for("core.dashboard"))

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
            SELECT user_id, password_hash
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

        if len(new_password) < 10:
            flash("Your new password must contain at least 10 characters.", "error")
            return render_template("auth/change_password.html")

        if new_password != confirm_password:
            flash("The new passwords do not match.", "error")
            return render_template("auth/change_password.html")

        with transaction() as cursor:
            cursor.execute(
                """
                UPDATE pv.users
                SET
                    password_hash = %s,
                    failed_login_attempts = 0,
                    locked_until = NULL,
                    updated_at = NOW()
                WHERE user_id = %s
                """,
                (
                    generate_password_hash(new_password),
                    session["user_id"],
                ),
            )

        flash("Your password was changed successfully.", "success")
        return redirect(url_for("core.dashboard"))

    return render_template("auth/change_password.html")


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))