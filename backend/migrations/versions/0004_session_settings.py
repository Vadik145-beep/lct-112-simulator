"""session settings: cards per trainee and the «Не завершено» threshold

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0 = the whole queue of the session.
    op.add_column(
        "training_sessions",
        sa.Column("cards_per_student", sa.Integer, nullable=False, server_default="0"),
    )
    # Seconds after the primary status before an open card becomes «Не завершено»
    # (memo: 48 hours in АРМ-112; a lesson may shorten it).
    op.add_column(
        "training_sessions",
        sa.Column("unfinished_seconds", sa.Integer, nullable=False, server_default="172800"),
    )


def downgrade() -> None:
    op.drop_column("training_sessions", "unfinished_seconds")
    op.drop_column("training_sessions", "cards_per_student")
