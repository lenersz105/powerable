# PowerAble — Database Design

## Current state

SQLite through SQLAlchemy, with schema history managed by Flask-Migrate.

| Table | Status | Migration |
|-------|--------|-----------|
| `users` | Implemented | `5fcf2e0fd4ae` (initial), `role` in `605cdba02237` |
| `smart_outlets` | Implemented | `986f5edc3ab3`, network fields in `b7c2e5f1a9d4` and `c8f4b2d6a1e3` |
| `device_events` | Implemented | `986f5edc3ab3` |
| `admin_actions` | Implemented | `605cdba02237` |
| `energy_readings` | Planned | — |
| `automation_schedules` | Planned | — |

Migration head: **`c8f4b2d6a1e3`**.

`app/models/energy_reading.py` and `app/models/automation_schedule.py`
currently hold documentation only; they are not imported by
`app/models/__init__.py` and create no tables.

## Entity relationships (overview)

```
User 1 ──── * SmartOutlet 1 ──── * EnergyReading      (planned)
  │               │
  │               ├── * AutomationSchedule            (planned)
  │               └── * DeviceEvent                   (implemented)
  │
  └── * AdminAction                                  (implemented)

User 1 ──── * AdminAction.actor_id (nullable, ON DELETE SET NULL)
```

- One **User** can own many **SmartOutlets** (`smart_outlets.owner_id`).
- One **User** can perform many audited **AdminActions**. The actor FK is set to
  `NULL` if the account is deleted; `actor_name` keeps the history readable.
- One **SmartOutlet** produces many **EnergyReadings** (planned).
- One **SmartOutlet** can have many **AutomationSchedules** (planned).
- One **SmartOutlet** logs many **DeviceEvents** (`device_events.outlet_id`).

## Implemented tables

### users

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| username | String(80), unique, indexed | Stored lowercase |
| password_hash | String(256) | Werkzeug hash, never plaintext |
| created_at | DateTime | UTC, not null |
| is_active_flag | String(20) | `"active"` / `"disabled"`, not null, default `"active"` |
| role | String(20), indexed | `"user"` / `"admin"`, not null, default `"user"` |

Relationship: `User.outlets` → `SmartOutlet`, ordered by channel.

Rules:

- Public registration always creates `role="user"`.
- Admins are created or promoted only by the local `create-admin` CLI.
- `User.is_admin` is true only for `role == "admin"`.
- `User.is_active` maps to `is_active_flag == "active"` and is used by
  Flask-Login. A disabled account is also treated as unauthenticated and is
  signed out on its next request.

### smart_outlets

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| device_uid | String(64), unique, indexed | Permanent ID printed on the label, e.g. `PA-ESP32-0001` |
| device_type | String(64) | Default `Smart Power Outlet` |
| owner_id | Integer FK → `users.id` (`ON DELETE SET NULL`), indexed, nullable | `NULL` until a valid pairing is registered |
| outlet_name | String(80), nullable | User-editable display name |
| channel | Integer, nullable | Allocated per owner at registration |
| pairing_state | String(20), indexed | `unregistered`, `available`, `pending`, `registered`, `revoked` |
| pairing_token_hash | String(256), nullable | scrypt hash of the one-time code; never the code |
| registered_at | DateTime, nullable | Set when an owner claims the outlet |
| connection_status | String(20) | `unknown`, `online`, `stale`, `disconnected`; default `unknown` |
| last_seen_at | DateTime, nullable | Only set by real telemetry, never by registration |
| relay_state | String(10) | `on`, `off`, `unknown`; default `unknown` |
| ip_address | String(45), nullable | Last LAN address reported by the ESP32 heartbeat |
| hardware_connected | Boolean, nullable | `NULL` until the hardware controller reports a state |
| network_mode | String(16), nullable | Last reported `online` or `offline` mode |
| connection_type | String(16), nullable | Last reported `router` or `access_point` |
| created_at | DateTime | Provisioning time |
| updated_at | DateTime | Auto-updated |

Constraints and relationships:

- `uq_outlet_owner_channel` — unique `(owner_id, channel)`, so two outlets owned
  by the same account cannot share a channel number.
- `SmartOutlet.owner` ↔ `User.outlets`.
- `SmartOutlet.events` → `DeviceEvent` with `cascade="all, delete-orphan"`.

Security rules encoded in this table:

- Permanent identity (`device_uid`, `device_type`) is separate from mutable
  fields (`outlet_name`, `owner_id`, `channel`).
- Only a one-way hash of the pairing code is stored.
- Registration clears `pairing_token_hash`, so a label is single-use.
- A row may exist unowned (`owner_id IS NULL`) before anyone pairs it.
- Registration never sets `connection_status`, `last_seen_at`, `relay_state`,
  `ip_address`, `hardware_connected`, `network_mode`, or `connection_type`.
- Only the `X-Device-Key` authenticated heartbeat endpoint, for an
  already-registered Device ID, can set the network and relay fields; unknown
  IDs are rejected, not auto-created.
- A device is displayed online only while `last_seen_at` is within
  `DEVICE_HEARTBEAT_TIMEOUT_SECONDS` and the last reported mode is not
  `offline`; registration alone never implies online.

Display helpers (`display_name`, `relay_label`, `connection_label`,
`connection_tone`, `pairing_label`, `hardware_label`, `network_mode_label`,
`connection_type_label`, `last_seen_label`) derive
labels from stored state and never invent readings.

### device_events

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| outlet_id | Integer FK → `smart_outlets.id` (`ON DELETE CASCADE`), indexed, not null | |
| event_type | String(40), indexed | `provisioned`, `registered`, `revoked` |
| description | String(255) | Short human-readable text, no pairing secrets |
| created_at | DateTime, indexed | Event time |

Only events that actually happened are recorded. No event implies telemetry
until a future hardware stage writes one.

### admin_actions

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| actor_id | Integer FK → `users.id` (`ON DELETE SET NULL`), indexed, nullable | Kept readable after account deletion |
| actor_name | String(80) | Username snapshot |
| action | String(40), indexed | e.g. `outlet.provisioned`, `user.role_changed` |
| target | String(120) | Device ID or username |
| description | String(255) | Short note, never a password or pairing code |
| created_at | DateTime, indexed | UTC operation time |

This table is append-only from the admin service. It records authority changes,
not telemetry. No pairing code, password, or session token is stored here.

## Planned tables

### energy_readings

| Column | Notes |
|--------|-------|
| outlet_id | Foreign key → `smart_outlets.id` |
| voltage, current | Electrical measurements |
| power_watts | Calculated or measured power |
| energy_kwh | Accumulated energy |
| power_factor | Optional, hardware-dependent |
| recorded_at | Measurement timestamp |

Rule: never store simulated values as real measurements.

### automation_schedules

| Column | Notes |
|--------|-------|
| outlet_id | Foreign key → `smart_outlets.id` |
| name | Schedule name |
| start_time, end_time | Daily window |
| enabled | On/off switch |
| days / recurrence | Applicable weekdays |

The schedule engine will live in `app/services/`, not in the model.

## Database management rules

- Tables are created through Flask-Migrate (`flask db upgrade`), never by hand.
- Do not run `flask db init` again; `migrations/` already exists.
- The SQLite file lives in `instance/` and is git-ignored.
- The data layer uses SQLAlchemy only, so switching to PostgreSQL later mainly
  means changing `SQLALCHEMY_DATABASE_URI`.

```powershell
.\\.venv\\Scripts\\python.exe -m flask --app run.py db upgrade
.\\.venv\\Scripts\\python.exe -m flask --app run.py db migrate -m "describe the change"
```
