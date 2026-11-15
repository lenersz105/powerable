"""add outlet network mode and connection type

Revision ID: c8f4b2d6a1e3
Revises: b7c2e5f1a9d4
Create Date: 2026-11-15 11:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c8f4b2d6a1e3'
down_revision = 'b7c2e5f1a9d4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('smart_outlets', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('network_mode', sa.String(length=16), nullable=True)
        )
        batch_op.add_column(
            sa.Column('connection_type', sa.String(length=16), nullable=True)
        )


def downgrade():
    with op.batch_alter_table('smart_outlets', schema=None) as batch_op:
        batch_op.drop_column('connection_type')
        batch_op.drop_column('network_mode')
