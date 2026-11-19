"""JSON API: secure QR pairing, owner-scoped reads, heartbeats, and relay control.

Endpoints:
    POST /api/pairing/validate
    POST /api/pairing/register
    GET  /api/devices
    GET  /api/devices/<device_id>
    POST /api/device/heartbeat
    POST /api/device/<device_id>/relay        (owner issues relay command)
    GET  /api/device/commands                 (ESP32 polls for commands)
    POST /api/device/commands/<id>/ack        (ESP32 acknowledges command)
    POST /api/device/telemetry                (ESP32 sends energy readings)
    POST /api/device/<device_id>/wifi-setup   (owner configures Wi-Fi)

Browser pairing/read requests require an authenticated session and, for
mutations, a valid CSRF token in the X-CSRF-Token header. The heartbeat is
a machine-to-machine endpoint that requires the shared ``X-Device-Key``
header (see app/device_auth.py) and still refuses any device ID that has not
already been registered from the website.

Neither pairing codes nor the device API key are echoed in responses or logs.
"""

import time

from flask import Blueprint, jsonify, request, session
from flask_login import current_user, login_required

from app.device_auth import require_device_key
from app.services import device_service, qr_service
from app.services.device_service import PairingError
from app.services.relay_service import RelayError, issue_relay_command, get_pending_commands, acknowledge_command, serialize_command
from app.services import energy_service
from app.services.energy_service import EnergyError
from app.services import wifi_service
from app.services.wifi_service import WifiError

api_bp = Blueprint("api", __name__)

ATTEMPT_WINDOW_SECONDS = 600
MAX_PAIRING_FAILURES = 12
ATTEMPTS_KEY = "pairing_failures"
MAX_PAYLOAD_LENGTH = 512
MAX_FIELD_LENGTH = 128


def _error(error, status=None):
    return (
        jsonify(ok=False, error=error.code, message=error.message),
        status or error.status,
    )


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def _credentials_from(data):
    """Pull payload or manual fields from a JSON object."""

    if data is None:
        raise PairingError(
            "Send a JSON body with a QR payload or device credentials.",
            status=400,
            code="invalid_request",
        )

    payload = data.get("payload")
    device_id = data.get("device_id")
    pairing_code = data.get("pairing_code")

    if payload is not None and not isinstance(payload, str):
        raise PairingError("The QR payload must be text.", status=400, code="invalid_request")
    if payload and len(payload) > MAX_PAYLOAD_LENGTH:
        raise PairingError("That QR payload is too long.", status=400, code="invalid_request")
    if device_id is not None and not isinstance(device_id, str):
        raise PairingError("The device ID must be text.", status=400, code="invalid_request")
    if pairing_code is not None and not isinstance(pairing_code, str):
        raise PairingError("The pairing code must be text.", status=400, code="invalid_request")
    if device_id and len(device_id) > MAX_FIELD_LENGTH:
        raise PairingError("That device ID is too long.", status=400, code="invalid_request")
    if pairing_code and len(pairing_code) > MAX_FIELD_LENGTH:
        raise PairingError("That pairing code is too long.", status=400, code="invalid_request")

    return {
        "payload": payload.strip() if payload else None,
        "device_id": device_id.strip() if device_id else None,
        "pairing_code": pairing_code.strip() if pairing_code else None,
    }


def _recent_failures():
    now = time.time()
    attempts = [
        stamp
        for stamp in session.get(ATTEMPTS_KEY, [])
        if isinstance(stamp, (int, float)) and now - stamp < ATTEMPT_WINDOW_SECONDS
    ]
    session[ATTEMPTS_KEY] = attempts
    return attempts


def _record_failure():
    session[ATTEMPTS_KEY] = _recent_failures() + [time.time()]


def _clear_failures():
    session.pop(ATTEMPTS_KEY, None)


def _throttled():
    if len(_recent_failures()) >= MAX_PAIRING_FAILURES:
        return (
            jsonify(
                ok=False,
                error="too_many_attempts",
                message="Too many pairing attempts. Wait ten minutes and try again.",
            ),
            429,
        )
    return None


@api_bp.after_request
def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@api_bp.post("/pairing/validate")
@login_required
def validate_pairing_code():
    """Validate a QR payload without claiming the device."""

    throttled = _throttled()
    if throttled:
        return throttled

    try:
        credentials = _credentials_from(_json_body())
        outlet = device_service.validate_pairing(**credentials)
    except PairingError as error:
        _record_failure()
        return _error(error)

    _clear_failures()
    return jsonify(
        ok=True,
        can_register=True,
        device=device_service.serialize_outlet(outlet),
    )


@api_bp.post("/pairing/register")
@login_required
def register_outlet():
    """Claim the outlet for the signed-in account (single-use code)."""

    throttled = _throttled()
    if throttled:
        return throttled

    try:
        credentials = _credentials_from(_json_body())
        outlet = device_service.register_outlet(current_user, **credentials)
    except PairingError as error:
        _record_failure()
        return _error(error)

    _clear_failures()
    return (
        jsonify(
            ok=True,
            message="Smart outlet registered.",
            device=device_service.serialize_outlet(outlet),
        ),
        201,
    )


@api_bp.post("/device/heartbeat")
@require_device_key
def device_heartbeat():
    """Update a registered outlet from an authenticated ESP32 heartbeat.

    Hardware has no browser session, so this endpoint uses the shared
    ``X-Device-Key`` header instead of login/CSRF (exempted in
    app/security.py). The service layer still only updates devices that
    already exist and are registered; the key never bypasses ownership.
    """

    try:
        outlet = device_service.process_heartbeat(_json_body())
    except PairingError as error:
        return _error(error)

    return jsonify(
        ok=True,
        message="Heartbeat accepted.",
        device=device_service.serialize_outlet(outlet),
    )


@api_bp.get("/devices")
@login_required
def list_devices():
    outlets = device_service.get_user_outlets(current_user.id)
    return jsonify(
        ok=True,
        devices=[device_service.serialize_outlet(outlet) for outlet in outlets],
    )


@api_bp.get("/devices/<device_id>")
@login_required
def get_device(device_id):
    outlet = device_service.get_user_outlet(current_user.id, device_id)
    if outlet is None:
        # 404 for both "does not exist" and "belongs to someone else" so
        # ownership is not disclosed.
        return (
            jsonify(
                ok=False,
                error="not_found",
                message="That outlet is not registered to your account.",
            ),
            404,
        )
    return jsonify(ok=True, device=device_service.serialize_outlet(outlet))


# ------------------------------------------------------------------
# Relay control
# ------------------------------------------------------------------


@api_bp.post("/device/<device_id>/relay")
@login_required
def relay_control(device_id):
    """Issue a relay command for a specific socket on an owned outlet."""

    outlet = device_service.get_user_outlet(current_user.id, device_id)
    if outlet is None:
        return (
            jsonify(
                ok=False,
                error="not_found",
                message="That outlet is not registered to your account.",
            ),
            404,
        )

    data = _json_body()
    if not data:
        return _error(PairingError("Send a JSON body.", status=400, code="invalid_request"))

    socket_number = data.get("socket_number")
    desired_state = data.get("desired_state")

    if not isinstance(socket_number, int) or socket_number not in (1, 2, 3):
        return _error(RelayError("socket_number must be 1, 2, or 3.", status=400, code="invalid_socket"))

    if desired_state not in ("on", "off"):
        return _error(RelayError("desired_state must be 'on' or 'off'.", status=400, code="invalid_state"))

    try:
        cmd = issue_relay_command(outlet, socket_number, desired_state, user_id=current_user.id)
    except RelayError as error:
        return _error(error)

    return jsonify(
        ok=True,
        message=f"Relay command queued for socket {socket_number}.",
        command=serialize_command(cmd),
    )


@api_bp.get("/device/commands")
@require_device_key
def poll_commands():
    """ESP32 polls for pending relay commands."""

    data = request.args
    device_id = data.get("device_id", "")

    if not device_id:
        return _error(PairingError("device_id query parameter is required.", status=400, code="invalid_request"))

    try:
        normalized = qr_service.normalize_esp32_device_id(device_id)
    except qr_service.QRFormatError as error:
        return _error(PairingError(str(error), status=400, code="invalid_device_id"))

    outlet = device_service.get_outlet_by_device_id(normalized)
    if outlet is None or not outlet.is_registered:
        return _error(PairingError("Device not registered.", status=404, code="unknown_device"))

    commands = get_pending_commands(outlet.id)
    return jsonify(
        ok=True,
        commands=[serialize_command(cmd) for cmd in commands],
        count=len(commands),
    )


@api_bp.post("/device/commands/<command_id>/ack")
@require_device_key
def ack_command(command_id):
    """ESP32 acknowledges a relay command after execution."""

    data = _json_body()
    if not data:
        return _error(PairingError("Send a JSON body with device_id.", status=400, code="invalid_request"))

    device_id = data.get("device_id", "")
    if not device_id:
        return _error(PairingError("device_id is required.", status=400, code="invalid_request"))

    try:
        normalized = qr_service.normalize_esp32_device_id(device_id)
    except qr_service.QRFormatError as error:
        return _error(PairingError(str(error), status=400, code="invalid_device_id"))

    outlet = device_service.get_outlet_by_device_id(normalized)
    if outlet is None or not outlet.is_registered:
        return _error(PairingError("Device not registered.", status=404, code="unknown_device"))

    socket_state = data.get("socket_state")
    if socket_state is not None and socket_state not in ("on", "off"):
        return _error(PairingError("socket_state must be 'on' or 'off'.", status=400, code="invalid_request"))

    try:
        cmd = acknowledge_command(command_id, outlet.id, socket_state=socket_state)
    except RelayError as error:
        return _error(error)

    return jsonify(
        ok=True,
        message="Command acknowledged.",
        command=serialize_command(cmd),
    )


# ------------------------------------------------------------------
# Telemetry
# ------------------------------------------------------------------


@api_bp.post("/device/telemetry")
@require_device_key
def device_telemetry():
    """ESP32 sends energy readings from PZEM-004T sensors."""

    data = _json_body()
    if not data:
        return _error(PairingError("Send a JSON body with device_id.", status=400, code="invalid_request"))

    device_id = data.get("device_id", "")
    if not device_id:
        return _error(PairingError("device_id is required.", status=400, code="invalid_request"))

    try:
        normalized = qr_service.normalize_esp32_device_id(device_id)
    except qr_service.QRFormatError as error:
        return _error(PairingError(str(error), status=400, code="invalid_device_id"))

    outlet = device_service.get_outlet_by_device_id(normalized)
    if outlet is None or not outlet.is_registered:
        return _error(PairingError("Device not registered.", status=404, code="unknown_device"))

    socket_number = data.get("socket_number", 1)
    if not isinstance(socket_number, int) or socket_number not in (1, 2, 3):
        return _error(EnergyError("socket_number must be 1, 2, or 3.", status=400, code="invalid_socket"))

    # Extract telemetry data (all optional)
    telemetry_data = {}
    for key in ("voltage", "current", "power_watts", "energy_kwh", "frequency", "power_factor"):
        if key in data:
            telemetry_data[key] = data[key]

    if not telemetry_data:
        return _error(EnergyError("At least one measurement field is required.", status=400, code="invalid_request"))

    try:
        reading = energy_service.ingest_telemetry(outlet.id, socket_number, telemetry_data)
    except EnergyError as error:
        return _error(error)

    return jsonify(
        ok=True,
        message="Telemetry received.",
        reading=energy_service.serialize_reading(reading),
    )


@api_bp.get("/monitoring/readings")
@login_required
def monitoring_readings():
    """Dashboard fetches energy readings for charts."""

    device_id = request.args.get("device_id", "")
    if not device_id:
        return _error(PairingError("device_id query parameter is required.", status=400, code="invalid_request"))

    outlet = device_service.get_user_outlet(current_user.id, device_id)
    if outlet is None:
        return (
            jsonify(
                ok=False,
                error="not_found",
                message="That outlet is not registered to your account.",
            ),
            404,
        )

    socket_number = request.args.get("socket_number", 1, type=int)
    hours = request.args.get("hours", 24, type=int)
    interval = request.args.get("interval", 5, type=int)

    if socket_number not in (1, 2, 3):
        socket_number = 1

    chart_data = energy_service.get_readings_for_chart(
        outlet.id, socket_number, hours=hours, interval_minutes=interval
    )

    # Also get the latest reading for live display
    latest = energy_service.get_latest_readings(outlet.id, socket_number, limit=1)
    latest_reading = energy_service.serialize_reading(latest[0]) if latest else None

    return jsonify(
        ok=True,
        chart=chart_data,
        latest=latest_reading,
        socket_number=socket_number,
    )


# ------------------------------------------------------------------
# Wi-Fi setup
# ------------------------------------------------------------------


@api_bp.post("/device/<device_id>/wifi-setup")
@login_required
def wifi_setup(device_id):
    """Customer configures Wi-Fi credentials for their owned outlet."""

    outlet = device_service.get_user_outlet(current_user.id, device_id)
    if outlet is None:
        return (
            jsonify(
                ok=False,
                error="not_found",
                message="That outlet is not registered to your account.",
            ),
            404,
        )

    data = _json_body()
    if not data:
        return _error(PairingError("Send a JSON body.", status=400, code="invalid_request"))

    ssid = data.get("ssid", "")
    password = data.get("password", "")

    try:
        credential = wifi_service.setup_wifi(outlet.id, ssid, password)
    except WifiError as error:
        return _error(error)

    # Update the outlet's wifi_ssid field for quick reference
    outlet.wifi_ssid = credential.ssid
    from app.extensions import db as db_ext
    db_ext.session.commit()

    return jsonify(
        ok=True,
        message="Wi-Fi credentials saved.",
        wifi=wifi_service.serialize_wifi_status(credential),
    )
