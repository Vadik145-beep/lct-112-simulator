"""lesson phone: the number the teacher entered for a lesson with a live call to the phone

``training_sessions.phone`` — 7XXXXXXXXXX; the calls of the lesson ring it instead of the
trainee's own number. Empty = the trainee's own number as before (0012).

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("training_sessions", sa.Column("phone", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("training_sessions", "phone")
