"""dds voice: card-response lessons kept their voice, now that the teacher can turn it off

Until the dialog mode became a setting of a card-response lesson too, the duty officer and
the squad leader were always voiced and ``voice_enabled`` was only about the caller of a
112 call. Lessons created before this carry ``false`` and would fall silent, so they are
switched on; new lessons get the checkbox.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE training_sessions SET voice_enabled = true "
        "WHERE mode = 'card_response' AND voice_enabled = false"
    )


def downgrade() -> None:
    # Which lessons were switched on here is not recorded; nothing to undo.
    pass
