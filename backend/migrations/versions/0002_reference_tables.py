"""reference tables from the organizers' dataset

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-16
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations.grants import grant_table

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "services",
    "incident_groups",
    "incident_types",
    "incident_flags",
    "response_statuses",
    "card_statuses",
    "reject_reasons",
    "typical_errors",
    "caller_topics",
    "tickets",
    "streets",
]


def upgrade() -> None:
    op.create_table(
        "services",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("short_title", sa.String(64), nullable=False),
        sa.Column("no_reject", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("via_arm112", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "incident_groups",
        sa.Column("code", sa.String(8), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("number", sa.Integer, nullable=False),
        sa.Column("source_row", sa.Integer),
    )
    op.create_table(
        "incident_types",
        sa.Column("code", sa.String(24), primary_key=True),
        sa.Column(
            "group_code",
            sa.String(8),
            sa.ForeignKey("incident_groups.code", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stat_group", sa.String(200)),
        sa.Column("sign1", sa.String(200), nullable=False),
        sa.Column("sign2", sa.String(200)),
        sa.Column("sign3", sa.String(200)),
        sa.Column("hints", sa.Text),
        sa.Column("final_title", sa.String(300), nullable=False),
        sa.Column("ekp_title", sa.String(300)),
        sa.Column("main_service", sa.String(32)),
        sa.Column("main_service_raw", sa.String(64)),
        sa.Column("service_rules", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column(
            "flag_codes",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "required_topics",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("source_row", sa.Integer),
    )
    op.create_index("ix_incident_types_group_code", "incident_types", ["group_code"])
    op.create_table(
        "incident_flags",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("column_hint", sa.String(200)),
        sa.Column("order", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "response_statuses",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("order", sa.Integer, nullable=False),
        sa.Column("is_system", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("is_final", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("requires_comment", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("requires_order_number", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "allowed_next",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("description", sa.Text),
        sa.Column("memo_page", sa.Integer),
    )
    op.create_table(
        "card_statuses",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("is_alert", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("order", sa.Integer, nullable=False),
    )
    op.create_table(
        "reject_reasons",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("order", sa.Integer, nullable=False),
    )
    op.create_table(
        "typical_errors",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("penalty", sa.Integer, nullable=False),
        sa.Column("memo_ref", sa.String(200)),
        sa.Column("example", sa.Text),
    )
    op.create_table(
        "caller_topics",
        sa.Column("code", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("keywords", postgresql.ARRAY(sa.String(64)), nullable=False, server_default="{}"),
        sa.Column("order", sa.Integer, nullable=False),
    )
    op.create_table(
        "tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_no", sa.Integer, nullable=False),
        sa.Column("item_no", sa.Integer, nullable=False),
        sa.Column("situation", sa.Text, nullable=False),
        sa.Column("address", sa.Text, nullable=False),
        sa.Column("ocr_confident", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("ocr_confidence", sa.Float),
        sa.Column("traps", postgresql.ARRAY(sa.String(32)), nullable=False, server_default="{}"),
        sa.UniqueConstraint("ticket_no", "item_no", name="uq_tickets_ticket_item"),
    )
    op.create_table(
        "streets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_normalized", sa.String(200), nullable=False),
        sa.Column("okrug", sa.String(16), nullable=False),
        sa.Column("district", sa.String(100), nullable=False),
        sa.Column("source", sa.String(32), nullable=False, server_default="osm"),
        sa.UniqueConstraint("name", "okrug", "district", name="uq_streets_name_okrug_district"),
    )
    op.create_index("ix_streets_name_normalized", "streets", ["name_normalized"])

    # The importer runs under the application role, so it needs full access here.
    for table in TABLES:
        grant_table(table)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
