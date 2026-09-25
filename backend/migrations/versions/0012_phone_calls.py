"""phone calls: a lesson may ring the trainees' own phones through MultiFon (docs/MULTIFON.md)

``training_sessions.phone_calls`` — the teacher's checkbox «Звонки на телефон»;
``users.phone`` — the trainee's own number (7XXXXXXXXXX), entered by himself.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "training_sessions",
        sa.Column("phone_calls", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("phone", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "phone")
    op.drop_column("training_sessions", "phone_calls")
