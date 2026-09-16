from app.db import transaction


def write_audit_log(
    record_type,
    record_id,
    action,
    actor_user_id=None,
    details=None,
):
    with transaction() as cursor:
        cursor.execute(
            """
            INSERT INTO pv.audit_log (
                record_type,
                record_id,
                action,
                details,
                actor_user_id
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                record_type,
                record_id,
                action,
                details,
                actor_user_id,
            ),
        )