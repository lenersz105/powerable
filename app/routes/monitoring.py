"""Monitoring blueprint: energy monitoring pages and data endpoints.

Planned routes (not yet implemented):
- GET /monitoring/                 monitoring overview page
- GET /monitoring/api/readings    JSON endpoint consumed by
                                   vanilla JavaScript charts

All readings must originate from authenticated hardware
communication. Never fabricate electrical measurements.
"""

from flask import Blueprint, render_template

monitoring_bp = Blueprint("monitoring", __name__)


@monitoring_bp.route("/")
def index():
    """Placeholder monitoring page."""
    return render_template("monitoring/index.html", title="Monitoring")
