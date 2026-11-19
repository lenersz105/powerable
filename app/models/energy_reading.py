"""EnergyReading model — electrical measurements from PZEM-004T sensors.

Each reading is tied to a specific socket on a SmartOutlet.  All numeric
fields are nullable so a missing sensor reports ``None`` instead of zero.
Never fabricate readings or substitute zero for sensor failure.
"""

from app.extensions import db
from app.utils import utcnow


class EnergyReading(db.Model):
    __tablename__ = "energy_readings"

    id = db.Column(db.Integer, primary_key=True)

    outlet_id = db.Column(
        db.Integer,
        db.ForeignKey("smart_outlets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    socket_number = db.Column(db.Integer, nullable=False)

    # Electrical measurements — all nullable; None means sensor unavailable.
    voltage = db.Column(db.Float, nullable=True)
    current = db.Column(db.Float, nullable=True)
    power_watts = db.Column(db.Float, nullable=True)
    energy_kwh = db.Column(db.Float, nullable=True)
    frequency = db.Column(db.Float, nullable=True)
    power_factor = db.Column(db.Float, nullable=True)

    recorded_at = db.Column(db.DateTime, nullable=False, index=True, default=utcnow)

    outlet = db.relationship("SmartOutlet", back_populates="energy_readings")

    __table_args__ = (
        db.CheckConstraint(
            "socket_number IN (1, 2, 3)", name="ck_energy_socket_number"
        ),
    )

    @classmethod
    def create(
        cls,
        outlet_id,
        socket_number,
        voltage=None,
        current=None,
        power_watts=None,
        energy_kwh=None,
        frequency=None,
        power_factor=None,
        recorded_at=None,
    ):
        """Create a new energy reading from authenticated hardware data."""
        reading = cls(
            outlet_id=outlet_id,
            socket_number=socket_number,
            voltage=voltage,
            current=current,
            power_watts=power_watts,
            energy_kwh=energy_kwh,
            frequency=frequency,
            power_factor=power_factor,
            recorded_at=recorded_at or utcnow(),
        )
        db.session.add(reading)
        return reading

    @property
    def has_any_reading(self):
        """True when at least one measurement field has a value."""
        return any(
            v is not None
            for v in (self.voltage, self.current, self.power_watts, self.energy_kwh)
        )

    def __repr__(self):
        return (
            f"<EnergyReading outlet={self.outlet_id} "
            f"socket={self.socket_number} "
            f"power={self.power_watts}W>"
        )
