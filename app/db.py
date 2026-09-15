from contextlib import contextmanager

import psycopg2
from flask import current_app, g
from psycopg2.extras import RealDictCursor


def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(
            current_app.config["DATABASE_URL"]
        )

    return g.db


def close_db(error=None):
    connection = g.pop("db", None)

    if connection is not None:
        connection.close()


@contextmanager
def transaction():
    connection = get_db()

    try:
        with connection.cursor(
            cursor_factory=RealDictCursor
        ) as cursor:
            yield cursor

        connection.commit()

    except Exception:
        connection.rollback()
        raise


def query_one(sql, parameters=()):
    with get_db().cursor(
        cursor_factory=RealDictCursor
    ) as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchone()


def query_all(sql, parameters=()):
    with get_db().cursor(
        cursor_factory=RealDictCursor
    ) as cursor:
        cursor.execute(sql, parameters)
        return cursor.fetchall()