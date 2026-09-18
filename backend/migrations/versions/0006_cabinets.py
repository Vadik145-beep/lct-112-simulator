"""cabinets: evaluation overrides, comments, administrator notifications, backups

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations.grants import grant_table

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A teacher's change of the total with a mandatory reason (PRD 10, 11). The evaluation row
    # keeps the current value; the override keeps the history for the review and the audit.
    op.create_table(
        "evaluation_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("old_total", sa.Float, nullable=False),
        sa.Column("new_total", sa.Float, nullable=False),
        sa.Column("old_passed", sa.Boolean, nullable=False),
        sa.Column("new_passed", sa.Boolean, nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column(
            "teacher_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_evaluation_overrides_attempt", "evaluation_overrides", ["attempt_id"])

    # Teacher's comments on an attempt (shown to the trainee) or on a whole session.
    op.create_table(
        "comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("attempts.id", ondelete="CASCADE")
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_sessions.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "author_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_comments_attempt", "comments", ["attempt_id"])
    op.create_index("ix_comments_session", "comments", ["session_id"])

    # Operational alerts for the administrator (a service down, a provider unavailable, a failed
    # backup); one open row per source until it is acknowledged.
    op.create_table(
        "admin_notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True)),
    )
    op.create_index("ix_admin_notifications_open", "admin_notifications", ["acknowledged_at"])

    # Backups requested from the cabinet; the scheduled ones are files only (the backup service
    # writes them without the API) and are listed from the folder.
    op.create_table(
        "backups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(200)),
        sa.Column("size_bytes", sa.BigInteger),
        sa.Column("error", sa.Text),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )

    for table in ("evaluation_overrides", "comments", "admin_notifications", "backups"):
        grant_table(table)


def downgrade() -> None:
    op.drop_table("backups")
    op.drop_table("admin_notifications")
    op.drop_table("comments")
    op.drop_table("evaluation_overrides")
