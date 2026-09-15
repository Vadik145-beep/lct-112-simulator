"""users and audit_log

Revision ID: 0001
Revises:
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations.grants import app_role, grant_sequence, grant_table

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("login", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column(
            "role",
            sa.Enum("student", "teacher", "admin", name="user_role"),
            nullable=False,
        ),
        sa.Column("service_code", sa.String(32)),
        sa.Column("is_blocked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("failed_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
        sa.Column("token_version", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_role", sa.String(16)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("entity", sa.String(64)),
        sa.Column("entity_id", sa.String(64)),
        sa.Column("details", postgresql.JSONB),
        sa.Column("ip", sa.String(64)),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_audit_log_at", "audit_log", ["at"])
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])

    # The application role may read and append to the audit log but never change it.
    grant_table("users")
    grant_table("audit_log", "SELECT, INSERT")
    grant_sequence("audit_log_id_seq")
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON TABLE audit_log FROM "{app_role()}"')


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS user_role")
