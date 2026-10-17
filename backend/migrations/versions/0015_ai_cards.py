"""ai cards: cards written for one trainee during a lesson «Задания формирует ИИ» (issue #265)

``ai_cards`` — the trainee's card number in the lesson (unique, so a card is never written
twice), its state while being written, the scenario once stored and why it was chosen.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations.grants import grant_table

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_cards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="generating"),
        sa.Column(
            "scenario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="SET NULL"),
        ),
        sa.Column("method", sa.String(16)),
        sa.Column(
            "reasons", postgresql.ARRAY(sa.Text), nullable=False, server_default="{}"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("session_id", "student_id", "seq", name="uq_ai_cards_seq"),
    )
    grant_table("ai_cards")


def downgrade() -> None:
    op.drop_table("ai_cards")
