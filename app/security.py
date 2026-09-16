from functools import wraps

from flask import flash, redirect, session, url_for


def login_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(
                url_for("auth.login")
            )

        return view_function(*args, **kwargs)

    return wrapped_view


def roles_required(*allowed_roles):
    allowed_roles = frozenset(allowed_roles)

    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(
                    url_for("auth.login")
                )

            if session.get("role") not in allowed_roles:
                flash(
                    "You do not have permission to access that page.",
                    "error",
                )
                return redirect(
                    url_for("core.dashboard")
                )

            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator