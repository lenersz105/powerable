"""add outlet IP address and hardware state

Revision ID: b7c2e5f1a9d4
Revises: a7d4c1e9b2f6
Create Date: 2026-11-15 09:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c2e5f1a9d4'
down_revision = 'a7d4c1e9b2f6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('smart_outlets', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('ip_address', sa.String(length=45), nullable=True)
        )
        batch_op.add_column(
            sa.Column('hardware_connected', sa.Boolean(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table('smart_outlets', schema=None) as batch_op:
        batch_op.drop_column('hardware_connected')
        batch_op.drop_column('ip_address')
