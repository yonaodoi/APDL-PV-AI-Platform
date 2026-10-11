"""In-app notifications for work that finishes in the background.

Background jobs (AI case assessment, reading an uploaded document) call
:func:`notify`. Every page polls :func:`pending` and shows each new
notification once as a pop-up with a short sound (and a desktop notification
when the user allowed it), so the user learns the work is done even after
closing the page that started it. Only titles and links are sent to the
browser; nothing about the case is stored on the device.
"""

from app.db import query_all, transaction


def notify(user_id, kind, title, message=None, link=None, tone="success"):
    """Never raises: a failed notification must not fail the job."""
    if not user_id:
        return
    try:
        with transaction() as cursor:
            cursor.execute(
                "INSERT INTO pv.user_notifications (user_id, kind, tone, title, message, link) VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, kind, tone if tone in ("success", "error", "info") else "info", title[:200],
                 (message or "")[:2000] or None, (link or "")[:500] or None),
            )
    except Exception:
        try:
            from app.db import get_db

            get_db().rollback()
        except Exception:
            pass


def pending(user_id, limit=10):
    """Notifications not yet shown to this user; marks them delivered."""
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.user_notifications SET delivered_at = NOW()
            WHERE notification_id IN (
                SELECT notification_id FROM pv.user_notifications
                WHERE user_id = %s AND delivered_at IS NULL
                ORDER BY created_at LIMIT %s
            )
            RETURNING notification_id, kind, tone, title, message, link, created_at
            """,
            (user_id, limit),
        )
        rows = cursor.fetchall()
    return sorted((dict(r) for r in rows), key=lambda r: r["created_at"])


def recent(user_id, limit=20):
    return query_all(
        "SELECT * FROM pv.user_notifications WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        (user_id, limit),
    )
