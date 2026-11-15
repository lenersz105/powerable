"""Dashboard blueprint: live outlet monitor.

Reads only outlets owned by the signed-in user. No measurements are
generated here; cards show zero placeholders marked as awaiting data.
"""

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.services import device_service, qr_service

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    """Live monitor for the current user's registered outlets."""

    outlets = device_service.get_user_outlets(current_user.id)

    try:
        public_base_url = qr_service.get_public_base_url()
    except qr_service.QRNetworkError:
        public_base_url = None

    return render_template(
        "dashboard/index.html",
        title="Live Dashboard",
        outlets=outlets,
        counts=device_service.outlet_status_counts(outlets),
        public_base_url=public_base_url,
    )
