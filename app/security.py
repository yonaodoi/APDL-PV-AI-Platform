from functools import wraps

from flask import abort, redirect, session, url_for


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
                abort(403)

            return view_function(*args, **kwargs)

        return wrapped_view

    return decorator