"""Smart outlet provisioning, pairing validation, and registration.

Rules enforced here (not only in JavaScript):
* the QR payload must be structurally valid;
* the device must exist and be unclaimed;
* the pairing code must match its stored hash;
* the code is consumed once at registration;
* a user can only read outlets they own.

Nothing in this module ever creates sensor readings or marks a device
online. Connection status stays "unknown" until real hardware telemetry
is received by a future stage.
"""

import hashlib
import ipaddress
import secrets

from flask import current_app
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.extensions import db
from app.models import DeviceEvent, SmartOutlet, User
from app.models.smart_outlet import CONNECTION_STATES
from app.services import qr_service
from app.utils import to_iso, utcnow


class PairingError(Exception):
    """A safe, user-presentable pairing failure.

    ``message`` never contains pairing codes, token hashes, or account
    details belonging to another user.
    """

    def __init__(self, message, status=400, code="pairing_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class DeviceError(PairingError):
    """A registration or heartbeat failure with a safe public message."""


def serialize_outlet(outlet):
    """Return only non-secret outlet fields for JSON responses."""

    # Build per-socket relay states
    socket_count = outlet.effective_socket_count
    sockets = {}
    for i in range(1, socket_count + 1):
        state = outlet.get_relay_state(i)
        sockets[i] = {
            "relay_state": state,
            "is_on": state == "on",
            "is_off": state == "off",
            "is_unknown": state == "unknown",
        }

    return {
        "device_id": outlet.device_uid,
        "device_type": outlet.device_type,
        "name": outlet.display_name,
        "channel": outlet.channel,
        "registration_status": outlet.pairing_label,
        "pairing_state": outlet.pairing_state,
        "registered": outlet.is_registered,
        "connection_status": (
            "online"
            if outlet.is_online
            else (
                "unknown"
                if not outlet.is_registered or outlet.last_seen_at is None
                else "offline"
            )
        ),
        "connection_label": outlet.connection_label,
        "online": outlet.is_online,
        "ip_address": outlet.ip_address,
        "hardware_connected": outlet.hardware_connected,
        "hardware_label": outlet.hardware_label,
        "hardware_status": {
            True: "CONNECTED",
            False: "DISCONNECTED",
            None: "UNKNOWN",
        }[outlet.hardware_connected],
        "network_mode": outlet.network_mode,
        "network_mode_label": outlet.network_mode_label,
        "connection_type": outlet.connection_type,
        "connection_type_label": outlet.connection_type_label,
        # Legacy single relay state (backward compat)
        "relay_state": outlet.relay_state,
        "outlet_state": (
            outlet.relay_state == "on"
            if outlet.relay_state in ("on", "off")
            else None
        ),
        # 3-socket support
        "socket_count": socket_count,
        "sockets": sockets,
        # Dual-board ESP32 identity
        "hardware_esp32_id": outlet.hardware_esp32_id,
        "network_esp32_id": outlet.network_esp32_id,
        "model": outlet.model,
        # Wi-Fi status
        "wifi_ssid": outlet.wifi_ssid,
        # Mesh
        "mesh_gateway": outlet.mesh_gateway,
        "mesh_id": outlet.mesh_id,
        "last_seen_at": to_iso(outlet.last_seen_at),
        "last_seen_label": outlet.last_seen_label,
        "registered_at": to_iso(outlet.registered_at),
        "created_at": to_iso(outlet.created_at),
        # Measurements are intentionally null: no hardware reading exists.
        "readings": None,
    }


def _generate_device_secret():
    """Generate a cryptographically random per-device API secret.

    Returns a 32-byte URL-safe token suitable for provisioning into the
    Network ESP32 firmware.  The plaintext is returned so it can be stored
    in the device; only its hash is kept in the database.
    """
    return secrets.token_urlsafe(32)


def _hash_device_secret(plaintext_secret):
    """Hash a per-device secret for storage.

    Uses SHA-256 — the same algorithm as device_auth.py::_hash_secret —
    so the auth layer can verify the stored hash.
    """
    return hashlib.sha256(plaintext_secret.encode("utf-8")).hexdigest()


def provision_device(
    device_uid,
    device_type=None,
    hardware_esp32_id=None,
    network_esp32_id=None,
    model=None,
    socket_count=None,
):
    """Create or rotate a pairing code for a physical outlet.

    This is an installer/admin action used by the Flask CLI and the admin
    console.  It returns ``(outlet, plaintext_code, payload, device_secret)``.
    The plaintext code and device secret are shown once so they can be
    printed into a label / flashed into the Network ESP32; only their
    hashes are stored.

    Args:
        device_uid: The canonical device UID.
        device_type: Human-readable type label.
        hardware_esp32_id: Permanent Hardware ESP32 identifier.
        network_esp32_id: Permanent Network ESP32 identifier.
        model: Product model identifier.
        socket_count: Number of independent sockets (default 3).
    """

    device_id = qr_service.normalize_device_id(device_uid)
    outlet = db.session.scalar(
        db.select(SmartOutlet).filter_by(device_uid=device_id)
    )

    if outlet is not None and outlet.is_registered:
        raise PairingError(
            "That outlet is already registered. Revoke it before issuing a new label.",
            status=409,
            code="already_registered",
        )

    code = qr_service.generate_pairing_code()
    device_secret = _generate_device_secret()

    if outlet is None:
        outlet = SmartOutlet(device_uid=device_id, pairing_state="available")
        db.session.add(outlet)
    else:
        outlet.owner_id = None
        outlet.channel = None
        outlet.registered_at = None
        outlet.pairing_state = "available"

    if device_type:
        outlet.device_type = device_type.strip()[:64]

    # Dual-board ESP32 identity
    if hardware_esp32_id:
        outlet.hardware_esp32_id = hardware_esp32_id.strip()[:64]
    if network_esp32_id:
        outlet.network_esp32_id = network_esp32_id.strip()[:64]

    # Product information
    if model:
        outlet.model = model.strip()[:64]
    if socket_count is not None:
        try:
            socket_count = int(socket_count)
            if 1 <= socket_count <= 8:
                outlet.socket_count = socket_count
            else:
                outlet.socket_count = 3
        except (ValueError, TypeError):
            outlet.socket_count = 3
    elif outlet.socket_count is None:
        outlet.socket_count = 3

    # Per-device authentication secret (hashed)
    outlet.device_api_secret = _hash_device_secret(device_secret)

    outlet.pairing_token_hash = qr_service.hash_pairing_code(code)
    outlet.connection_status = "unknown"
    outlet.last_seen_at = None
    outlet.relay_state = "unknown"
    outlet.ip_address = None
    outlet.hardware_connected = None
    outlet.network_mode = None
    outlet.connection_type = None

    db.session.flush()
    db.session.add(
        DeviceEvent(
            outlet_id=outlet.id,
            event_type="provisioned",
            description="Pairing label generated by the installer.",
        )
    )
    db.session.commit()

    return outlet, code, qr_service.build_payload(device_id, code), device_secret


def rotate_device_secret(outlet):
    """Rotate the per-device API secret for an already-provisioned outlet.

    Generates a new cryptographically secure secret, stores only its SHA-256
    hash, and returns the plaintext once.  Preserves the existing Device ID,
    customer ownership, pairing state, and all other outlet fields.

    Args:
        outlet: The SmartOutlet instance to rotate.

    Returns:
        The plaintext device secret string (shown once to the admin).

    Raises:
        PairingError: If the outlet does not exist.
    """

    if outlet is None:
        raise PairingError("That outlet no longer exists.", status=404, code="not_found")

    new_secret = _generate_device_secret()
    outlet.device_api_secret = _hash_device_secret(new_secret)
    outlet.updated_at = utcnow()

    db.session.flush()
    db.session.add(
        DeviceEvent(
            outlet_id=outlet.id,
            event_type="secret_rotated",
            description="Device API secret rotated by an administrator.",
        )
    )
    db.session.commit()

    return new_secret


def get_outlet_by_device_id(device_id):
    try:
        normalized = qr_service.normalize_device_id(device_id)
    except qr_service.QRFormatError:
        return None
    return db.session.scalar(
        db.select(SmartOutlet).filter_by(device_uid=normalized)
    )


def _resolve_payload(payload=None, device_id=None, pairing_code=None):
    if payload:
        return qr_service.parse_payload(payload)
    if device_id and pairing_code:
        return qr_service.PairingPayload(
            device_id=qr_service.normalize_device_id(device_id),
            pairing_code=qr_service.normalize_pairing_code(pairing_code),
        )
    raise PairingError(
        "Scan a PowerAble QR code or enter the device ID and pairing code.",
        status=400,
        code="missing_credentials",
    )


def validate_pairing(payload=None, device_id=None, pairing_code=None):
    """Check a scanned/entered code without claiming the device.

    Returns the unclaimed SmartOutlet. Raises PairingError otherwise.
    """

    try:
        parsed = _resolve_payload(payload, device_id, pairing_code)
    except qr_service.QRFormatError as error:
        raise PairingError(str(error), status=400, code="invalid_qr") from error

    outlet = db.session.scalar(
        db.select(SmartOutlet).filter_by(device_uid=parsed.device_id)
    )

    if outlet is None:
        raise PairingError(
            "This QR code does not match a PowerAble outlet.",
            status=404,
            code="unknown_device",
        )

    if outlet.pairing_state == "revoked":
        raise PairingError(
            "This outlet has been disabled. Contact the installer.",
            status=403,
            code="revoked",
        )

    if outlet.owner_id is not None or outlet.pairing_state == "registered":
        raise PairingError(
            "This outlet is already registered to an account.",
            status=409,
            code="already_registered",
        )

    if outlet.pairing_state != "available" or not outlet.pairing_token_hash:
        raise PairingError(
            "This outlet is not available for pairing.",
            status=409,
            code="not_available",
        )

    if not qr_service.verify_pairing_code(
        outlet.pairing_token_hash, parsed.pairing_code
    ):
        raise PairingError(
            "The pairing code is not valid for this outlet.",
            status=400,
            code="invalid_code",
        )

    return outlet


def _next_channel(user_id):
    used = set(
        db.session.scalars(
            db.select(SmartOutlet.channel).filter(
                SmartOutlet.owner_id == user_id,
                SmartOutlet.channel.isnot(None),
            )
        )
    )
    channel = 1
    while channel in used:
        channel += 1
    return channel


def register_outlet(user, payload=None, device_id=None, pairing_code=None):
    """Claim a validated outlet for ``user`` inside a transaction."""

    if not isinstance(user, User) or not user.is_authenticated:
        raise PairingError("Sign in to register an outlet.", status=401, code="auth")

    outlet = validate_pairing(payload, device_id, pairing_code)

    limit = int(current_app.config.get("MAX_OUTLETS_PER_USER", 16))
    owned_count = db.session.scalar(
        db.select(db.func.count(SmartOutlet.id)).filter_by(owner_id=user.id)
    )
    if owned_count >= limit:
        raise PairingError(
            f"Your account already has the maximum of {limit} outlets.",
            status=409,
            code="limit_reached",
        )

    channel = _next_channel(user.id)
    now = utcnow()

    # Conditional update prevents two simultaneous requests from claiming
    # the same device. Only one can match owner_id IS NULL.
    result = (
        db.session.query(SmartOutlet)
        .filter(
            SmartOutlet.id == outlet.id,
            SmartOutlet.owner_id.is_(None),
            SmartOutlet.pairing_state == "available",
            SmartOutlet.pairing_token_hash.isnot(None),
        )
        .update(
            {
                SmartOutlet.owner_id: user.id,
                SmartOutlet.outlet_name: f"Outlet {channel}",
                SmartOutlet.channel: channel,
                SmartOutlet.pairing_state: "registered",
                SmartOutlet.pairing_token_hash: None,
                SmartOutlet.registered_at: now,
                SmartOutlet.connection_status: "unknown",
                SmartOutlet.last_seen_at: None,
                SmartOutlet.relay_state: "unknown",
                SmartOutlet.ip_address: None,
                SmartOutlet.hardware_connected: None,
                SmartOutlet.network_mode: None,
                SmartOutlet.connection_type: None,
                SmartOutlet.updated_at: now,
            },
            synchronize_session=False,
        )
    )

    if result != 1:
        db.session.rollback()
        raise PairingError(
            "This outlet was just claimed by another account. Refresh and try again.",
            status=409,
            code="already_registered",
        )

    db.session.add(
        DeviceEvent(
            outlet_id=outlet.id,
            event_type="registered",
            description="Outlet registered to a PowerAble account.",
        )
    )
    db.session.commit()

    return db.session.get(SmartOutlet, outlet.id)


def next_outlet_name(channel):
    """Name a self-registered outlet ``SmartOutlet N`` for its channel."""

    return f"SmartOutlet {channel}"


def register_outlet_by_device_id(user, device_id):
    """Register a factory Device ID for ``user`` without a pairing code.

    Returns ``(outlet, created)``. An outlet already owned by the same
    account is returned with ``created=False`` so callers can show an
    honest "already registered" message instead of creating a duplicate.
    """

    if not isinstance(user, User) or not user.is_authenticated:
        raise DeviceError("Sign in to register an outlet.", status=401, code="auth")

    try:
        normalized = qr_service.normalize_esp32_device_id(device_id)
    except qr_service.QRFormatError as error:
        raise DeviceError(
            str(error), status=400, code="invalid_device_id"
        ) from error

    existing = db.session.scalar(
        db.select(SmartOutlet).filter_by(device_uid=normalized)
    )
    if existing is not None:
        if existing.owner_id == user.id and existing.is_registered:
            return existing, False
        if existing.owner_id is not None:
            raise DeviceError(
                "This device ID is already registered to another account.",
                status=409,
                code="already_registered",
            )
        raise DeviceError(
            "This device ID has already been provisioned. Use the pairing "
            "code flow to claim it.",
            status=409,
            code="already_provisioned",
        )

    limit = int(current_app.config.get("MAX_OUTLETS_PER_USER", 16))
    owned_count = db.session.scalar(
        db.select(db.func.count(SmartOutlet.id)).filter_by(owner_id=user.id)
    )
    if owned_count >= limit:
        raise DeviceError(
            f"Your account already has the maximum of {limit} outlets.",
            status=409,
            code="limit_reached",
        )

    channel = _next_channel(user.id)
    now = utcnow()
    outlet = SmartOutlet(
        device_uid=normalized,
        device_type="Smart Power Outlet",
        owner_id=user.id,
        outlet_name=next_outlet_name(channel),
        channel=channel,
        pairing_state="registered",
        pairing_token_hash=None,
        registered_at=now,
        connection_status="unknown",
        last_seen_at=None,
        relay_state="unknown",
        ip_address=None,
        hardware_connected=None,
        network_mode=None,
        connection_type=None,
    )
    db.session.add(outlet)

    try:
        db.session.flush()
        db.session.add(
            DeviceEvent(
                outlet_id=outlet.id,
                event_type="registered",
                description="Smart outlet self-registered from a Device ID.",
            )
        )
        db.session.commit()
    except IntegrityError as error:
        # A simultaneous request won the unique device ID or channel race.
        db.session.rollback()
        existing = db.session.scalar(
            db.select(SmartOutlet).filter_by(device_uid=normalized)
        )
        if existing is not None and existing.owner_id == user.id:
            return existing, False
        raise DeviceError(
            "This device ID was just registered. Refresh and try again.",
            status=409,
            code="already_registered",
        ) from error
    except SQLAlchemyError as error:
        db.session.rollback()
        raise DeviceError(
            "The outlet could not be saved. Check the server log and try again.",
            status=503,
            code="database_error",
        ) from error

    return db.session.get(SmartOutlet, outlet.id), True


def _clean_heartbeat_ip(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise DeviceError(
            "ip_address must be text.", status=400, code="invalid_request"
        )
    value = value.strip()
    if not value:
        return None
    if len(value) > 45:
        raise DeviceError(
            "ip_address is too long.", status=400, code="invalid_request"
        )
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise DeviceError(
            "ip_address is not a valid IPv4 or IPv6 address.",
            status=400,
            code="invalid_request",
        ) from error


_UNSET = object()

NETWORK_MODES = ("online", "offline")
CONNECTION_TYPES = ("router", "access_point")
HARDWARE_STATUSES = {
    "CONNECTED": True,
    "DISCONNECTED": False,
    "UNKNOWN": None,
}
RELAY_STATES = {"ON": "on", "OFF": "off", "UNKNOWN": "unknown"}


def _clean_heartbeat_choice(value, field, allowed):
    """Validate one of the fixed heartbeat enums, or return ``_UNSET``."""

    if value is None:
        return _UNSET
    if not isinstance(value, str):
        raise DeviceError(
            f"{field} must be text.", status=400, code="invalid_request"
        )
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return _UNSET
    if normalized not in allowed:
        choices = ", ".join(allowed)
        raise DeviceError(
            f"{field} must be one of: {choices}.",
            status=400,
            code="invalid_request",
        )
    return normalized


def _parse_hardware_state(payload):
    """Read ``hardware_status`` (new) or ``hardware_connected`` (legacy)."""

    status = payload.get("hardware_status")
    legacy = payload.get("hardware_connected")

    if status is not None:
        if not isinstance(status, str):
            raise DeviceError(
                "hardware_status must be text.",
                status=400,
                code="invalid_request",
            )
        key = status.strip().upper()
        if key not in HARDWARE_STATUSES:
            raise DeviceError(
                "hardware_status must be CONNECTED, DISCONNECTED, or UNKNOWN.",
                status=400,
                code="invalid_request",
            )
        value = HARDWARE_STATUSES[key]
        if legacy is not None:
            if not isinstance(legacy, bool):
                raise DeviceError(
                    "hardware_connected must be true or false.",
                    status=400,
                    code="invalid_request",
                )
            if legacy is not value:
                raise DeviceError(
                    "hardware_status conflicts with hardware_connected.",
                    status=400,
                    code="invalid_request",
                )
        return value

    if legacy is not None:
        if not isinstance(legacy, bool):
            raise DeviceError(
                "hardware_connected must be true or false.",
                status=400,
                code="invalid_request",
            )
        return legacy

    return _UNSET


def _parse_relay_state(payload):
    """Read ``relay_state`` (new) or ``outlet_state`` (legacy)."""

    relay_state = payload.get("relay_state")
    legacy = payload.get("outlet_state")

    if relay_state is not None:
        if not isinstance(relay_state, str):
            raise DeviceError(
                "relay_state must be text.",
                status=400,
                code="invalid_request",
            )
        key = relay_state.strip().upper()
        if key not in RELAY_STATES:
            raise DeviceError(
                "relay_state must be ON, OFF, or UNKNOWN.",
                status=400,
                code="invalid_request",
            )
        value = RELAY_STATES[key]
        if legacy is not None:
            if not isinstance(legacy, bool):
                raise DeviceError(
                    "outlet_state must be true or false.",
                    status=400,
                    code="invalid_request",
                )
            expected = "on" if legacy else "off"
            if value != expected:
                raise DeviceError(
                    "relay_state conflicts with outlet_state.",
                    status=400,
                    code="invalid_request",
                )
        return value

    if legacy is not None:
        if not isinstance(legacy, bool):
            raise DeviceError(
                "outlet_state must be true or false.",
                status=400,
                code="invalid_request",
            )
        return "on" if legacy else "off"

    return _UNSET


def _parse_relay_states(payload):
    """Parse per-socket relay states from the heartbeat payload.

    Supports two formats:
    1. ``relay_states`` dict: ``{"1": "on", "2": "off", "3": "off"}``
    2. Individual fields: ``relay_state_1``, ``relay_state_2``, ``relay_state_3``

    Returns a dict of ``{socket_number: state}`` for sockets that changed.
    """
    states = {}

    # Format 1: relay_states dict
    relay_states_obj = payload.get("relay_states")
    if isinstance(relay_states_obj, dict):
        for key, value in relay_states_obj.items():
            try:
                socket_num = int(key)
            except (ValueError, TypeError):
                continue
            if socket_num not in (1, 2, 3):
                continue
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in ("on", "off", "unknown"):
                    states[socket_num] = normalized

    # Format 2: individual fields (relay_state_1, relay_state_2, relay_state_3)
    for socket_num in (1, 2, 3):
        field = f"relay_state_{socket_num}"
        value = payload.get(field)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("on", "off", "unknown"):
                states[socket_num] = normalized

    return states


def process_heartbeat(payload):
    """Apply a heartbeat for an already registered device.

    Accepts the ESP32 Network Handler payload::

        {
          "device_id": "PA-NET-12345678",
          "network_mode": "online",
          "connection_type": "router",
          "ip_address": "192.168.1.120",
          "hardware_status": "CONNECTED",
          "relay_state": "OFF",
          "relay_states": {"1": "on", "2": "off", "3": "off"}
        }

    The earlier fields ``outlet_state`` (bool) and ``hardware_connected``
    (bool) still work. Unknown devices are rejected; this function never
    creates a row and never changes an outlet owned by someone else. Only
    fields present in the payload are changed.

    Per-socket relay states are supported via ``relay_states`` dict or
    individual ``relay_state_1``, ``relay_state_2``, ``relay_state_3`` fields.
    """

    if not isinstance(payload, dict):
        raise DeviceError(
            "Send a JSON object with a device_id.",
            status=400,
            code="invalid_request",
        )

    raw_device_id = payload.get("device_id")
    if not isinstance(raw_device_id, str):
        raise DeviceError(
            "A device_id is required.", status=400, code="invalid_request"
        )
    try:
        normalized = qr_service.normalize_esp32_device_id(raw_device_id)
    except qr_service.QRFormatError as error:
        raise DeviceError(
            str(error), status=400, code="invalid_device_id"
        ) from error

    outlet = db.session.scalar(
        db.select(SmartOutlet).filter_by(device_uid=normalized)
    )
    if outlet is None:
        raise DeviceError(
            "This device is not registered. Register it from the PowerAble "
            "website first.",
            status=404,
            code="unknown_device",
        )
    if not outlet.is_registered:
        raise DeviceError(
            "This device is not registered to an account.",
            status=403,
            code="not_registered",
        )

    # Validate everything before mutating the row, so a bad payload cannot
    # leave a half-applied heartbeat in the session.
    ip_address = _clean_heartbeat_ip(payload.get("ip_address"))
    network_mode = _clean_heartbeat_choice(
        payload.get("network_mode"), "network_mode", NETWORK_MODES
    )
    connection_type = _clean_heartbeat_choice(
        payload.get("connection_type"), "connection_type", CONNECTION_TYPES
    )
    hardware_state = _parse_hardware_state(payload)
    relay_state = _parse_relay_state(payload)
    per_socket_states = _parse_relay_states(payload)

    if ip_address:
        outlet.ip_address = ip_address
    if network_mode is not _UNSET:
        outlet.network_mode = network_mode
    if connection_type is not _UNSET:
        outlet.connection_type = connection_type
    if hardware_state is not _UNSET:
        outlet.hardware_connected = hardware_state
    if relay_state is not _UNSET:
        outlet.relay_state = relay_state

    # Update per-socket relay states
    for socket_num, state in per_socket_states.items():
        outlet.set_relay_state(socket_num, state)
        # Also update legacy relay_state for socket 1 for backward compat
        if socket_num == 1 and state in ("on", "off"):
            outlet.relay_state = state

    now = utcnow()
    outlet.last_seen_at = now
    outlet.updated_at = now

    # Offline/access-point devices still communicate with the local server,
    # but the dashboard must not call them online.
    effective_mode = (
        outlet.network_mode if network_mode is _UNSET else network_mode
    )
    outlet.connection_status = (
        "offline" if effective_mode == "offline" else "online"
    )

    try:
        db.session.commit()
    except SQLAlchemyError as error:
        db.session.rollback()
        raise DeviceError(
            "The heartbeat could not be saved. Try again shortly.",
            status=503,
            code="database_error",
        ) from error

    return outlet


def get_user_outlets(user_id):
    return list(
        db.session.scalars(
            db.select(SmartOutlet)
            .filter_by(owner_id=user_id)
            .order_by(SmartOutlet.channel.is_(None), SmartOutlet.channel, SmartOutlet.id)
        )
    )


def get_user_outlet(user_id, device_id):
    try:
        normalized = qr_service.normalize_device_id(device_id)
    except qr_service.QRFormatError:
        return None
    return db.session.scalar(
        db.select(SmartOutlet).filter_by(
            owner_id=user_id, device_uid=normalized
        )
    )


def outlet_status_counts(outlets):
    on_count = sum(1 for outlet in outlets if outlet.relay_state == "on")
    reporting = sum(1 for outlet in outlets if outlet.is_online)
    return {"total": len(outlets), "on": on_count, "reporting": reporting}


def mark_device_online(device_id):
    """Deprecated shim: heartbeats now update online status directly."""

    outlet = get_outlet_by_device_id(device_id)
    if outlet is None:
        return None
    outlet.connection_status = "online"
    outlet.last_seen_at = utcnow()
    db.session.commit()
    return outlet


def is_valid_connection_state(value):
    return value in CONNECTION_STATES
