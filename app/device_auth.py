"""Device API key authentication for ESP32-to-server communication.

The ESP32 Network Handler is not a browser: it has no session cookie and no
CSRF token. It authenticates with a single shared secret sent in the
``X-Device-Key`` header. The key lives in ``current_app.config`` (set from
``DEVICE_API_KEY`` in the environment) and is compared in constant time.

This module only authenticates the *device*. Ownership and registration rules
remain in :mod:`app.services.device_service`, so a valid key still cannot
create rows or reach another account's outlet.

The key is never written to responses or logs.
"""

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
    """Return the configured device key, or an empty string."""

    value = current_app.config.get("DEVICE_API_KEY") or ""
    return str(value)


def require_device_key(view):
    """Reject device requests without a valid ``X-Device-Key`` header.

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
        expected = configured_device_key()
        supplied = request.headers.get(DEVICE_KEY_HEADER)

        if not supplied:
            return _error(
                DEVICE_KEY_MISSING,
                "The X-Device-Key header is required for device requests.",
                401,
            )

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
