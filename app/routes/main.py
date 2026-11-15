"""Main blueprint: homepage, user manual, about, and QR registration."""

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.services import device_service
from app.services.device_service import PairingError

main_bp = Blueprint("main", __name__)


@main_bp.get("/register")
@login_required
def register_device():
    """Register an outlet from the Device ID encoded in a scanned QR link."""

    raw_device_id = request.args.get("device_id", "").strip()
    if not raw_device_id:
        return (
            render_template(
                "devices/register_result.html",
                title="Register outlet",
                ok=False,
                message=(
                    "This registration link has no device ID. Scan the QR code "
                    "again or enter the ID on the Add Smart Outlet page."
                ),
            ),
            400,
        )

    try:
        outlet, created = device_service.register_outlet_by_device_id(
            current_user, raw_device_id
        )
    except PairingError as error:
        return (
            render_template(
                "devices/register_result.html",
                title="Register outlet",
                ok=False,
                message=error.message,
                code=error.code,
                device_id=raw_device_id.upper(),
            ),
            error.status,
        )

    return (
        render_template(
            "devices/register_result.html",
            title="Register outlet",
            ok=True,
            created=created,
            outlet=outlet,
            message=(
                "Your smart outlet has been registered to your account."
                if created
                else "This smart outlet is already registered to your account."
            ),
        ),
        201 if created else 200,
    )


@main_bp.route("/")
def index():
    """Overview dashboard. Anonymous visitors see a sign-in empty state."""

    outlets = []
    if current_user.is_authenticated:
        outlets = device_service.get_user_outlets(current_user.id)

    return render_template(
        "main/index.html",
        title="Home",
        outlets=outlets,
        counts=device_service.outlet_status_counts(outlets),
    )


@main_bp.route("/manual")
def manual():
    """User manual and operating guidance."""

    return render_template("main/manual.html", title="User Manual")


@main_bp.route("/about")
def about():
    """Project background, architecture, and roadmap."""

    return render_template("main/about.html", title="About the Project")
