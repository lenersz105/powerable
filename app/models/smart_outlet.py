"""SmartOutlet model.

A SmartOutlet row exists as soon as a physical device is provisioned by the
installer. It becomes owned by a customer only after a valid QR pairing
code is confirmed by the Flask backend.

Security rules:
* ``pairing_token_hash`` stores a scrypt hash, never the code itself.
* Codes are single-use: registration clears the hash.
* A device is never marked online merely because it is registered.
"""

from datetime import datetime

from app.extensions import db
from app.utils import utcnow

PAIRING_STATES = (
    "unregistered",
    "available",
    "pending",
    "registered",
    "revoked",
)

CONNECTION_STATES = ("unknown", "online", "stale", "disconnected")


class SmartOutlet(db.Model):
    __tablename__ = "smart_outlets"

    id = db.Column(db.Integer, primary_key=True)

    # Permanent, unique identifier printed on the device label.
    device_uid = db.Column(db.String(64), unique=True, nullable=False, index=True)
    device_type = db.Column(
        db.String(64), nullable=False, default="Smart Power Outlet"
    )

    owner_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    outlet_name = db.Column(db.String(80), nullable=True)
    channel = db.Column(db.Integer, nullable=True)

    pairing_state = db.Column(
        db.String(20), nullable=False, default="unregistered", index=True
    )
    pairing_token_hash = db.Column(db.String(256), nullable=True)
    registered_at = db.Column(db.DateTime, nullable=True)

    connection_status = db.Column(
        db.String(20), nullable=False, default="unknown"
    )
    last_seen_at = db.Column(db.DateTime, nullable=True)
    relay_state = db.Column(db.String(10), nullable=False, default="unknown")

    # Network identity reported by the ESP32 network controller.
    ip_address = db.Column(db.String(45), nullable=True)
    # None means the hardware controller has not reported a state yet.
    hardware_connected = db.Column(db.Boolean, nullable=True)
    # "online" (joined a router) or "offline" (access-point fallback).
    network_mode = db.Column(db.String(16), nullable=True)
    # "router" or "access_point"; None until the device reports it.
    connection_type = db.Column(db.String(16), nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    owner = db.relationship("User", back_populates="outlets")
    events = db.relationship(
        "DeviceEvent",
        back_populates="outlet",
        cascade="all, delete-orphan",
        order_by="DeviceEvent.created_at.desc()",
    )

    __table_args__ = (
        db.UniqueConstraint("owner_id", "channel", name="uq_outlet_owner_channel"),
    )

    # ------------------------------------------------------------------
    # Display helpers (never invent measurements)
    # ------------------------------------------------------------------

    @property
    def display_name(self):
        return self.outlet_name or f"Outlet {self.channel or self.id}"

    @property
    def is_registered(self):
        return self.owner_id is not None and self.pairing_state == "registered"

    @property
    def relay_label(self):
        return {"on": "ON", "off": "OFF"}.get(self.relay_state, "Unknown")

    @property
    def heartbeat_timeout_seconds(self):
        """Seconds after the last heartbeat before a device is offline.

        Reads the application setting lazily so the model can be imported
        without an application context (the CLI imports models early).
        """

        from flask import current_app, has_app_context

        if not has_app_context():
            return 60
        try:
            return int(
                current_app.config.get("DEVICE_HEARTBEAT_TIMEOUT_SECONDS", 60)
            )
        except (TypeError, ValueError):
            return 60

    @property
    def is_online(self):
        """True only when a registered outlet has a fresh, online heartbeat."""

        if not self.is_registered or self.last_seen_at is None:
            return False
        # A device in access-point fallback mode reports offline even though
        # it just contacted the server, so the heartbeat timestamp alone is
        # not enough.
        if self.connection_status == "offline" or self.network_mode == "offline":
            return False
        age = (utcnow() - self.last_seen_at).total_seconds()
        return age <= self.heartbeat_timeout_seconds

    @property
    def last_seen_label(self):
        if self.last_seen_at is None:
            return "Never"
        return self.last_seen_at.strftime("%d %b %Y, %H:%M UTC")

    @property
    def ip_label(self):
        return self.ip_address or "—"

    @property
    def hardware_label(self):
        return {
            True: "Connected",
            False: "Not connected",
            None: "Unknown",
        }[self.hardware_connected]

    @property
    def hardware_tone(self):
        return {
            True: "online",
            False: "offline",
            None: "unknown",
        }[self.hardware_connected]

    @property
    def network_mode_label(self):
        return {
            "online": "Online",
            "offline": "Offline",
        }.get(self.network_mode, "—")

    @property
    def connection_type_label(self):
        return {
            "router": "Router",
            "access_point": "Access point",
        }.get(self.connection_type, "—")

    @property
    def connection_label(self):
        if not self.is_registered:
            return "Not registered"
        if self.last_seen_at is None:
            return "Waiting for first reading"
        return "Online" if self.is_online else "Offline"

    @property
    def connection_tone(self):
        if not self.is_registered or self.last_seen_at is None:
            return "waiting"
        return "online" if self.is_online else "offline"

    @property
    def pairing_label(self):
        return {
            "unregistered": "Not provisioned",
            "available": "Available",
            "pending": "Pending",
            "registered": "Registered",
            "revoked": "Revoked",
        }.get(self.pairing_state, self.pairing_state.title())

    def __repr__(self):
        # Never include pairing material in a repr that may reach logs.
        return f"<SmartOutlet {self.device_uid}>"
