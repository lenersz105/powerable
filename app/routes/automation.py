"""Automation blueprint: scheduling interface.

Saving and execution are still planned (Stage 5). The page lists the
user's real outlets for context but does not create schedules yet.
"""

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from app.services import device_service

automation_bp = Blueprint("automation", __name__)


@automation_bp.route("/")
@login_required
def index():
    outlets = device_service.get_user_outlets(current_user.id)
    return render_template(
        "automation/index.html",
        title="Scheduling",
        outlets=outlets,
    )
