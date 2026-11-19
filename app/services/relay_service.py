"""Relay command service — queue, deliver, and acknowledge relay commands.

Responsibilities:
- Create relay commands with unique IDs and TTL
- Enforce ownership authorization
- Prevent duplicate commands for the same socket
- Expire stale commands
- Acknowledge commands from ESP32 hardware
- Update outlet relay state on confirmed commands
"""

import secrets

from datetime import timedelta

from flask import current_app
from sqlalchemy import or_

from app.extensions import db
from app.models import DeviceEvent, RelayCommand, SmartOutlet
from app.models.relay_command import COMMAND_STATUSES
from app.utils import utcnow


class RelayError(Exception):
    """A safe, user-presentable relay command failure."""

    def __init__(self, message, status=400, code="relay_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def _command_ttl():
    """Return the command TTL in seconds from config, defaulting to 120 s."""
    return int(current_app.config.get("RELAY_COMMAND_TTL_SECONDS", 120))


def _max_pending_per_outlet():
    """Return the max pending commands per outlet, defaulting to 10."""
    return int(current_app.config.get("MAX_PENDING_COMMANDS", 10))


def issue_relay_command(outlet, socket_number, desired_state, user_id=None):
    """Queue a relay command for an outlet socket.

    Args:
        outlet: The SmartOutlet instance.
        socket_number: 1, 2, or 3.
        desired_state: "on" or "off".
        user_id: The ID of the requesting user (for audit).

    Returns:
        The created RelayCommand instance.

    Raises:
        RelayError: If the outlet is offline, socket is invalid, or
            a pending command already exists for the same socket.
    """
    # Validate socket number
    if socket_number not in (1, 2, 3):
        raise RelayError(
            "Socket number must be 1, 2, or 3.",
            status=400,
            code="invalid_socket",
        )

    # Validate desired state
    if desired_state not in ("on", "off"):
        raise RelayError(
            "Desired state must be 'on' or 'off'.",
            status=400,
            code="invalid_state",
        )

    # Check outlet is online
    if not outlet.is_online:
        raise RelayError(
            "The outlet is not online. Commands can only be sent to online devices.",
            status=409,
            code="outlet_offline",
        )

    # Check for existing pending command for the same socket
    pending = db.session.scalar(
        db.select(RelayCommand).filter_by(
            outlet_id=outlet.id,
            socket_number=socket_number,
            status="pending",
        )
    )
    if pending is not None:
        raise RelayError(
            "A command for this socket is already pending. Wait for it to complete.",
            status=409,
            code="duplicate_command",
        )

    # Check max pending commands
    pending_count = db.session.scalar(
        db.select(db.func.count(RelayCommand.id)).filter_by(
            outlet_id=outlet.id,
            status="pending",
        )
    ) or 0
    if pending_count >= _max_pending_per_outlet():
        raise RelayError(
            "Too many pending commands for this outlet. Wait for some to complete.",
            status=429,
            code="too_many_commands",
        )

    # Create the command
    command_id = f"rc-{secrets.token_urlsafe(16)}"
    cmd = RelayCommand.create(
        outlet_id=outlet.id,
        socket_number=socket_number,
        desired_state=desired_state,
        requested_by=user_id,
        command_id=command_id,
        ttl_seconds=_command_ttl(),
    )

    # Log the event
    db.session.add(
        DeviceEvent(
            outlet_id=outlet.id,
            event_type="relay_command",
            description=f"Relay command queued: socket {socket_number} -> {desired_state}",
        )
    )

    db.session.commit()
    return cmd


def get_pending_commands(outlet_id):
    """Return all pending commands for an outlet (for ESP32 polling).

    Also expires any commands that have passed their TTL.
    """
    now = utcnow()

    # Expire stale commands
    stale = db.session.scalars(
        db.select(RelayCommand).filter_by(
            outlet_id=outlet_id,
            status="pending",
        ).filter(
            RelayCommand.expires_at < now
        )
    )
    for cmd in stale:
        cmd.expire()

    # Get pending commands
    commands = db.session.scalars(
        db.select(RelayCommand).filter_by(
            outlet_id=outlet_id,
            status="pending",
        ).order_by(RelayCommand.created_at)
    ).all()

    db.session.commit()
    return commands


def acknowledge_command(command_id, outlet_id, socket_state=None):
    """Acknowledge a relay command from ESP32 hardware.

    Args:
        command_id: The unique command ID.
        outlet_id: The outlet ID (for authorization).
        socket_state: Optional actual relay state after execution.

    Returns:
        The RelayCommand instance.

    Raises:
        RelayError: If the command is not found or already processed.
    """
    cmd = db.session.scalar(
        db.select(RelayCommand).filter_by(
            command_id=command_id,
            outlet_id=outlet_id,
        )
    )

    if cmd is None:
        raise RelayError(
            "Command not found.",
            status=404,
            code="command_not_found",
        )

    if cmd.is_terminal:
        raise RelayError(
            "Command has already been processed.",
            status=409,
            code="already_processed",
        )

    if cmd.is_expired:
        cmd.expire()
        db.session.commit()
        raise RelayError(
            "Command has expired.",
            status=410,
            code="command_expired",
        )

    # Acknowledge and complete
    cmd.acknowledge()
    cmd.complete()

    # Update the outlet's relay state if provided
    if socket_state and socket_state in ("on", "off"):
        outlet = db.session.get(SmartOutlet, outlet_id)
        if outlet:
            outlet.set_relay_state(cmd.socket_number, socket_state)
            outlet.relay_state = socket_state  # Backward compat
            outlet.updated_at = utcnow()

            # Log the state change
            db.session.add(
                DeviceEvent(
                    outlet_id=outlet_id,
                    event_type="relay_changed",
                    description=f"Socket {cmd.socket_number} changed to {socket_state} (command confirmed)",
                )
            )

    db.session.commit()
    return cmd


def expire_stale_commands(max_age_seconds=None):
    """Expire all pending commands that have passed their TTL.

    This can be called periodically to clean up stale commands.
    """
    if max_age_seconds is None:
        max_age_seconds = _command_ttl()

    now = utcnow()
    stale = db.session.scalars(
        db.select(RelayCommand).filter_by(status="pending").filter(
            RelayCommand.expires_at < now
        )
    )
    count = 0
    for cmd in stale:
        cmd.expire()
        count += 1

    if count:
        db.session.commit()

    return count


def serialize_command(cmd):
    """Return a non-secret dict for JSON responses."""
    return {
        "command_id": cmd.command_id,
        "outlet_id": cmd.outlet_id,
        "socket_number": cmd.socket_number,
        "desired_state": cmd.desired_state,
        "status": cmd.status,
        "acknowledged_at": cmd.acknowledged_at.isoformat() if cmd.acknowledged_at else None,
        "expires_at": cmd.expires_at.isoformat() if cmd.expires_at else None,
        "created_at": cmd.created_at.isoformat() if cmd.created_at else None,
    }
