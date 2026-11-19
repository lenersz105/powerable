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

    # --- Dual-board ESP32 identity (added for online system) ---
    # Permanent Hardware ESP32 identifier (e.g. PA-ESP32-A8F291C42D10).
    hardware_esp32_id = db.Column(db.String(64), nullable=True)
    # Permanent Network ESP32 identifier (e.g. PA-NET-12345678).
    network_esp32_id = db.Column(db.String(64), nullable=True)
    # Product model identifier.
    model = db.Column(db.String(64), nullable=True)
    # Number of independent sockets (default 3).
    socket_count = db.Column(db.Integer, nullable=True, default=3)
    # Per-device authentication secret hash (for per-device auth).
    device_api_secret = db.Column(db.String(256), nullable=True)

    # --- 3-socket relay states (relay_state preserved for backward compat) ---
    relay_state_1 = db.Column(db.String(10), nullable=False, default="unknown")
    relay_state_2 = db.Column(db.String(10), nullable=False, default="unknown")
    relay_state_3 = db.Column(db.String(10), nullable=False, default="unknown")

    # --- Wi-Fi configuration ---
    wifi_ssid = db.Column(db.String(255), nullable=True)

    # --- Mesh support ---
    mesh_gateway = db.Column(db.Boolean, nullable=False, default=False)
    mesh_id = db.Column(db.String(64), nullable=True)

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
    relay_commands = db.relationship(
        "RelayCommand",
        back_populates="outlet",
        cascade="all, delete-orphan",
        order_by="RelayCommand.created_at.desc()",
    )
    energy_readings = db.relationship(
        "EnergyReading",
        back_populates="outlet",
        cascade="all, delete-orphan",
        order_by="EnergyReading.recorded_at.desc()",
    )
    wifi_credential = db.relationship(
        "WifiCredential",
        back_populates="outlet",
        uselist=False,
        cascade="all, delete-orphan",
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

    # ------------------------------------------------------------------
    # 3-socket relay helpers
    # ------------------------------------------------------------------

    @property
    def effective_socket_count(self):
        """Return the configured socket count, defaulting to 3."""
        return self.socket_count or 3

    def get_relay_state(self, socket_number):
        """Return the relay state for a given socket (1-based).

        Falls back to the legacy ``relay_state`` field when the per-socket
        state is still "unknown", so heartbeats that update only the legacy
        field continue to work during the transition period.
        """
        if socket_number == 1:
            state = self.relay_state_1
        elif socket_number == 2:
            state = self.relay_state_2
        elif socket_number == 3:
            state = self.relay_state_3
        else:
            return "unknown"

        # Fall back to legacy relay_state for socket 1 during transition
        if state == "unknown" and socket_number == 1 and self.relay_state in ("on", "off"):
            return self.relay_state
        return state

    def set_relay_state(self, socket_number, state):
        """Set the relay state for a given socket (1-based)."""
        if state not in ("on", "off", "unknown"):
            state = "unknown"
        if socket_number == 1:
            self.relay_state_1 = state
        elif socket_number == 2:
            self.relay_state_2 = state
        elif socket_number == 3:
            self.relay_state_3 = state

    @property
    def socket_states(self):
        """Return a list of relay states for all configured sockets."""
        return [
            self.get_relay_state(i) for i in range(1, self.effective_socket_count + 1)
        ]

    @property
    def all_sockets_online(self):
        """True when all sockets have a known relay state."""
        return all(s in ("on", "off") for s in self.socket_states)

    # ------------------------------------------------------------------
    # Canonical device identity
    # ------------------------------------------------------------------

    @property
    def canonical_device_id(self):
        """Return the Hardware ESP32 ID if set, otherwise fall back to device_uid.

        The Hardware ESP32 ID is the canonical cloud device identity per the
        online system spec.  Legacy devices that were provisioned before the
        dual-board fields existed will continue to use device_uid.
        """
        return self.hardware_esp32_id or self.device_uid

    def __repr__(self):
        # Never include pairing material in a repr that may reach logs.
        return f"<SmartOutlet {self.device_uid}>"
