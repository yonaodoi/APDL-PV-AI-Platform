from pathlib import Path

import psycopg2

from config import Config


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_LOCK_ID = 92745131


def apply_migrations():
    Config.validate()
    connection = psycopg2.connect(Config.DATABASE_URL)

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(%s)",
                (MIGRATION_LOCK_ID,),
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        connection.commit()

        with connection.cursor() as cursor:
            cursor.execute("SELECT version FROM public.schema_migrations")
            applied_versions = {row[0] for row in cursor.fetchall()}

        migration_files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
        for migration_file in migration_files:
            version = migration_file.stem
            if version in applied_versions:
                continue

            sql = migration_file.read_text(encoding="utf-8")
            with connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO public.schema_migrations (version) VALUES (%s)",
                    (version,),
                )
            connection.commit()
            print(f"Applied migration {version}")
    finally:
        if not connection.closed:
            connection.rollback()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    (MIGRATION_LOCK_ID,),
                )
            connection.close()


if __name__ == "__main__":
    apply_migrations()
