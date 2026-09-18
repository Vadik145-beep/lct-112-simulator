"""analytics: skill ratings of trainees and the adaptive card selection flag of a session

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from migrations.grants import grant_table

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rating of a trainee per incident group and mode (PRD 9.7, Elo-like; start 1400).
    op.create_table(
        "skill_ratings",
        sa.Column(
            "student_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("incident_group", sa.String(8), primary_key=True),
        sa.Column("mode", sa.String(16), primary_key=True),
        sa.Column("rating", sa.Float, nullable=False),
        sa.Column("n", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    grant_table("skill_ratings")

    # Adaptive selection (PRD 9.7): the next card of each trainee comes from the weakest
    # incident group at a difficulty near the rating, unseen scenarios first.
    op.add_column(
        "training_sessions",
        sa.Column("adaptive", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("training_sessions", "adaptive")
    op.drop_table("skill_ratings")
