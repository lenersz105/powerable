"""User model and password helpers.

Passwords are stored only as Werkzeug hashes. Pairing tokens are stored on
SmartOutlet, separately and also hashed.

Roles:
* ``user``  — may pair and read only their own outlets.
* ``admin`` — full installer authority (QR labels, revocation, account
  management). Admins are created explicitly, never by self-registration.
"""

from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db

ROLES = ("user", "admin")
ACCOUNT_STATES = ("active", "disabled")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)

    # Normalised lowercase login name (see app/routes/auth.py).
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)

    # Stores a hash, NEVER the plain password.
    password_hash = db.Column(db.String(256), nullable=False)

    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(timezone.utc), nullable=False
    )

    # "active" or "disabled" — used for account status checks.
    is_active_flag = db.Column(
        db.String(20),
        default="active",
        server_default="active",
        nullable=False,
    )

    # "user" or "admin". Defaults to the least privileged role.
    role = db.Column(
        db.String(20),
        default="user",
        server_default="user",
        nullable=False,
        index=True,
    )

    outlets = db.relationship(
        "SmartOutlet",
        back_populates="owner",
        order_by="SmartOutlet.channel",
    )

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        """True only for accounts explicitly granted the admin role."""

        return self.role == "admin"

    @property
    def is_active(self):
        """Flask-Login hook: disabled accounts cannot hold a session."""

        return self.is_active_flag == "active"

    @property
    def display_name(self):
        return self.username

    def __repr__(self):
        return f"<User {self.username}>"
