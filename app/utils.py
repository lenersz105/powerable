"""Small shared helpers used across models, services, and routes."""

from datetime import datetime, timezone


def utcnow():
    """Return the current UTC time as a naive datetime.

    SQLite stores datetimes without timezone information. Keeping every
    stored timestamp naive-but-UTC avoids comparing aware and naive values,
    which raises TypeError.
    """

    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_iso(value):
    """Serialise a stored datetime as ISO-8601 UTC (or None)."""

    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
