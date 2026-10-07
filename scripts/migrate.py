import sys
from pathlib import Path

import psycopg2

# Allow running as "python scripts\migrate.py" from the project folder.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config  # noqa: E402


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


def baseline_migrations(up_to_version):
    """Record migrations up to ``up_to_version`` (e.g. "033") as applied
    without running them. Use once for a database that was created by
    running the SQL files by hand."""
    Config.validate()
    connection = psycopg2.connect(Config.DATABASE_URL)

    try:
        with connection.cursor() as cursor:
            # Migration 033 creates this table; its presence shows the
            # database really is at least that far along.
            cursor.execute(
                "SELECT to_regclass('pv.case_duplicate_reviews') IS NOT NULL"
            )
            if up_to_version >= "033" and not cursor.fetchone()[0]:
                raise SystemExit(
                    "pv.case_duplicate_reviews does not exist, so the "
                    "database is not at migration 033. Nothing was recorded."
                )

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS public.schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            for migration_file in sorted(
                MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")
            ):
                version = migration_file.stem
                if version[:3] > up_to_version:
                    continue
                cursor.execute(
                    """
                    INSERT INTO public.schema_migrations (version)
                    VALUES (%s)
                    ON CONFLICT (version) DO NOTHING
                    """,
                    (version,),
                )
                print(f"Recorded {version} as already applied")
        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--baseline":
        baseline_migrations(sys.argv[2])
    else:
        apply_migrations()
