"""telephony: SIP account of a trainee, call state of an attempt, operational settings

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from migrations.grants import grant_table

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SIP password of the trainee's softphone, encrypted with a key derived from SECRET_KEY
    # (PRD 9.5). Created on demand for students; empty for teachers and administrators.
    op.add_column("users", sa.Column("sip_password_enc", sa.String(255)))

    # The call of a call-intake attempt (PRD 9.5, 11): idle → ringing → answered → ended.
    op.add_column(
        "attempts",
        sa.Column("call_state", sa.String(16), nullable=False, server_default="idle"),
    )
    op.add_column("attempts", sa.Column("call_ended_at", sa.DateTime(timezone=True)))
    op.add_column("attempts", sa.Column("call_end_reason", sa.String(16)))
    # Marks the operator sets in the call panel; the evaluation reads them (detectors).
    op.add_column(
        "attempts",
        sa.Column("call_dropped_marked", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "attempts",
        sa.Column("no_contact_marked", sa.Boolean, nullable=False, server_default=sa.false()),
    )

    # Operational settings edited by the administrator (PRD 10: `settings`), one row per
    # section; telephony is the first one.
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB, nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    grant_table("settings")


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_column("attempts", "no_contact_marked")
    op.drop_column("attempts", "call_dropped_marked")
    op.drop_column("attempts", "call_end_reason")
    op.drop_column("attempts", "call_ended_at")
    op.drop_column("attempts", "call_state")
    op.drop_column("users", "sip_password_enc")
