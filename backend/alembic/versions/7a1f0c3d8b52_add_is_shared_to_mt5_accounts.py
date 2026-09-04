"""add is_shared to mt5_accounts

Revision ID: 7a1f0c3d8b52
Revises: 194efd489b33
Create Date: 2026-09-04 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a1f0c3d8b52'
down_revision: Union[str, Sequence[str], None] = '194efd489b33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default matches MT5Account.is_shared's Python-side default
    # (False) -- required, not just nice-to-have: this column is NOT NULL
    # and the live database already has account rows, so a bare add_column
    # with no default would fail applying this migration (same reasoning as
    # 44059d35c4c2_add_loss_recovery_enabled_to_trading_.py).
    #
    # Every existing row therefore stays private to its owner: sharing is
    # opt-in, set afterwards per account from the /accounts page, never
    # backfilled here.
    with op.batch_alter_table('mt5_accounts', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('is_shared', sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('mt5_accounts', schema=None) as batch_op:
        batch_op.drop_column('is_shared')
