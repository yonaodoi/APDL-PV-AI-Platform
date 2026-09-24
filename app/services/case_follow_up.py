from datetime import date, timedelta

from app.db import query_all, transaction


def sync_case_follow_up_tasks(case, checks):
    review_checks = [
        check for check in checks if check["status"] == "Review"
    ]
    default_due_date = (
        case.get("follow_up_due_date")
        or date.today() + timedelta(days=7)
    )

    with transaction() as cursor:
        for check in review_checks:
            cursor.execute(
                """
                INSERT INTO pv.case_follow_up_tasks (
                    case_id,
                    check_code,
                    task_title,
                    task_description,
                    assigned_to,
                    due_date
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (case_id, check_code)
                DO UPDATE SET
                    task_title = EXCLUDED.task_title,
                    task_description = EXCLUDED.task_description,
                    due_date = EXCLUDED.due_date,
                    updated_at = CURRENT_TIMESTAMP
                WHERE pv.case_follow_up_tasks.status
                    IN ('Open', 'In progress')
                """,
                (
                    case["case_id"],
                    check["code"],
                    f"Follow up: {check['label']}",
                    check["message"],
                    case.get("created_by"),
                    default_due_date,
                ),
            )

        if review_checks:
            cursor.execute(
                """
                UPDATE pv.case_follow_up_tasks
                SET status = 'Completed',
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE case_id = %s
                  AND status IN ('Open', 'In progress')
                  AND check_code NOT IN (
                      SELECT check_code
                      FROM pv.case_completeness_checks
                      WHERE case_id = %s
                        AND status = 'Review'
                  )
                """,
                (case["case_id"], case["case_id"]),
            )
        else:
            cursor.execute(
                """
                UPDATE pv.case_follow_up_tasks
                SET status = 'Completed',
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE case_id = %s
                  AND status IN ('Open', 'In progress')
                """,
                (case["case_id"],),
            )


def get_open_follow_up_tasks():
    return query_all(
        """
        SELECT
            tasks.*,
            cases.case_number,
            cases.workflow_status,
            users.full_name AS assigned_to_name
        FROM pv.case_follow_up_tasks AS tasks
        JOIN pv.safety_cases AS cases
            ON cases.case_id = tasks.case_id
        LEFT JOIN pv.users AS users
            ON users.user_id = tasks.assigned_to
        WHERE tasks.status IN ('Open', 'In progress')
        ORDER BY tasks.due_date, tasks.created_at
        """
    )
