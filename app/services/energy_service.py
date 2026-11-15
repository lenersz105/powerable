"""Energy service — PLANNED, not yet implemented.

Responsibilities when implemented:
- Convert raw voltage/current samples into power (watts) and
  accumulated energy (kWh).
- Prepare time-windowed aggregates for the monitoring charts.

Rule: never generate fake electrical measurements. Only process
data received from real, authenticated hardware.
"""


def calculate_power(voltage, current, power_factor=1.0):
    """Placeholder: P = V x I x PF."""
    raise NotImplementedError("Energy calculations are not implemented yet.")


def summarize_readings(outlet_id, start, end):
    """Placeholder: aggregate readings for a time window."""
    raise NotImplementedError("Reading summaries are not implemented yet.")
