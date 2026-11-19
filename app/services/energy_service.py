"""Energy service — telemetry ingestion and reading aggregation.

Responsibilities:
- Validate and store energy readings from authenticated ESP32 hardware.
- Calculate power from voltage/current if not provided.
- Prepare time-windowed aggregates for the monitoring charts.

Rule: never generate fake electrical measurements. Only process
data received from real, authenticated hardware.
"""

from datetime import timedelta

from sqlalchemy import func

from app.extensions import db
from app.models import EnergyReading
from app.utils import utcnow


class EnergyError(Exception):
    """A safe, user-presentable energy service failure."""

    def __init__(self, message, status=400, code="energy_error"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


def calculate_power(voltage, current, power_factor=1.0):
    """Calculate active power: P = V x I x PF.

    Returns None if any required value is None.
    """
    if voltage is None or current is None:
        return None
    return round(voltage * current * power_factor, 2)


def ingest_telemetry(outlet_id, socket_number, data):
    """Ingest a telemetry reading from authenticated ESP32 hardware.

    Args:
        outlet_id: The SmartOutlet ID.
        socket_number: 1, 2, or 3.
        data: Dict with optional keys: voltage, current, power_watts,
            energy_kwh, frequency, power_factor.

    Returns:
        The created EnergyReading instance.

    Raises:
        EnergyError: If validation fails.
    """
    if socket_number not in (1, 2, 3):
        raise EnergyError("socket_number must be 1, 2, or 3.", status=400, code="invalid_socket")

    # Extract and validate numeric fields
    def _float_or_none(key):
        val = data.get(key)
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            raise EnergyError(
                f"{key} must be a number.", status=400, code="invalid_value"
            )

    voltage = _float_or_none("voltage")
    current = _float_or_none("current")
    power_watts = _float_or_none("power_watts")
    energy_kwh = _float_or_none("energy_kwh")
    frequency = _float_or_none("frequency")
    power_factor = _float_or_none("power_factor")

    # Calculate power if not provided but voltage and current are
    if power_watts is None and voltage is not None and current is not None:
        pf = power_factor or 1.0
        power_watts = calculate_power(voltage, current, pf)

    # Validate ranges (reject obviously wrong values)
    if voltage is not None and (voltage < 0 or voltage > 500):
        raise EnergyError("voltage is out of range (0-500V).", status=400, code="out_of_range")
    if current is not None and (current < 0 or current > 100):
        raise EnergyError("current is out of range (0-100A).", status=400, code="out_of_range")
    if power_watts is not None and (power_watts < 0 or power_watts > 50000):
        raise EnergyError("power_watts is out of range.", status=400, code="out_of_range")

    reading = EnergyReading.create(
        outlet_id=outlet_id,
        socket_number=socket_number,
        voltage=voltage,
        current=current,
        power_watts=power_watts,
        energy_kwh=energy_kwh,
        frequency=frequency,
        power_factor=power_factor,
    )

    db.session.commit()
    return reading


def get_latest_readings(outlet_id, socket_number=None, limit=1):
    """Get the most recent readings for an outlet.

    Args:
        outlet_id: The SmartOutlet ID.
        socket_number: Optional specific socket (1, 2, or 3).
        limit: Number of readings to return.

    Returns:
        List of EnergyReading instances.
    """
    stmt = db.select(EnergyReading).filter_by(outlet_id=outlet_id)
    if socket_number is not None:
        stmt = stmt.filter_by(socket_number=socket_number)
    stmt = stmt.order_by(EnergyReading.recorded_at.desc()).limit(limit)
    return list(db.session.scalars(stmt))


def get_readings_for_chart(outlet_id, socket_number, hours=24, interval_minutes=5):
    """Get aggregated readings for a chart.

    Args:
        outlet_id: The SmartOutlet ID.
        socket_number: 1, 2, or 3.
        hours: Number of hours to look back.
        interval_minutes: Aggregation interval.

    Returns:
        List of dicts with timestamp and averaged values.
    """
    from datetime import timedelta

    now = utcnow()
    start = now - timedelta(hours=hours)

    readings = (
        db.session.scalars(
            db.select(EnergyReading)
            .filter_by(outlet_id=outlet_id, socket_number=socket_number)
            .filter(EnergyReading.recorded_at >= start)
            .order_by(EnergyReading.recorded_at)
        )
    ).all()

    if not readings:
        return []

    # Simple aggregation: group by interval and average
    results = []
    current_interval = []
    interval_start = start.replace(minute=(start.minute // interval_minutes) * interval_minutes, second=0, microsecond=0)

    for r in readings:
        while interval_start < r.recorded_at - timedelta(minutes=interval_minutes):
            if current_interval:
                results.append(_aggregate_interval(interval_start, current_interval))
            current_interval = []
            interval_start += timedelta(minutes=interval_minutes)
        current_interval.append(r)

    # Add the last interval
    if current_interval:
        results.append(_aggregate_interval(interval_start, current_interval))

    return results


def _aggregate_interval(start, readings):
    """Average a list of readings into a single data point."""
    def _avg(values):
        non_none = [v for v in values if v is not None]
        if not non_none:
            return None
        return round(sum(non_none) / len(non_none), 2)

    return {
        "timestamp": start.isoformat(),
        "voltage": _avg(r.voltage for r in readings),
        "current": _avg(r.current for r in readings),
        "power_watts": _avg(r.power_watts for r in readings),
        "energy_kwh": readings[-1].energy_kwh if any(r.energy_kwh is not None for r in readings) else None,
        "frequency": _avg(r.frequency for r in readings),
        "power_factor": _avg(r.power_factor for r in readings),
    }


def summarize_readings(outlet_id, start, end):
    """Aggregate readings for a time window.

    Returns a dict with min, max, avg for voltage, current, and power.
    """
    readings = (
        db.session.scalars(
            db.select(EnergyReading)
            .filter_by(outlet_id=outlet_id)
            .filter(
                EnergyReading.recorded_at >= start,
                EnergyReading.recorded_at <= end,
            )
        )
    ).all()

    if not readings:
        return {
            "count": 0,
            "voltage": None,
            "current": None,
            "power_watts": None,
            "energy_kwh": None,
        }

    def _stats(field):
        values = [getattr(r, field) for r in readings if getattr(r, field) is not None]
        if not values:
            return None
        return {
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "avg": round(sum(values) / len(values), 2),
        }

    return {
        "count": len(readings),
        "voltage": _stats("voltage"),
        "current": _stats("current"),
        "power_watts": _stats("power_watts"),
        "energy_kwh": readings[-1].energy_kwh if any(r.energy_kwh is not None for r in readings) else None,
    }


def serialize_reading(reading):
    """Return a non-secret dict for JSON responses."""
    return {
        "outlet_id": reading.outlet_id,
        "socket_number": reading.socket_number,
        "voltage": reading.voltage,
        "current": reading.current,
        "power_watts": reading.power_watts,
        "energy_kwh": reading.energy_kwh,
        "frequency": reading.frequency,
        "power_factor": reading.power_factor,
        "recorded_at": reading.recorded_at.isoformat() if reading.recorded_at else None,
    }
