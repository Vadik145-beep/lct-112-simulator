"""training tables: groups, scenarios, sessions, attempts, evaluations, session events

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-17
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations.grants import grant_sequence, grant_table

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "groups",
    "group_members",
    "scenarios",
    "scenario_versions",
    "training_sessions",
    "attempts",
    "evaluations",
    "session_events",
]


def _uuid_pk() -> sa.Column:
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def _user_ref(name: str, nullable: bool = True, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(
        name,
        postgresql.UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    op.create_table(
        "groups",
        _uuid_pk(),
        sa.Column("title", sa.String(200), nullable=False),
        _user_ref("teacher_id"),
        _created_at(),
    )
    op.create_table(
        "group_members",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "scenarios",
        _uuid_pk(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column(
            "ticket_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tickets.id", ondelete="SET NULL"),
        ),
        sa.Column("ticket_ref", sa.String(16)),
        sa.Column("incident_type_code", sa.String(24)),
        sa.Column("service_code", sa.String(32)),
        sa.Column("difficulty", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("current_version", sa.Integer, nullable=False, server_default="1"),
        _user_ref("author_id"),
        sa.Column("seed_key", sa.String(100), unique=True),
        _created_at(),
    )
    op.create_index("ix_scenarios_kind_status", "scenarios", ["kind", "status"])
    op.create_table(
        "scenario_versions",
        _uuid_pk(),
        sa.Column(
            "scenario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("body", postgresql.JSONB, nullable=False),
        sa.Column("revision_comment", sa.Text),
        _user_ref("created_by"),
        _created_at(),
        sa.UniqueConstraint("scenario_id", "version", name="uq_scenario_versions"),
    )
    op.create_table(
        "training_sessions",
        _uuid_pk(),
        sa.Column("title", sa.String(300), nullable=False),
        _user_ref("teacher_id"),
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="SET NULL"),
        ),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("card_source", sa.String(16), nullable=False, server_default="scenarios"),
        sa.Column(
            "scenario_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "incident_groups", postgresql.ARRAY(sa.String(8)), nullable=False, server_default="{}"
        ),
        sa.Column("difficulty", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "service_profile", postgresql.ARRAY(sa.String(32)), nullable=False, server_default="{}"
        ),
        sa.Column("norm_seconds", sa.Integer, nullable=False, server_default="30"),
        sa.Column("pass_threshold", sa.Integer, nullable=False, server_default="70"),
        sa.Column("hints_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("voice_enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("dialog_mode", sa.String(16), nullable=False, server_default="select"),
        sa.Column("weights", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("seed_key", sa.String(100), unique=True),
        _created_at(),
    )
    op.create_index(
        "ix_training_sessions_group_status", "training_sessions", ["group_id", "status"]
    )
    op.create_table(
        "attempts",
        _uuid_pk(),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _user_ref("student_id", nullable=False, ondelete="CASCADE"),
        sa.Column(
            "scenario_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scenarios.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("scenario_version", sa.Integer, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("card_number", sa.String(16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("answered_at", sa.DateTime(timezone=True)),
        sa.Column("primary_status_at", sa.DateTime(timezone=True)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("draft", postgresql.JSONB),
        sa.Column("result", postgresql.JSONB),
        sa.Column("status_log", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("dialog", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("recording_path", sa.String(500)),
        sa.Column("client_submission_id", sa.String(64), unique=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="issued"),
        sa.Column("response_status", sa.String(32), nullable=False, server_default="added"),
        sa.Column("card_status", sa.String(32), nullable=False, server_default="registered"),
        _created_at(),
    )
    op.create_index("ix_attempts_session_student", "attempts", ["session_id", "student_id"])
    op.create_index("ix_attempts_state", "attempts", ["state"])
    op.create_table(
        "evaluations",
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attempts.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("total", sa.Float, nullable=False),
        sa.Column("passed", sa.Boolean, nullable=False),
        sa.Column("components", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("errors", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("ai_comment", sa.Text),
        sa.Column("methods", postgresql.JSONB, nullable=False, server_default="{}"),
        _created_at(),
    )
    op.create_table(
        "session_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("training_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("type", sa.String(48), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True)),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "seq", name="uq_session_events_seq"),
    )

    for table in TABLES:
        grant_table(table)
    grant_sequence("session_events_id_seq")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
