"""Admin blueprint: QR labels, outlet lifecycle, accounts, and audit view.

Every route is protected by :func:`app.permissions.admin_required`. The
service layer repeats the role check, so hiding a button in a template can
never grant authority by itself.

Pairing codes are rendered once, directly into the response of the request
that generated them, and marked ``Cache-Control: no-store``. Only the scrypt
hash is kept in the database.
"""

import segno
from flask import (
    Blueprint,
    abort,
    flash,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user

from app.models.smart_outlet import PAIRING_STATES
from app.permissions import admin_required
from app.services import admin_service, qr_service
from app.services.admin_service import AdminError
from app.services.device_service import PairingError

admin_bp = Blueprint("admin", __name__)

DEFAULT_DEVICE_TYPE = "Smart Power Outlet"


def _search():
    return request.args.get("search", "").strip()


def _state_filter():
    state = request.args.get("state", "").strip()
    return state if state in PAIRING_STATES else ""


def _form_values():
    return {
        "device_id": request.form.get("device_id", "").strip(),
        "device_type": request.form.get("device_type", "").strip()
        or DEFAULT_DEVICE_TYPE,
        "hardware_esp32_id": request.form.get("hardware_esp32_id", "").strip() or None,
        "network_esp32_id": request.form.get("network_esp32_id", "").strip() or None,
        "model": request.form.get("model", "").strip() or None,
        "socket_count": request.form.get("socket_count", "").strip() or None,
    }


def _flash_error(error):
    flash(getattr(error, "message", str(error)), "error")


def _render_label(outlet, pairing_code, device_secret=None):
    """Render the one-time label response. The code and secret are never stored in a session."""

    payload = qr_service.build_payload(outlet.device_uid, pairing_code)
    qr_svg = segno.make(payload, error="m").svg_inline(
        scale=5, border=3, dark="#000000", light="#ffffff"
    )
    response = make_response(
        render_template(
            "admin/label.html",
            title=f"Pairing label · {outlet.device_uid}",
            outlet=outlet,
            pairing_code=pairing_code,
            device_secret=device_secret,
            qr_svg=qr_svg,
        )
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _outlet_or_404(outlet_id):
    outlet = admin_service.get_outlet(outlet_id)
    if outlet is None:
        abort(404)
    return outlet


def _user_or_404(user_id):
    user = admin_service.get_user(user_id)
    if user is None:
        abort(404)
    return user


# ----------------------------------------------------------------------
# Overview
# ----------------------------------------------------------------------


@admin_bp.get("/")
@admin_required
def index():
    return render_template(
        "admin/index.html",
        title="Admin console",
        stats=admin_service.dashboard_stats(),
        actions=admin_service.recent_admin_actions(8),
        events=admin_service.recent_device_events(8),
    )


# ----------------------------------------------------------------------
# Outlets and QR labels
# ----------------------------------------------------------------------


@admin_bp.get("/outlets")
@admin_required
def outlets():
    search = _search()
    state = _state_filter()
    rows = admin_service.list_outlets(search=search, state=state)
    return render_template(
        "admin/outlets.html",
        title="Outlets and labels",
        outlets=rows,
        search=search,
        state=state,
        states=PAIRING_STATES,
        stats=admin_service.dashboard_stats(),
    )


@admin_bp.route("/outlets/new", methods=["GET", "POST"])
@admin_required
def new_outlet():
    values = {
        "device_id": "",
        "device_type": DEFAULT_DEVICE_TYPE,
        "hardware_esp32_id": "",
        "network_esp32_id": "",
        "model": "",
        "socket_count": "3",
    }

    if request.method == "POST":
        values = _form_values()
        try:
            outlet, code, _payload, device_secret = admin_service.provision_outlet(
                current_user,
                values["device_id"],
                device_type=values["device_type"],
                hardware_esp32_id=values.get("hardware_esp32_id"),
                network_esp32_id=values.get("network_esp32_id"),
                model=values.get("model"),
                socket_count=values.get("socket_count"),
            )
        except (AdminError, PairingError) as error:
            _flash_error(error)
        else:
            return _render_label(outlet, code, device_secret=device_secret)

    return render_template(
        "admin/new_label.html",
        title="Generate pairing label",
        values=values,
    )


@admin_bp.post("/outlets/<int:outlet_id>/rotate")
@admin_required
def rotate_outlet(outlet_id):
    outlet = _outlet_or_404(outlet_id)
    try:
        outlet, code, _payload, device_secret = admin_service.provision_outlet(
            current_user,
            outlet.device_uid,
            device_type=outlet.device_type,
            hardware_esp32_id=outlet.hardware_esp32_id,
            network_esp32_id=outlet.network_esp32_id,
            model=outlet.model,
            socket_count=outlet.socket_count,
        )
    except (AdminError, PairingError) as error:
        _flash_error(error)
        return redirect(url_for("admin.outlets"))

    return _render_label(outlet, code, device_secret=device_secret)


@admin_bp.post("/outlets/<int:outlet_id>/rename")
@admin_required
def rename_outlet(outlet_id):
    outlet = _outlet_or_404(outlet_id)
    try:
        admin_service.rename_outlet(
            current_user, outlet, request.form.get("outlet_name", "")
        )
    except AdminError as error:
        _flash_error(error)
    else:
        flash(f"Renamed {outlet.device_uid}.", "success")
    return redirect(url_for("admin.outlets"))


@admin_bp.post("/outlets/<int:outlet_id>/revoke")
@admin_required
def revoke_outlet(outlet_id):
    outlet = _outlet_or_404(outlet_id)
    try:
        admin_service.revoke_outlet(
            outlet, actor=current_user, reason=request.form.get("reason", "")
        )
    except AdminError as error:
        _flash_error(error)
    else:
        flash(
            f"{outlet.device_uid} was revoked and released from its owner.",
            "success",
        )
    return redirect(url_for("admin.outlets"))


@admin_bp.post("/outlets/<int:outlet_id>/delete")
@admin_required
def delete_outlet(outlet_id):
    outlet = _outlet_or_404(outlet_id)
    device_id = outlet.device_uid
    try:
        admin_service.delete_outlet(
            current_user, outlet, request.form.get("confirm_device_id", "")
        )
    except AdminError as error:
        _flash_error(error)
    else:
        flash(f"{device_id} was permanently deleted.", "success")
    return redirect(url_for("admin.outlets"))


# ----------------------------------------------------------------------
# Accounts
# ----------------------------------------------------------------------


@admin_bp.get("/users")
@admin_required
def users():
    search = _search()
    return render_template(
        "admin/users.html",
        title="Accounts",
        users=admin_service.list_users(search=search),
        search=search,
        stats=admin_service.dashboard_stats(),
    )


@admin_bp.post("/users/<int:user_id>/role")
@admin_required
def set_role(user_id):
    user = _user_or_404(user_id)
    try:
        admin_service.set_user_role(
            current_user, user, request.form.get("role", "").strip()
        )
    except AdminError as error:
        _flash_error(error)
    else:
        flash(f"{user.username} is now {user.role}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.post("/users/<int:user_id>/status")
@admin_required
def set_status(user_id):
    user = _user_or_404(user_id)
    try:
        admin_service.set_user_status(
            current_user, user, request.form.get("status", "").strip()
        )
    except AdminError as error:
        _flash_error(error)
    else:
        flash(f"{user.username} is now {user.is_active_flag}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.post("/users/<int:user_id>/password")
@admin_required
def reset_password(user_id):
    user = _user_or_404(user_id)
    password = request.form.get("password", "")
    confirmation = request.form.get("confirm_password", "")

    if password != confirmation:
        flash("The two passwords do not match.", "error")
        return redirect(url_for("admin.users"))

    try:
        admin_service.reset_user_password(current_user, user, password)
    except AdminError as error:
        _flash_error(error)
    else:
        flash(f"Password replaced for {user.username}.", "success")
    return redirect(url_for("admin.users"))


@admin_bp.post("/users/<int:user_id>/delete")
@admin_required
def delete_user(user_id):
    user = _user_or_404(user_id)
    username = user.username
    try:
        released = admin_service.delete_user(
            current_user, user, request.form.get("confirm_username", "")
        )
    except AdminError as error:
        _flash_error(error)
    else:
        flash(
            f"{username} was deleted; {released} outlet(s) were revoked.",
            "success",
        )
    return redirect(url_for("admin.users"))


# ----------------------------------------------------------------------
# Audit
# ----------------------------------------------------------------------


@admin_bp.get("/activity")
@admin_required
def activity():
    return render_template(
        "admin/activity.html",
        title="Admin activity",
        actions=admin_service.recent_admin_actions(100),
        events=admin_service.recent_device_events(100),
    )
