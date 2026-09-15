"""Helpers to grant the restricted application role access to new tables.

Migrations run as the schema owner; the API connects as APP_DB_USER, which only
receives the privileges granted here.
"""

from alembic import op

from app.config import get_settings


def app_role() -> str:
    return get_settings().app_db_user


def grant_table(table: str, privileges: str = "SELECT, INSERT, UPDATE, DELETE") -> None:
    role = app_role()
    op.execute(f'GRANT {privileges} ON TABLE "{table}" TO "{role}"')


def grant_sequence(sequence: str) -> None:
    role = app_role()
    op.execute(f'GRANT USAGE, SELECT ON SEQUENCE "{sequence}" TO "{role}"')
