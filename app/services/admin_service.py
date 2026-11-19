"""Administrator operations: devices, accounts, and audit records.

Authority is enforced here, never in a template. Every mutating helper
calls :func:`require_admin` (except the installer CLI path, which is
already a trusted local action) and writes an :class:`AdminAction`.

Deliberate limits:
* An admin cannot change their own role, status, or delete themselves.
* The final active admin cannot be demoted, disabled, or deleted.
* Admin power covers accounts and pairing labels only. It never fabricates
  telemetry, relay state, or online status.
"""

import re

from sqlalchemy import func, or_, update

from app.extensions import db
from app.models import AdminAction, DeviceEvent, SmartOutlet, User
from app.models.smart_outlet import PAIRING_STATES
from app.models.user import ACCOUNT_STATES, ROLES
from app.services import device_service
from app.utils import utcnow

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")

MAX_SEARCH_LENGTH = 80
MAX_NAME_LENGTH = 80
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128


class AdminError(Exception):
    """A safe, user-presentable admin failure."""

    def __init__(self, message, status=400, code="admin_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def require_admin(user):
    if not getattr(user, "is_authenticated", False) or not getattr(
        user, "is_admin", False
    ):
        raise AdminError(
            "Administrator access is required.",
            status=403,
            code="admin_required",
        )


def _clean(value, limit=MAX_SEARCH_LENGTH):
    return " ".join((value or "").split())[:limit]


def _active_admin_count(exclude_id=None):
    stmt = db.select(func.count(User.id)).where(
        User.role == "admin", User.is_active_flag == "active"
    )
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return int(db.session.scalar(stmt) or 0)


def log_action(actor, action, target, description="", commit=False):
    """Write an audit record. Never include secrets in ``description``."""

    entry = AdminAction(
        actor_id=getattr(actor, "id", None),
        actor_name=getattr(actor, "username", None) or "installer CLI",
        action=action[:40],
        target=str(target)[:120],
        description=_clean(description, 255),
    )
    db.session.add(entry)
    if commit:
        db.session.commit()
    return entry


# ----------------------------------------------------------------------
# Read-only views
# ----------------------------------------------------------------------


def dashboard_stats():
    outlet_rows = db.session.execute(
        db.select(SmartOutlet.pairing_state, func.count(SmartOutlet.id)).group_by(
            SmartOutlet.pairing_state
        )
    ).all()
    by_state = {state: int(count) for state, count in outlet_rows}

    return {
        "users": int(db.session.scalar(db.select(func.count(User.id))) or 0),
        "admins": int(
            db.session.scalar(
                db.select(func.count(User.id)).where(User.role == "admin")
            )
            or 0
        ),
        "disabled_users": int(
            db.session.scalar(
                db.select(func.count(User.id)).where(
                    User.is_active_flag == "disabled"
                )
            )
            or 0
        ),
        "outlets": sum(by_state.values()),
        "registered": by_state.get("registered", 0),
        "available": by_state.get("available", 0),
        "revoked": by_state.get("revoked", 0),
        "reporting": int(
            db.session.scalar(
                db.select(func.count(SmartOutlet.id)).where(
                    SmartOutlet.last_seen_at.isnot(None)
                )
            )
            or 0
        ),
        "admin_actions": int(
            db.session.scalar(db.select(func.count(AdminAction.id))) or 0
        ),
        "device_events": int(
            db.session.scalar(db.select(func.count(DeviceEvent.id))) or 0
        ),
    }


def list_outlets(search=None, state=None, limit=None):
    stmt = db.select(SmartOutlet).outerjoin(
        User, SmartOutlet.owner_id == User.id
    )

    term = _clean(search)
    if term:
        pattern = f"%{term}%"
        stmt = stmt.where(
            or_(
                SmartOutlet.device_uid.ilike(pattern),
                SmartOutlet.outlet_name.ilike(pattern),
                User.username.ilike(pattern),
            )
        )

    if state in PAIRING_STATES:
        stmt = stmt.where(SmartOutlet.pairing_state == state)

    stmt = stmt.order_by(SmartOutlet.device_uid)
    if limit:
        stmt = stmt.limit(int(limit))

    return list(db.session.scalars(stmt))


def list_users(search=None):
    stmt = db.select(User)
    term = _clean(search)
    if term:
        stmt = stmt.where(User.username.ilike(f"%{term}%"))
    return list(db.session.scalars(stmt.order_by(User.created_at, User.id)))


def get_outlet(outlet_id):
    return db.session.get(SmartOutlet, outlet_id)


def get_user(user_id):
    return db.session.get(User, user_id)


def recent_device_events(limit=12):
    return list(
        db.session.scalars(
            db.select(DeviceEvent)
            .order_by(DeviceEvent.created_at.desc())
            .limit(limit)
        )
    )


def recent_admin_actions(limit=12):
    return list(
        db.session.scalars(
            db.select(AdminAction)
            .order_by(AdminAction.created_at.desc())
            .limit(limit)
        )
    )


# ----------------------------------------------------------------------
# Devices
# ----------------------------------------------------------------------


def provision_outlet(
    actor,
    device_id,
    device_type="Smart Power Outlet",
    hardware_esp32_id=None,
    network_esp32_id=None,
    model=None,
    socket_count=None,
):
    """Create or rotate a single-use QR label for a physical outlet.

    Args:
        actor: The admin user.
        device_id: The canonical device UID.
        device_type: Human-readable type label.
        hardware_esp32_id: Permanent Hardware ESP32 identifier.
        network_esp32_id: Permanent Network ESP32 identifier.
        model: Product model identifier.
        socket_count: Number of independent sockets (default 3).

    Returns:
        Tuple of (outlet, pairing_code, payload, device_secret).
        The device_secret is the plaintext per-device API secret for
        provisioning into the Network ESP32 firmware.
    """

    require_admin(actor)

    outlet, code, payload, device_secret = device_service.provision_device(
        device_id,
        device_type=_clean(device_type, 64) or "Smart Power Outlet",
        hardware_esp32_id=hardware_esp32_id,
        network_esp32_id=network_esp32_id,
        model=model,
        socket_count=socket_count,
    )

    description = "New single-use pairing label generated."
    if hardware_esp32_id or network_esp32_id:
        description += " Dual-board ESP32 IDs recorded."
    description += " Per-device API secret provisioned."

    log_action(
        actor,
        "outlet.provisioned",
        outlet.device_uid,
        description,
        commit=True,
    )
    return outlet, code, payload, device_secret


def rename_outlet(actor, outlet, name):
    require_admin(actor)
    if outlet is None:
        raise AdminError("That outlet no longer exists.", status=404, code="not_found")

    cleaned = _clean(name, MAX_NAME_LENGTH)
    if not cleaned:
        raise AdminError("Enter a display name for the outlet.")

    previous = outlet.outlet_name or "(no name)"
    if cleaned == outlet.outlet_name:
        return outlet

    outlet.outlet_name = cleaned
    outlet.updated_at = utcnow()
    log_action(
        actor,
        "outlet.renamed",
        outlet.device_uid,
        f"Renamed from {previous} to {cleaned}.",
    )
    db.session.commit()
    return outlet


def revoke_outlet(outlet, actor=None, reason=None):
    """Disable an outlet and release ownership. Safe to call from the CLI."""

    if outlet is None:
        raise AdminError("That outlet no longer exists.", status=404, code="not_found")

    note = _clean(reason, 200)
    description = "Outlet revoked by an administrator."
    if note:
        description = f"{description} Reason: {note}"

    outlet.pairing_state = "revoked"
    outlet.pairing_token_hash = None
    outlet.owner_id = None
    outlet.channel = None
    outlet.registered_at = None
    outlet.connection_status = "unknown"
    outlet.last_seen_at = None
    outlet.relay_state = "unknown"
    outlet.updated_at = utcnow()

    db.session.add(
        DeviceEvent(
            outlet_id=outlet.id,
            event_type="revoked",
            description=description[:255],
        )
    )
    log_action(
        actor,
        "outlet.revoked",
        outlet.device_uid,
        note or "Ownership released and pairing cleared.",
    )
    db.session.commit()
    return outlet


def delete_outlet(actor, outlet, confirmation):
    """Permanently remove an outlet and its event history."""

    require_admin(actor)
    if outlet is None:
        raise AdminError("That outlet no longer exists.", status=404, code="not_found")

    if _clean(confirmation, 64) != outlet.device_uid:
        raise AdminError(
            "Type the device ID exactly to confirm permanent deletion.",
            status=409,
            code="confirmation_mismatch",
        )

    target = outlet.device_uid
    db.session.delete(outlet)
    log_action(
        actor,
        "outlet.deleted",
        target,
        "Outlet and its device events were permanently deleted.",
    )
    db.session.commit()


# ----------------------------------------------------------------------
# Accounts
# ----------------------------------------------------------------------


def set_user_role(actor, user, role):
    require_admin(actor)
    if user is None:
        raise AdminError("That account no longer exists.", status=404, code="not_found")
    if role not in ROLES:
        raise AdminError("Unknown role.")
    if user.id == actor.id:
        raise AdminError(
            "You cannot change your own role. Ask another admin to do it.",
            status=409,
            code="self_action",
        )

    if role == user.role:
        return user

    if role == "user" and user.is_admin and _active_admin_count(exclude_id=user.id) < 1:
        raise AdminError(
            "At least one active administrator must remain.",
            status=409,
            code="last_admin",
        )

    previous = user.role
    user.role = role
    log_action(
        actor,
        "user.role_changed",
        user.username,
        f"Role changed from {previous} to {role}.",
    )
    db.session.commit()
    return user


def set_user_status(actor, user, status):
    require_admin(actor)
    if user is None:
        raise AdminError("That account no longer exists.", status=404, code="not_found")
    if status not in ACCOUNT_STATES:
        raise AdminError("Unknown account status.")
    if user.id == actor.id:
        raise AdminError(
            "You cannot disable your own account.",
            status=409,
            code="self_action",
        )

    if status == user.is_active_flag:
        return user

    if (
        status == "disabled"
        and user.is_admin
        and _active_admin_count(exclude_id=user.id) < 1
    ):
        raise AdminError(
            "At least one active administrator must remain.",
            status=409,
            code="last_admin",
        )

    previous = user.is_active_flag
    user.is_active_flag = status
    log_action(
        actor,
        "user.status_changed",
        user.username,
        f"Account changed from {previous} to {status}.",
    )
    db.session.commit()
    return user


def reset_user_password(actor, user, password):
    require_admin(actor)
    if user is None:
        raise AdminError("That account no longer exists.", status=404, code="not_found")

    password = password or ""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AdminError(
            f"Password must be {MAX_PASSWORD_LENGTH} characters or fewer."
        )

    user.set_password(password)
    log_action(
        actor,
        "user.password_reset",
        user.username,
        "Password replaced by an administrator.",
    )
    db.session.commit()
    return user


def delete_user(actor, user, confirmation):
    """Delete an account and revoke every outlet it owned."""

    require_admin(actor)
    if user is None:
        raise AdminError("That account no longer exists.", status=404, code="not_found")
    if user.id == actor.id:
        raise AdminError(
            "You cannot delete your own account.",
            status=409,
            code="self_action",
        )
    if _clean(confirmation, 80) != user.username:
        raise AdminError(
            "Type the username exactly to confirm permanent deletion.",
            status=409,
            code="confirmation_mismatch",
        )
    if user.is_admin and _active_admin_count(exclude_id=user.id) < 1:
        raise AdminError(
            "At least one active administrator must remain.",
            status=409,
            code="last_admin",
        )

    username = user.username
    released = 0
    for outlet in list(user.outlets):
        outlet.pairing_state = "revoked"
        outlet.pairing_token_hash = None
        outlet.owner_id = None
        outlet.channel = None
        outlet.registered_at = None
        outlet.connection_status = "unknown"
        outlet.last_seen_at = None
        outlet.relay_state = "unknown"
        outlet.ip_address = None
        outlet.hardware_connected = None
        outlet.network_mode = None
        outlet.connection_type = None
        outlet.updated_at = utcnow()
        released += 1
        db.session.add(
            DeviceEvent(
                outlet_id=outlet.id,
                event_type="revoked",
                description="Outlet released because its owner account was deleted.",
            )
        )

    # Keep audit history readable after the account is gone.
    db.session.execute(
        update(AdminAction)
        .where(AdminAction.actor_id == user.id)
        .values(actor_id=None)
    )

    log_action(
        actor,
        "user.deleted",
        username,
        f"Account deleted; {released} outlet(s) revoked.",
    )
    db.session.delete(user)
    db.session.commit()
    return released


# ----------------------------------------------------------------------
# CLI support
# ----------------------------------------------------------------------


def create_or_promote_admin(username, password):
    """Create an admin account, or promote/reset an existing one.

    Used only by the local installer CLI. The password is hashed before it
    is stored and is never written to logs or audit records.
    """

    normalized = (username or "").strip().lower()
    if not USERNAME_PATTERN.match(normalized):
        raise AdminError(
            "Username must be 3-32 characters: lowercase letters, numbers, "
            "dot, dash, or underscore."
        )
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise AdminError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AdminError(
            f"Password must be {MAX_PASSWORD_LENGTH} characters or fewer."
        )

    user = db.session.scalar(db.select(User).filter_by(username=normalized))
    created = user is None
    if created:
        user = User(username=normalized)
        db.session.add(user)

    user.set_password(password)
    user.role = "admin"
    user.is_active_flag = "active"
    db.session.flush()

    log_action(
        None,
        "admin.created" if created else "admin.promoted",
        normalized,
        "Admin account created by the installer CLI."
        if created
        else "Existing account promoted to admin by the installer CLI.",
    )
    db.session.commit()
    return user, created


__all__ = [
    "AdminError",
    "require_admin",
    "log_action",
    "dashboard_stats",
    "list_outlets",
    "list_users",
    "get_outlet",
    "get_user",
    "recent_device_events",
    "recent_admin_actions",
    "provision_outlet",
    "rename_outlet",
    "revoke_outlet",
    "delete_outlet",
    "set_user_role",
    "set_user_status",
    "reset_user_password",
    "delete_user",
    "create_or_promote_admin",
]
