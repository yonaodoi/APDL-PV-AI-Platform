"""Clear messages instead of a bare "Internal Server Error".

Every unexpected failure is logged with a short reference, the database
transaction is rolled back, and the person sees what happened and what to do.
Common data problems from the database (a date or number in the wrong
format, text too long, a duplicate number, a missing required value) are
explained in plain words.
"""

import secrets

from flask import current_app, jsonify, render_template, request, session
try:
    from flask_wtf.csrf import CSRFError
except ImportError:  # pragma: no cover - very old Flask-WTF
    from werkzeug.exceptions import BadRequest as CSRFError
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge


def _rollback():
    try:
        from flask import g

        if "db" in g:
            g.db.rollback()
    except Exception:
        pass


def _field(name):
    return (name or "").replace("_", " ").strip() or "a field"


def explain_database_error(error):
    """A plain explanation for common database errors, or None."""
    code = getattr(error, "pgcode", None)
    diag = getattr(error, "diag", None)
    column = getattr(diag, "column_name", None) if diag else None
    constraint = getattr(diag, "constraint_name", None) if diag else None
    detail = (getattr(diag, "message_detail", None) if diag else None) or ""
    text = str(error).split("\n")[0]
    if code in ("22007", "22008"):
        return "A date was not in a valid format or does not exist (for example 31 February). Use the date picker or YYYY-MM-DD."
    if code == "22001":
        return "One of the entries is longer than the field allows. Shorten the longest text and try again."
    if code == "22003":
        return "A number is too large or too small for its field."
    if code == "22P02":
        return "A value was not in the expected form (for example text typed where a number is needed). " \
               "Check the numbers and choices you entered."
    if code == "23505":
        key = detail.replace("Key ", "").replace("already exists.", "already exists").strip()
        return f"This would create a duplicate: {key or 'the same identifier is already used'}. Use a different number or open the existing record."
    if code == "23503":
        return "This refers to a record that no longer exists, or is still used by other records. Reload the page and try again."
    if code == "23502":
        return f"A required value is missing: {_field(column)}."
    if code == "23514":
        return f"A value is not allowed here ({_field(constraint)}). Check the choices and dates you entered."
    if code and code.startswith("08") or "could not connect" in text or "server closed the connection" in text:
        return "The database could not be reached. Check that PostgreSQL is running, then try again."
    if code == "42P01" or code == "42703":
        return "The database is missing a table or column this page needs. Run python scripts\\migrate.py and restart the app."
    return None


def _wants_json():
    return request.path.endswith(".json") or request.is_json or \
        request.accept_mimetypes.best == "application/json" or request.headers.get("X-Requested-With") == "fetch"


def _page(status, title, message, *, reference=None, saved_note=True):
    posted = request.method in ("POST", "PUT", "PATCH", "DELETE")
    if _wants_json():
        return jsonify({"error": message, "reference": reference}), status
    try:
        return render_template("errors/error.html", status=status, title=title, message=message,
                               reference=reference, posted=posted and saved_note), status
    except Exception:
        current_app.logger.exception("The error page itself failed")
        return (f"<h1>{title}</h1><p>{message}</p>"
                + (f"<p>Reference: {reference}</p>" if reference else "")
                + "<p><a href='javascript:history.back()'>Go back</a></p>"), status


def register_error_handlers(app):

    @app.errorhandler(CSRFError)
    def csrf_expired(error):
        _rollback()
        return _page(400, "The form had expired",
                     "Nothing was saved because the page had been open too long or you were signed out. "
                     "Go back, copy anything you typed, reload the page and enter it again.")

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(error):
        _rollback()
        limit = (app.config.get("MAX_CONTENT_LENGTH") or 0) // (1024 * 1024)
        return _page(413, "The file is too large",
                     f"Nothing was saved. Files can be up to {limit} MB. Choose a smaller file "
                     "(or ask the administrator to raise MAX_UPLOAD_MB).")

    @app.errorhandler(404)
    def not_found(error):
        return _page(404, "Page not found",
                     "That page or record does not exist, or it was deleted. Use the menu to find what you need.",
                     saved_note=False)

    @app.errorhandler(403)
    def forbidden(error):
        return _page(403, "Not allowed",
                     "Your role does not allow this action. Ask your administrator if you think it should.",
                     saved_note=False)

    @app.errorhandler(405)
    def wrong_method(error):
        return _page(405, "That action cannot be done this way",
                     "Use the buttons on the page instead of the browser address bar.", saved_note=False)

    @app.errorhandler(Exception)
    def unexpected(error):
        if isinstance(error, HTTPException):
            return error
        _rollback()
        reference = secrets.token_hex(3).upper()
        current_app.logger.exception("Unexpected error %s on %s %s (user %s)", reference, request.method,
                                     request.path, session.get("user_id"))
        try:
            from app.audit import write_audit_log

            write_audit_log("system", 0, "Unexpected error", session.get("user_id"),
                            f"Reference {reference}: {request.method} {request.path} – {type(error).__name__}")
        except Exception:
            _rollback()
        explanation = explain_database_error(error)
        if explanation:
            return _page(422 if request.method == "POST" else 500, "This could not be saved", explanation,
                         reference=reference)
        return _page(500, "Something went wrong",
                     "The step stopped because of an unexpected error. The administrator can find the details "
                     "in the application log using the reference below.", reference=reference)
