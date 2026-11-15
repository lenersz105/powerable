# PowerAble — Architecture Overview

## What this document is

A plain-language map of how the PowerAble backend is organized and why. Read this
before adding new features.

## Application style: Flask Application Factory

PowerAble uses the **application factory pattern**. Instead of creating the Flask
app at the top of one file, `app/__init__.py` defines `create_app()` which builds a
fresh app every time it is called.

Why this matters:

- Configuration stays isolated from the code that uses it.
- Tests can create independent app instances with their own database.
- Extensions (database, login, migrations) are initialized in one predictable
  place.

## Layer responsibilities

| Layer | Folder | Job |
|-------|--------|-----|
| Routes (blueprints) | `app/routes/` | Receive HTTP requests, call services, return HTML/JSON |
| Services | `app/services/` | Reusable business logic, no HTTP details |
| Models | `app/models/` | Database tables and relationships |
| Security | `app/security.py` | Session CSRF, disabled-account blocking, login redirects, JSON/HTML error pages |
| Permissions | `app/permissions.py` | `login_required` plus the admin role guard |
| Installer CLI | `app/cli.py` | Provision, list, revoke outlets, and create/promote admins |
| Templates | `app/templates/` | HTML pages (Jinja2) |
| Static | `app/static/` | CSS, JavaScript, vendored jsQR, icons |
| Config | `app/config.py` | Environment-specific settings |
| Extensions | `app/extensions.py` | Shared extension objects (db, migrate, login) |
| Scripts | `scripts/` | Local HTTPS helper for phone camera access |

Rule of thumb: if a function needs `request` or `render_template`, it belongs in a
route. If it is pure logic (calculations, pairing rules), it belongs in a service.

## Blueprints (URL groups)

| Blueprint | Prefix | Purpose |
|-----------|--------|---------|
| `main` | `/` | Homepage, manual, and about pages |
| `auth` | `/auth` | Registration, login, logout |
| `dashboard` | `/dashboard` | Live monitor of the user's outlets |
| `devices` | `/devices` | Outlet list and QR pairing screen |
| `monitoring` | `/monitoring` | Future energy charts and data endpoints |
| `automation` | `/automation` | Scheduling preview using real outlets |
| `api` | `/api` | JSON pairing and owner-scoped device reads |
| `admin` | `/admin` | Role-protected labels, outlets, accounts, and audit log |

## Pairing data flow

```
installer CLI or admin console
    └── provision_device() ──> SmartOutlet (available) + scrypt hash + QR label

phone scanner (local decode)
    └── POST /api/pairing/validate ──> device_service.validate_pairing()
                                          └── structure, state, hash

confirmation sheet
    └── POST /api/pairing/register ──> conditional UPDATE (single winner)
                                          └── owner_id set, code cleared, event logged
```

The browser never decides whether a code is valid. See
`docs/pairing-security.md` for the API contract and threat model, and
`docs/admin-console.md` for administrator authority.

## Development phases

1. **Website interface — complete.** Shared Liquid Clear Glass layout, Home,
   Live Dashboard, Scheduling, User Manual, About, mobile dock, and honest
   “awaiting data” placeholders.
2. **Database, accounts, and pairing — complete.** `User`, `SmartOutlet`, and
   `DeviceEvent` models; hashed passwords; CSRF-protected auth; installer CLI;
   strict QR payloads; single-use hashed pairing codes; ownership-scoped reads.
3. **Flask API and admin console — current.** Pairing validation/registration,
   device-read endpoints, and the role-protected admin console are live.
   Telemetry ingestion, relay commands, and device status updates remain.
4. **Frontend integration** — polling, real relay controls, and power trend
   charts fed only by verified samples.
5. **Scheduling and energy limits** — saved schedules, historical readings, and
   safe auto cutoff based on real accumulated energy.
6. **Hardware integration** — ESP32-S3 + PZEM-004T + relay feedback, with MQTT
   over TLS considered later. Kept separate from routes and models.

## Security checklist

Implemented now:

- Passwords hashed with Werkzeug (`generate_password_hash`).
- Pairing codes hashed with scrypt; plaintext is never stored.
- CSRF tokens on every state-changing form and JSON API request.
- Ownership checks: users may only read their own outlets.
- Admin role checked on every `/admin` route and repeated in the admin service.
- Disabled accounts are refused at login and signed out of existing sessions.
- Admin, role, status, password, and outlet lifecycle changes are audited;
  passwords and pairing codes never enter the audit table.
- The plaintext pairing code is rendered once with `Cache-Control: no-store`.
- Session rate limits for login and pairing failures.
- Secrets only in `.env` (git-ignored) — never in source code.
- Device “online” status only after verified communication; registration sets
  `unknown`.
- Same-origin, locally served assets; no runtime CDN.

Still required:

- Per-device authentication for telemetry and relay commands.
- Transport security for device messages (MQTT/TLS or equivalent).
- Wi-Fi provisioning workflow, kept separate from QR pairing.
