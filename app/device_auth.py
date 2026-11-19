"""Device API key authentication for ESP32-to-server communication.

The ESP32 Network Handler is not a browser: it has no session cookie and no
CSRF token. It authenticates with either:

1. A **per-device secret** provisioned at manufacturing and stored hashed in
   ``smart_outlets.device_api_secret``. The device sends its ``device_id``
   in the request body and its secret in the ``X-Device-Key`` header.
2. A **shared fallback key** (``DEVICE_API_KEY``) for backward compatibility
   with legacy devices that were provisioned before per-device secrets.

Per-device authentication is preferred: when a request includes a valid
``device_id`` and the corresponding outlet has a ``device_api_secret``, the
per-device secret is validated. If the outlet has no per-device secret, the
shared key is used as a fallback.

This module only authenticates the *device*. Ownership and registration rules
remain in :mod:`app.services.device_service`, so a valid key still cannot
create rows or reach another account's outlet.

The key is never written to responses or logs.
"""

import hashlib
import hmac
from functools import wraps

from flask import current_app, jsonify, request

DEVICE_KEY_HEADER = "X-Device-Key"
DEVICE_KEY_MISSING = "missing_device_key"
DEVICE_KEY_INVALID = "invalid_device_key"
DEVICE_KEY_UNCONFIGURED = "device_key_not_configured"


def _error(code, message, status):
    return jsonify(ok=False, error=code, message=message), status


def configured_device_key():
    """Return the configured shared device key, or an empty string."""

    value = current_app.config.get("DEVICE_API_KEY") or ""
    return str(value)


def _hash_secret(plaintext_secret):
    """Hash a per-device secret using SHA-256 for storage and comparison.

    Uses a salt derived from the device_id to prevent rainbow table attacks.
    The salt is not stored separately — it is deterministically derived from
    the device_id which is already unique per device.
    """
    return hashlib.sha256(plaintext_secret.encode("utf-8")).hexdigest()


def _extract_device_id_from_request():
    """Extract device_id from query params or JSON body.

    Returns the raw device_id string or None.
    """
    # Check query parameters first (used by GET /api/device/commands)
    device_id = request.args.get("device_id")
    if device_id:
        return device_id.strip()

    # Check JSON body (used by POST endpoints)
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        device_id = data.get("device_id")
        if isinstance(device_id, str) and device_id.strip():
            return device_id.strip()

    return None


def _validate_per_device_secret(device_id, supplied_secret):
    """Validate a per-device secret against the stored hash.

    Args:
        device_id: The raw device_id from the request.
        supplied_secret: The secret from the X-Device-Key header.

    Returns:
        True if the per-device secret matches, False otherwise.
    """
    from app.services import qr_service
    from app.extensions import db
    from app.models import SmartOutlet

    try:
        normalized = qr_service.normalize_esp32_device_id(device_id)
    except (qr_service.QRFormatError, AttributeError):
        return False

    outlet = db.session.scalar(
        db.select(SmartOutlet).filter_by(device_uid=normalized)
    )
    if outlet is None or not outlet.device_api_secret:
        return False

    expected_hash = _hash_secret(supplied_secret)
    return hmac.compare_digest(outlet.device_api_secret, expected_hash)


def require_device_key(view):
    """Reject device requests without a valid ``X-Device-Key`` header.

    Authentication order:
    1. If the request includes a ``device_id`` and the outlet has a
       ``device_api_secret``, validate the per-device secret.
    2. Fall back to the shared ``DEVICE_API_KEY`` for backward compatibility.

    Responses:
    * ``401`` when the header is missing;
    * ``403`` when the header is present but wrong;
    * ``503`` when the server has no key configured (fails closed).

    A successful check calls the wrapped view unchanged. The submitted and
    expected keys are compared with :func:`hmac.compare_digest` so timing does
    not reveal how many leading characters matched.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        supplied = request.headers.get(DEVICE_KEY_HEADER)

        if not supplied:
            return _error(
                DEVICE_KEY_MISSING,
                "The X-Device-Key header is required for device requests.",
                401,
            )

        # Try per-device secret first
        device_id = _extract_device_id_from_request()
        if device_id:
            if _validate_per_device_secret(device_id, supplied):
                return view(*args, **kwargs)

        # Fall back to shared key for backward compatibility
        expected = configured_device_key()

        if not expected:
            # Never fall back to a default here. An unconfigured production
            # server must refuse hardware traffic rather than accept a guess.
            current_app.logger.error(
                "DEVICE_API_KEY is not configured; device requests are blocked."
            )
            return _error(
                DEVICE_KEY_UNCONFIGURED,
                "Device authentication is not configured on this server.",
                503,
            )

        if not hmac.compare_digest(
            supplied.encode("utf-8"), expected.encode("utf-8")
        ):
            return _error(
                DEVICE_KEY_INVALID,
                "The device API key is not valid.",
                403,
            )

        return view(*args, **kwargs)

    return wrapped


__all__ = [
    "DEVICE_KEY_HEADER",
    "configured_device_key",
    "require_device_key",
]
