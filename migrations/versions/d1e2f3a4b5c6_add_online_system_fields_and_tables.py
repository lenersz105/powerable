"""add online system fields and tables

Adds fields for the complete online system integration:
- Network/Hardware ESP32 ID separation
- 3-socket relay state tracking
- Model and socket count
- Per-device authentication secret
- Mesh gateway support
- New tables: relay_commands, energy_readings, wifi_credentials

Revision ID: d1e2f3a4b5c6
Revises: c8f4b2d6a1e3
Create Date: 2026-11-19 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1e2f3a4b5c6'
down_revision = 'c8f4b2d6a1e3'
branch_labels = None
depends_on = None


def upgrade():
    # ------------------------------------------------------------------
    # 1. Add new columns to smart_outlets
    # ------------------------------------------------------------------
    with op.batch_alter_table('smart_outlets', schema=None) as batch_op:
        # ESP32 dual-board identity
        batch_op.add_column(
            sa.Column('hardware_esp32_id', sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column('network_esp32_id', sa.String(length=64), nullable=True)
        )
        # Product information
        batch_op.add_column(
            sa.Column('model', sa.String(length=64), nullable=True)
        )
        batch_op.add_column(
            sa.Column('socket_count', sa.Integer(), nullable=True, server_default='3')
        )
        # Per-device authentication
        batch_op.add_column(
            sa.Column('device_api_secret', sa.String(length=256), nullable=True)
        )
        # 3-socket relay states (relay_state preserved for backward compat)
        batch_op.add_column(
            sa.Column('relay_state_1', sa.String(length=10), nullable=False, server_default='unknown')
        )
        batch_op.add_column(
            sa.Column('relay_state_2', sa.String(length=10), nullable=False, server_default='unknown')
        )
        batch_op.add_column(
            sa.Column('relay_state_3', sa.String(length=10), nullable=False, server_default='unknown')
        )
        # Wi-Fi configuration
        batch_op.add_column(
            sa.Column('wifi_ssid', sa.String(length=255), nullable=True)
        )
        # Mesh support
        batch_op.add_column(
            sa.Column('mesh_gateway', sa.Boolean(), nullable=False, server_default='0')
        )
        batch_op.add_column(
            sa.Column('mesh_id', sa.String(length=64), nullable=True)
        )

    # ------------------------------------------------------------------
    # 2. Create relay_commands table
    # ------------------------------------------------------------------
    op.create_table(
        'relay_commands',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('outlet_id', sa.Integer(), nullable=False),
        sa.Column('socket_number', sa.Integer(), nullable=False),
        sa.Column('desired_state', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('requested_by', sa.Integer(), nullable=True),
        sa.Column('command_id', sa.String(length=64), unique=True, nullable=False, index=True),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['outlet_id'], ['smart_outlets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['requested_by'], ['users.id'], ondelete='SET NULL'),
        sa.CheckConstraint('socket_number IN (1, 2, 3)', name='ck_socket_number_range'),
        sa.CheckConstraint("desired_state IN ('on', 'off')", name='ck_desired_state'),
        sa.CheckConstraint("status IN ('pending', 'acknowledged', 'completed', 'expired', 'failed')",
                          name='ck_command_status'),
    )
    op.create_index(op.f('ix_relay_commands_outlet_id_status'), 'relay_commands',
                    ['outlet_id', 'status'])
    op.create_index(op.f('ix_relay_commands_expires_at'), 'relay_commands',
                    ['expires_at'])

    # ------------------------------------------------------------------
    # 3. Create energy_readings table
    # ------------------------------------------------------------------
    op.create_table(
        'energy_readings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('outlet_id', sa.Integer(), nullable=False),
        sa.Column('socket_number', sa.Integer(), nullable=False),
        sa.Column('voltage', sa.Float(), nullable=True),
        sa.Column('current', sa.Float(), nullable=True),
        sa.Column('power_watts', sa.Float(), nullable=True),
        sa.Column('energy_kwh', sa.Float(), nullable=True),
        sa.Column('frequency', sa.Float(), nullable=True),
        sa.Column('power_factor', sa.Float(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=False, index=True),
        sa.ForeignKeyConstraint(['outlet_id'], ['smart_outlets.id'], ondelete='CASCADE'),
        sa.CheckConstraint('socket_number IN (1, 2, 3)', name='ck_energy_socket_number'),
    )
    op.create_index(op.f('ix_energy_readings_outlet_socket_time'), 'energy_readings',
                    ['outlet_id', 'socket_number', 'recorded_at'])

    # ------------------------------------------------------------------
    # 4. Create wifi_credentials table
    # ------------------------------------------------------------------
    op.create_table(
        'wifi_credentials',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('outlet_id', sa.Integer(), unique=True, nullable=False),
        sa.Column('ssid', sa.String(length=255), nullable=False),
        sa.Column('password_encrypted', sa.String(length=512), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['outlet_id'], ['smart_outlets.id'], ondelete='CASCADE'),
    )


def downgrade():
    # Drop tables in reverse order
    op.drop_table('wifi_credentials')
    op.drop_table('energy_readings')
    op.drop_table('relay_commands')

    # Remove columns from smart_outlets
    with op.batch_alter_table('smart_outlets', schema=None) as batch_op:
        batch_op.drop_column('mesh_id')
        batch_op.drop_column('mesh_gateway')
        batch_op.drop_column('wifi_ssid')
        batch_op.drop_column('relay_state_3')
        batch_op.drop_column('relay_state_2')
        batch_op.drop_column('relay_state_1')
        batch_op.drop_column('device_api_secret')
        batch_op.drop_column('socket_count')
        batch_op.drop_column('model')
        batch_op.drop_column('network_esp32_id')
        batch_op.drop_column('hardware_esp32_id')
