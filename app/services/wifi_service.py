"""Wi-Fi credential service — manage encrypted Wi-Fi credentials for ESP32.

Responsibilities:
- Store Wi-Fi credentials encrypted at rest
- Update existing credentials
- Never expose passwords in API responses
"""

from app.extensions import db
from app.models import WifiCredential


class WifiError(Exception):
    """A safe, user-presentable Wi-Fi service failure."""

    def __init__(self, message, status=400, code="wifi_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def setup_wifi(outlet_id, ssid, password):
    """Store or update Wi-Fi credentials for an outlet.

    Args:
        outlet_id: The SmartOutlet ID.
        ssid: Wi-Fi network name.
        password: Wi-Fi password (will be encrypted).

    Returns:
        The WifiCredential instance (without the decrypted password).

    Raises:
        WifiError: If validation fails.
    """
    if not ssid or not ssid.strip():
        raise WifiError("SSID is required.", status=400, code="missing_ssid")

    if not password:
        raise WifiError("Wi-Fi password is required.", status=400, code="missing_password")

    ssid = ssid.strip()
    if len(ssid) > 255:
        raise WifiError("SSID is too long.", status=400, code="invalid_ssid")

    if len(password) > 255:
        raise WifiError("Password is too long.", status=400, code="invalid_password")

    credential = WifiCredential.create_or_update(outlet_id, ssid, password)
    db.session.commit()
    return credential


def get_wifi_status(outlet_id):
    """Check whether Wi-Fi credentials are configured for an outlet.

    Returns:
        Dict with configured status and SSID (never the password).
    """
    credential = db.session.scalar(
        db.select(WifiCredential).filter_by(outlet_id=outlet_id)
    )
    if credential is None:
        return {
            "configured": False,
            "ssid": None,
        }
    return {
        "configured": True,
        "ssid": credential.ssid,
    }


def serialize_wifi_status(credential):
    """Return a non-secret dict for JSON responses."""
    return {
        "configured": True,
        "ssid": credential.ssid,
    }
