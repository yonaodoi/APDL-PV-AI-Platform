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


def get_open_reminders():
    create_overdue_reminders()
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


def get_open_reminder_count():
    create_overdue_reminders()
    reminder = query_one(
        """
        SELECT COUNT(*) AS count
        FROM pv.case_follow_up_reminders
        WHERE acknowledged_at IS NULL
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
