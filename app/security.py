from functools import wraps
from urllib.parse import urlsplit

from flask import flash, redirect, request, session, url_for


def safe_next_path(value):
    """Return ``value`` if it is a path on this site, otherwise None.

    Stops the sign-in page being used to send people to another website.
    """
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return None
    if value.endswith("?"):
        value = value[:-1]
    return value or None


def login_redirect():
    """Send the user to sign in, remembering the page they asked for."""
    next_path = None
    if request.method == "GET":
        next_path = safe_next_path(request.full_path)
        if next_path == "/":
            next_path = None
    if next_path:
        flash("Please sign in to continue.", "info")
        return redirect(url_for("auth.login", next=next_path))
    return redirect(url_for("auth.login"))


def login_required(view_function):
    @wraps(view_function)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            return login_redirect()

        return view_function(*args, **kwargs)

    return wrapped_view


def roles_required(*allowed_roles):
    allowed_roles = frozenset(allowed_roles)

    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(*args, **kwargs):
            if not session.get("user_id"):
                return login_redirect()

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
