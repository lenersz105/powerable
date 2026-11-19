"""RelayCommand model — queued relay commands with acknowledgment tracking.

Each command carries a unique ``command_id`` so the ESP32 can detect and
skip duplicates.  Commands expire after a configurable TTL so stale
entries do not accumulate.

Status lifecycle:
    pending → acknowledged → completed
    pending → expired
    pending → failed
"""

from datetime import timedelta

from flask import current_app, has_app_context

from app.extensions import db
from app.utils import utcnow


COMMAND_STATUSES = ("pending", "acknowledged", "completed", "expired", "failed")


def _default_ttl():
    """Return the command TTL in seconds from config, defaulting to 120 s."""
    if has_app_context():
        return int(current_app.config.get("RELAY_COMMAND_TTL_SECONDS", 120))
    return 120


class RelayCommand(db.Model):
    __tablename__ = "relay_commands"

    id = db.Column(db.Integer, primary_key=True)

    outlet_id = db.Column(
        db.Integer,
        db.ForeignKey("smart_outlets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    socket_number = db.Column(db.Integer, nullable=False)
    desired_state = db.Column(db.String(10), nullable=False)
    status = db.Column(
        db.String(20), nullable=False, default="pending", index=True
    )
    requested_by = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    command_id = db.Column(
        db.String(64), unique=True, nullable=False, index=True
    )
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    outlet = db.relationship("SmartOutlet", back_populates="relay_commands")

    __table_args__ = (
        db.CheckConstraint("socket_number IN (1, 2, 3)", name="ck_socket_number_range"),
        db.CheckConstraint("desired_state IN ('on', 'off')", name="ck_desired_state"),
        db.CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in COMMAND_STATUSES)})",
            name="ck_command_status",
        ),
    )

    @classmethod
    def create(
        cls,
        outlet_id,
        socket_number,
        desired_state,
        requested_by=None,
        command_id=None,
        ttl_seconds=None,
    ):
        """Create a new relay command and return it."""
        import secrets

        if command_id is None:
            command_id = f"rc-{secrets.token_urlsafe(16)}"
        if ttl_seconds is None:
            ttl_seconds = _default_ttl()
        expires_at = utcnow() + timedelta(seconds=ttl_seconds)

        cmd = cls(
            outlet_id=outlet_id,
            socket_number=socket_number,
            desired_state=desired_state,
            status="pending",
            requested_by=requested_by,
            command_id=command_id,
            expires_at=expires_at,
        )
        db.session.add(cmd)
        return cmd

    @property
    def is_expired(self):
        """True when the command has passed its expiration time."""
        return utcnow() > self.expires_at

    @property
    def is_terminal(self):
        """True when the command reached a final state."""
        return self.status in ("completed", "expired", "failed")

    def acknowledge(self):
        """Mark the command as acknowledged by the hardware."""
        self.status = "acknowledged"
        self.acknowledged_at = utcnow()

    def complete(self):
        """Mark the command as completed (hardware confirmed the state)."""
        self.status = "completed"
        self.acknowledged_at = self.acknowledged_at or utcnow()

    def fail(self):
        """Mark the command as failed."""
        self.status = "failed"

    def expire(self):
        """Mark the command as expired."""
        self.status = "expired"

    def __repr__(self):
        return f"<RelayCommand {self.command_id} socket={self.socket_number} {self.status}>"
