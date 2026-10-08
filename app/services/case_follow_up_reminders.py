from app.db import query_all, query_one, transaction


def create_overdue_reminders():
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.case_follow_up_reminders (
                task_id, message
            )
            SELECT tasks.task_id,
                   'Follow-up task is overdue: ' || tasks.task_title
            FROM pv.case_follow_up_tasks AS tasks
            WHERE tasks.status IN ('Open', 'In progress')
              AND tasks.due_date < CURRENT_DATE
            ON CONFLICT (task_id, reminder_date) DO NOTHING
            """
        )
        return cursor.rowcount


def get_open_reminders():
    return query_all(
        """
        SELECT reminders.*, tasks.task_title, tasks.due_date,
               cases.case_id, cases.case_number
        FROM pv.case_follow_up_reminders AS reminders
        JOIN pv.case_follow_up_tasks AS tasks
            ON tasks.task_id = reminders.task_id
        JOIN pv.safety_cases AS cases
            ON cases.case_id = tasks.case_id
        WHERE reminders.acknowledged_at IS NULL
          AND tasks.status IN ('Open', 'In progress')
        ORDER BY tasks.due_date, reminders.created_at
        """
    )


def ensure_overdue_reminders():
    """Create today's overdue reminders without ever breaking a page.

    Reminders used to be created only by the manual
    ``flask generate-follow-up-reminders`` command, so they never appeared.
    This runs when the dashboard or follow-up pages open. The unique
    (task_id, reminder_date) key means at most one reminder per task per day.
    """
    from flask import current_app

    from app.db import get_db

    try:
        return create_overdue_reminders()
    except Exception:
        current_app.logger.exception("Could not create overdue reminders")
        try:
            get_db().rollback()
        except Exception:
            pass
        return 0


def get_open_reminder_count():
    # Count only reminders whose task is still open, matching the
    # Overdue Reminders page.
    reminder = query_one(
        """
        SELECT COUNT(*) AS count
        FROM pv.case_follow_up_reminders AS reminders
        JOIN pv.case_follow_up_tasks AS tasks
            ON tasks.task_id = reminders.task_id
        WHERE reminders.acknowledged_at IS NULL
          AND tasks.status IN ('Open', 'In progress')
        """
    )
    return reminder["count"] if reminder else 0


def acknowledge_reminder(reminder_id, user_id):
    with transaction() as cursor:
        cursor.execute(
            """
            UPDATE pv.case_follow_up_reminders
            SET acknowledged_at = CURRENT_TIMESTAMP,
                acknowledged_by = %s
            WHERE reminder_id = %s
              AND acknowledged_at IS NULL
            RETURNING reminder_id
            """,
            (user_id, reminder_id),
        )
        return cursor.fetchone()
