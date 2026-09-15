import getpass

import click
import psycopg2
from werkzeug.security import generate_password_hash

from .db import query_one, transaction


def register_cli(app):
    app.cli.add_command(create_admin)


@click.command("create-admin")
@click.option(
    "--username",
    prompt="Username",
)
@click.option(
    "--full-name",
    prompt="Full name",
)
@click.option(
    "--email",
    prompt="Email address",
)
def create_admin(username, full_name, email):
    role = query_one(
        """
        SELECT role_id
        FROM pv.roles
        WHERE role_name = %s
        """,
        ("System Administrator",),
    )

    if not role:
        raise click.ClickException(
            "System Administrator role was not found."
        )

    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass(
        "Confirm password: "
    )

    if password != confirmation:
        raise click.ClickException(
            "The passwords do not match."
        )

    if len(password) < 12:
        raise click.ClickException(
            "The password must contain "
            "at least 12 characters."
        )

    password_hash = generate_password_hash(
        password,
        method="scrypt",
    )

    try:
        with transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO pv.users (
                    role_id,
                    username,
                    full_name,
                    email,
                    password_hash
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING user_id
                """,
                (
                    role["role_id"],
                    username.strip(),
                    full_name.strip(),
                    email.strip().lower(),
                    password_hash,
                ),
            )

            user = cursor.fetchone()

    except psycopg2.IntegrityError:
        raise click.ClickException(
            "The username or email address "
            "already exists."
        )

    click.echo(
        "System Administrator created "
        f"with user ID {user['user_id']}."
    )