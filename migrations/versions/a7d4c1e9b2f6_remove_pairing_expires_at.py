"""remove pairing expiry

Revision ID: a7d4c1e9b2f6
Revises: 605cdba02237
Create Date: 2026-11-15 08:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7d4c1e9b2f6'
down_revision = '605cdba02237'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('smart_outlets', schema=None) as batch_op:
        batch_op.drop_column('pairing_expires_at')


def downgrade():
    with op.batch_alter_table('smart_outlets', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('pairing_expires_at', sa.DateTime(), nullable=True)
        )
