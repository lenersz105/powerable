"""DeviceEvent model — an audit trail for outlet lifecycle events.

Only events that actually happened are stored (provisioned, registered,
revoked, and later online/offline/relay changes from real hardware).
"""

from app.extensions import db
from app.utils import utcnow


class DeviceEvent(db.Model):
    __tablename__ = "device_events"

    id = db.Column(db.Integer, primary_key=True)
    outlet_id = db.Column(
        db.Integer,
        db.ForeignKey("smart_outlets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type = db.Column(db.String(40), nullable=False, index=True)
    description = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)

    outlet = db.relationship("SmartOutlet", back_populates="events")

    def __repr__(self):
        return f"<DeviceEvent {self.event_type} outlet={self.outlet_id}>"
