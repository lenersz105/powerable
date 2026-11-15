"""QR pairing payloads, self-registration links, and QR rendering.

Two QR formats are supported:

* installer pairing labels containing a public device identifier, a
  single-use pairing code, and a format version;
* self-registration links that point a phone at ``/register?device_id=...``
  on this computer's LAN address.

Neither format contains Wi-Fi credentials, user data, or a permanent device
secret. Pairing codes are stored only as a scrypt hash.
"""

import base64
import io
import ipaddress
import re
import secrets
import socket
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import qrcode
from werkzeug.security import check_password_hash, generate_password_hash

QR_HEADER = "POWERABLE:PAIR"
QR_VERSION = "1"
MAX_PAYLOAD_LENGTH = 512

DEVICE_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,63}$")
# Hardware IDs accepted by the self-registration and heartbeat flows:
#   PA-ESP32-A8F291C42D10  (ESP32-S3 MAC based, 12 hex characters)
#   PA-NET-12345678        (network handler identifier, 8 hex characters)
HARDWARE_DEVICE_ID_PATTERN = re.compile(
    r"^(?:PA-ESP32-[0-9A-F]{12}|PA-NET-[0-9A-F]{8})$"
)
ESP32_DEVICE_ID_PATTERN = HARDWARE_DEVICE_ID_PATTERN
PAIRING_CODE_PATTERN = re.compile(r"^[A-Z2-7]{16,32}$")
REQUIRED_KEYS = {"VERSION", "DEVICE_ID", "PAIRING_CODE"}

REGISTER_PATH = "/register"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class QRFormatError(ValueError):
    """Raised when a scanned payload is not a valid PowerAble pairing QR."""


class QRNetworkError(RuntimeError):
    """Raised when no usable LAN address can be built for a QR link."""


@dataclass(frozen=True)
class PairingPayload:
    device_id: str
    pairing_code: str
    version: str = QR_VERSION


def generate_pairing_code(byte_length=15):
    """Return a cryptographically random base32 code.

    15 bytes = 120 bits of entropy, rendered as 24 uppercase characters
    from the RFC 4648 base32 alphabet (A-Z, 2-7).
    """

    raw = secrets.token_bytes(byte_length)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def normalize_device_id(value):
    """Uppercase and validate a device identifier."""

    if not isinstance(value, str):
        raise QRFormatError("A device ID is required.")
    device_id = value.strip().upper()
    if not DEVICE_ID_PATTERN.match(device_id):
        raise QRFormatError("That device ID format is not valid.")
    return device_id


def normalize_esp32_device_id(value):
    """Validate a hardware Device ID from the ESP32 / network handler.

    Two shapes are accepted:

    * ``PA-ESP32-A8F291C42D10`` — MAC based ESP32-S3 identifier;
    * ``PA-NET-12345678`` — shorter network-handler identifier.

    Lowercase input is accepted and uppercased; anything else is rejected.
    """

    if not isinstance(value, str):
        raise QRFormatError("A device ID is required.")
    device_id = value.strip().upper()
    if not device_id:
        raise QRFormatError("A device ID is required.")
    if len(device_id) > 64:
        raise QRFormatError("That device ID is too long.")
    if not HARDWARE_DEVICE_ID_PATTERN.match(device_id):
        raise QRFormatError(
            "Device IDs look like PA-ESP32-A8F291C42D10 "
            "(PA-ESP32- plus 12 hexadecimal characters) or PA-NET-12345678 "
            "(PA-NET- plus 8 hexadecimal characters)."
        )
    return device_id


def detect_lan_ip():
    """Return this computer's reachable IPv4 address, never loopback.

    A connected UDP socket makes the operating system pick the interface
    that would be used for outbound traffic; no packet is actually sent.
    The hostname lookup is a fallback for machines without a default route.
    """

    candidates = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.5)
            probe.connect(("8.8.8.8", 80))
            candidates.append(probe.getsockname()[0])
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            candidates.append(info[4][0])
    except OSError:
        pass

    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version != 4:
            continue
        if address.is_loopback or address.is_unspecified or address.is_link_local:
            continue
        return str(address)

    raise QRNetworkError(
        "No local network address could be detected. Set PUBLIC_BASE_URL "
        "(for example http://192.168.1.10:5000) in your .env file."
    )


def _validated_base_url(base_url):
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise QRNetworkError(
            "PUBLIC_BASE_URL must be a full http:// or https:// address."
        )
    if parts.query or parts.fragment:
        raise QRNetworkError(
            "PUBLIC_BASE_URL must not include a query string or fragment."
        )
    if parts.hostname.lower() in LOOPBACK_HOSTS:
        raise QRNetworkError(
            "The QR link must use a local network address, not localhost. "
            "Set PUBLIC_BASE_URL to this computer's LAN address."
        )
    return base_url.rstrip("/")


def get_public_base_url(base_url=None, port=None):
    """Return the configured or auto-detected LAN base URL."""

    if base_url is None:
        from flask import current_app, has_app_context

        if has_app_context():
            base_url = current_app.config.get("PUBLIC_BASE_URL")
            if port is None:
                port = current_app.config.get("PUBLIC_PORT", 5000)
        else:
            base_url = None

    if not base_url:
        if port is None:
            port = 5000
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 5000
        base_url = f"http://{detect_lan_ip()}:{port}"

    return _validated_base_url(str(base_url))


def build_registration_url(device_id, base_url=None, port=None):
    """Build the LAN URL encoded in the self-registration QR code."""

    device_id = normalize_esp32_device_id(device_id)
    base = get_public_base_url(base_url=base_url, port=port)
    query = urlencode({"device_id": device_id})
    return f"{base}{REGISTER_PATH}?{query}"


def generate_qr_png(text, box_size=10, border=4):
    """Render ``text`` as a PNG QR code and return the image bytes."""

    if not isinstance(text, str) or not text:
        raise QRFormatError("QR content is required.")

    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    code.add_data(text)
    code.make(fit=True)

    image = code.make_image(fill_color="black", back_color="white")
    if image.mode != "RGB":
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def normalize_pairing_code(value):
    """Accept codes typed with spaces or dashes, then validate them."""

    if not isinstance(value, str):
        raise QRFormatError("A pairing code is required.")
    code = re.sub(r"[\s-]+", "", value).upper()
    if not PAIRING_CODE_PATTERN.match(code):
        raise QRFormatError("That pairing code format is not valid.")
    return code


def build_payload(device_id, pairing_code):
    """Build the exact text that is printed into the outlet's QR label."""

    device_id = normalize_device_id(device_id)
    pairing_code = normalize_pairing_code(pairing_code)
    return (
        f"{QR_HEADER}\n"
        f"VERSION:{QR_VERSION}\n"
        f"DEVICE_ID:{device_id}\n"
        f"PAIRING_CODE:{pairing_code}\n"
    )


def parse_payload(raw):
    """Strictly parse a scanned PowerAble QR payload.

    Unknown keys, duplicate keys, a missing header, and extra garbage are
    rejected. Parsing does not prove the device exists or the code is
    valid — the backend must still check the database.
    """

    if not isinstance(raw, str):
        raise QRFormatError("No QR text was received.")
    if len(raw) > MAX_PAYLOAD_LENGTH:
        raise QRFormatError("That QR code is not a PowerAble label.")

    lines = [line.strip() for line in raw.replace("\r\n", "\n").split("\n")]
    lines = [line for line in lines if line]
    if len(lines) < 2 or lines[0] != QR_HEADER:
        raise QRFormatError("That QR code is not a PowerAble label.")

    values = {}
    for line in lines[1:]:
        if ":" not in line:
            raise QRFormatError("That QR code is not a PowerAble label.")
        key, value = line.split(":", 1)
        key = key.strip().upper()
        if key in values or key not in REQUIRED_KEYS:
            raise QRFormatError("That QR code is not a PowerAble label.")
        values[key] = value.strip()

    if set(values) != REQUIRED_KEYS:
        raise QRFormatError("That QR code is incomplete.")
    if values["VERSION"] != QR_VERSION:
        raise QRFormatError("This QR code version is not supported.")

    return PairingPayload(
        device_id=normalize_device_id(values["DEVICE_ID"]),
        pairing_code=normalize_pairing_code(values["PAIRING_CODE"]),
    )


def hash_pairing_code(code):
    """Return a one-way scrypt hash suitable for the database."""

    return generate_password_hash(normalize_pairing_code(code))


def verify_pairing_code(stored_hash, code):
    if not stored_hash:
        return False
    try:
        return check_password_hash(stored_hash, normalize_pairing_code(code))
    except (ValueError, TypeError):
        return False
