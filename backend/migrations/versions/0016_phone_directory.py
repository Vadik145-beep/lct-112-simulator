"""phone directory: the numbers of the dispatch services' own people, and the lesson's
permission to dial the caller of the card (issue #263)

``phone_directory`` — up to ten rows per service: a role, a name, a four-digit number unique
within the service; ``training_sessions.allow_caller_call`` — the trainee may dial the caller's
number from the card, off by default.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from migrations.grants import grant_table

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "phone_directory",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "service_code",
            sa.String(32),
            sa.ForeignKey("services.code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("number", sa.String(4), nullable=False),
        sa.Column("position", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("service_code", "number", name="uq_phone_directory_number"),
    )
    op.create_index("ix_phone_directory_service_code", "phone_directory", ["service_code"])
    grant_table("phone_directory")
    op.add_column(
        "training_sessions",
        sa.Column("allow_caller_call", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("training_sessions", "allow_caller_call")
    op.drop_index("ix_phone_directory_service_code", table_name="phone_directory")
    op.drop_table("phone_directory")
