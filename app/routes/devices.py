"""Devices blueprint: outlets, self-registration, and QR pairing."""

from io import BytesIO

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from app.services import device_service, qr_service
from app.services.device_service import PairingError

devices_bp = Blueprint("devices", __name__)


@devices_bp.route("/")
@login_required
def index():
    """List only the signed-in customer's outlets."""

    outlets = device_service.get_user_outlets(current_user.id)
    return render_template(
        "devices/index.html",
        title="My outlets",
        outlets=outlets,
        counts=device_service.outlet_status_counts(outlets),
    )


@devices_bp.route("/add", methods=["GET", "POST"])
@login_required
def add():
    """Add a SmartOutlet.

    Role-based routing:
    - Admin: Opens the admin provisioning interface (admin/outlets/new).
    - Regular user: Opens the QR scanner to claim an admin-provisioned device.

    Regular users cannot self-register devices by device ID.
    """

    # Non-admin users are redirected to the QR scanner for claiming devices
    if not current_user.is_admin:
        return redirect(url_for("devices.pair"))

    # Admin path: self-registration by device ID (legacy flow preserved)
    device_id = (
        request.form.get("device_id") or request.args.get("device_id") or ""
    ).strip()

    if request.method == "GET":
        return render_template(
            "devices/add.html",
            title="Add Smart Outlet",
            device_id=device_id,
        )

    action = request.form.get("action", "preview")

    try:
        normalized = qr_service.normalize_esp32_device_id(device_id)
    except qr_service.QRFormatError as error:
        return (
            render_template(
                "devices/add.html",
                title="Add Smart Outlet",
                device_id=device_id,
                error=str(error),
            ),
            400,
        )

    existing = device_service.get_outlet_by_device_id(normalized)

    if action == "register":
        try:
            outlet, created = device_service.register_outlet_by_device_id(
                current_user, normalized
            )
        except PairingError as error:
            return (
                render_template(
                    "devices/add.html",
                    title="Add Smart Outlet",
                    device_id=normalized,
                    existing=existing,
                    error=error.message,
                ),
                error.status,
            )

        if created:
            flash(
                f"{outlet.display_name} ({outlet.device_uid}) was registered.",
                "success",
            )
        else:
            flash(
                f"{outlet.display_name} ({outlet.device_uid}) is already registered "
                "to your account.",
                "info",
            )
        return redirect(url_for("devices.index"))

    if existing is not None:
        if existing.owner_id == current_user.id:
            message = (
                "This device ID is already registered to your account. "
                "Open My Outlets to view it."
            )
        elif existing.owner_id is not None:
            message = "This device ID is already registered to another account."
        else:
            message = (
                "This device ID has already been provisioned. Use the pairing "
                "code flow to claim it."
            )
        return (
            render_template(
                "devices/add.html",
                title="Add Smart Outlet",
                device_id=normalized,
                existing=existing,
                error=message,
            ),
            409,
        )

    try:
        registration_url = qr_service.build_registration_url(normalized)
    except qr_service.QRNetworkError as error:
        return (
            render_template(
                "devices/add.html",
                title="Add Smart Outlet",
                device_id=normalized,
                error=str(error),
            ),
            503,
        )

    return render_template(
        "devices/add.html",
        title="Add Smart Outlet",
        device_id=normalized,
        registration_url=registration_url,
        qr_image_url=url_for("devices.qr_code", device_id=normalized),
    )


@devices_bp.get("/qr/<device_id>.png")
@login_required
def qr_code(device_id):
    """Render the self-registration QR as a PNG, optionally as a download."""

    try:
        normalized = qr_service.normalize_esp32_device_id(device_id)
    except qr_service.QRFormatError as error:
        abort(400, description=str(error))

    try:
        registration_url = qr_service.build_registration_url(normalized)
        image_bytes = qr_service.generate_qr_png(registration_url)
    except qr_service.QRNetworkError as error:
        abort(503, description=str(error))

    download = request.args.get("download") == "1"
    response = send_file(
        BytesIO(image_bytes),
        mimetype="image/png",
        as_attachment=download,
        download_name=f"{normalized}-registration-qr.png",
        max_age=0,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@devices_bp.route("/pair")
@login_required
def pair():
    """Camera-based QR scanner and pairing-code registration flow."""

    return render_template("devices/pair.html", title="Add Smart Outlet")
