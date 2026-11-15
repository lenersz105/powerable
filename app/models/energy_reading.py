"""EnergyReading model — PLANNED, not yet implemented.

Planned fields (see docs/database-design.md):
- outlet_id: foreign key to smart_outlets.id
- voltage, current, power_watts, energy_kwh
- power_factor: optional, depends on hardware support
- recorded_at: measurement timestamp

Important rule: readings must come from real, authenticated hardware
communication. Never store or display simulated values as real
measurements. Test/demo data must be clearly labelled as such.
"""
