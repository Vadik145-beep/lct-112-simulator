"""call intake access: the 112 operator mode is shown only to users the administrator allows

``users.call_intake_access`` — false hides the mode from the forms, lists and analytics;
existing administrators get it, everybody else starts without it.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("call_intake_access", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE users SET call_intake_access = true WHERE role = 'admin'")


def downgrade() -> None:
    op.drop_column("users", "call_intake_access")
