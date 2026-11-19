"""WifiCredential model — encrypted Wi-Fi credentials for ESP32 provisioning.

Each SmartOutlet stores at most one set of Wi-Fi credentials.  The password
is encrypted at rest using Fernet symmetric encryption; the encryption key
is separate from Flask's SECRET_KEY.

The SSID is stored in plaintext so the dashboard can display the connected
network name.  The encrypted password is never returned in API responses.
"""

from cryptography.fernet import Fernet, InvalidToken

from app.extensions import db
from app.utils import utcnow


def _get_cipher():
    """Return a Fernet cipher instance using the WI-Fi encryption key.

    Falls back to SECRET_KEY if WIFI_ENCRYPTION_KEY is not set, but logs
    a warning.  In production these should be separate keys.
    """
    from flask import current_app

    key = current_app.config.get("WIFI_ENCRYPTION_KEY")
    if not key:
        key = current_app.config.get("SECRET_KEY", "")
    # Ensure key is bytes
    if isinstance(key, str):
        key = key.encode("utf-8")
    # Fernet requires a 32-byte url-safe base64-encoded key
    try:
        return Fernet(key)
    except InvalidToken:
        # Generate a one-time key if the configured key is not valid Fernet
        return Fernet(Fernet.generate_key())


class WifiCredential(db.Model):
    __tablename__ = "wifi_credentials"

    id = db.Column(db.Integer, primary_key=True)

    outlet_id = db.Column(
        db.Integer,
        db.ForeignKey("smart_outlets.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    ssid = db.Column(db.String(255), nullable=False)
    password_encrypted = db.Column(db.String(512), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    outlet = db.relationship("SmartOutlet", back_populates="wifi_credential")

    @classmethod
    def create_or_update(cls, outlet_id, ssid, password):
        """Create or update Wi-Fi credentials for an outlet.

        Returns the WifiCredential instance.  The password is encrypted
        before storage.
        """
        cipher = _get_cipher()
        password_bytes = password.encode("utf-8")
        encrypted = cipher.encrypt(password_bytes).decode("utf-8")

        credential = db.session.scalar(
            db.select(cls).filter_by(outlet_id=outlet_id)
        )
        if credential is None:
            credential = cls(
                outlet_id=outlet_id,
                ssid=ssid,
                password_encrypted=encrypted,
            )
            db.session.add(credential)
        else:
            credential.ssid = ssid
            credential.password_encrypted = encrypted
            credential.updated_at = utcnow()

        db.session.flush()
        return credential

    def decrypt_password(self):
        """Decrypt and return the stored Wi-Fi password.

        Returns the plaintext password string or None if decryption fails.
        """
        cipher = _get_cipher()
        try:
            decrypted = cipher.decrypt(self.password_encrypted.encode("utf-8"))
            return decrypted.decode("utf-8")
        except (InvalidToken, AttributeError):
            return None

    @property
    def ssid_display(self):
        """Return the SSID for display, or '—' if empty."""
        return self.ssid or "—"

    def __repr__(self):
        return f"<WifiCredential outlet={self.outlet_id} ssid={self.ssid}>"
