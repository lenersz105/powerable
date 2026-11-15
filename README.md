# PowerAble — Web-Based Smart Power Outlet

Thesis project. A local-first Flask web application for registering, monitoring,
and eventually automating smart power outlets. It runs on your own computer and
local network by default, with no cloud dependency. An optional production path
for Render (Gunicorn + Postgres) is included for remote access.

**Current state:** the interface, accounts, secure QR pairing, Device-ID
self-registration with LAN QR codes, key-authenticated ESP32 heartbeats, owner-scoped
device APIs, and the admin console work. Heartbeats update the reported IP, relay
state, hardware-controller status, and last communication time. Electrical
measurements are still zero placeholders explicitly marked “awaiting data”.

## Tech stack

- Python 3 · Flask · SQLite · SQLAlchemy · Flask-Migrate · Flask-Login
- Gunicorn · Render Postgres (optional deployment)
- HTML5 · CSS3 · vanilla JavaScript
- Segno (installer QR labels) · qrcode + Pillow (registration QR images)
- cryptography (local HTTPS helper)
- PyCharm (development environment)

## Project structure

```
PowerAble/
├── app/
│   ├── __init__.py       # Application factory
│   ├── config.py         # Settings per environment
│   ├── extensions.py     # Shared db / migrate / login objects
│   ├── security.py       # Session CSRF, disabled-account block, error handling
│   ├── permissions.py    # login_required + admin role guard
│   ├── cli.py            # Installer provisioning and create-admin commands
│   ├── models/           # Database tables (users, outlets, events, audit)
│   ├── routes/           # URL blueprints (pages, JSON API, admin console)
│   ├── services/         # Pairing, QR, admin, and device business rules
│   ├── templates/        # Jinja2 pages (including admin/)
│   └── static/           # CSS, JavaScript, vendored jsQR, icons
├── instance/             # SQLite database, QR labels, TLS certs (git-ignored)
├── migrations/           # Flask-Migrate schema history
├── docs/                 # Architecture, admin, database, pairing, hardware docs
├── scripts/
│   ├── serve_https.py    # Self-signed local HTTPS for phone cameras
│   └── render_start.sh   # Render: migrate, bootstrap admin, start Gunicorn
├── tests/                # pytest suite (auth, CSRF, pairing, admin, pages)
├── run.py                # Local development entry point
├── wsgi.py               # Production WSGI entry point (Gunicorn)
├── render.yaml           # Render Blueprint (web service + Postgres)
├── requirements.txt
├── requirements-dev.txt
└── .env.example
```

## Setup on Windows

### 1. Open the project in PyCharm

Open this folder as a project. PyCharm should use the `.venv` virtual
environment already inside it.

### 2. Install dependencies

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Use `requirements.txt` instead if you do not need pytest.

### 3. Create your local environment file

Copy `.env.example` to `.env`, then replace the placeholder `SECRET_KEY` with a
random value:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

`.env` is git-ignored.

### 4. Apply the database migrations

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py db upgrade
```

Do **not** run `flask db init` again; `migrations/` already exists. The current
head creates `users`, `smart_outlets`, `device_events`, and the admin audit table
`admin_actions`, adds `users.role`, adds the outlet `ip_address` and
`hardware_connected` columns (migration `b7c2e5f1a9d4`), and adds
`network_mode` and `connection_type` (migration `c8f4b2d6a1e3`).

For future model changes:

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py db migrate -m "describe the change"
.\.venv\Scripts\python.exe -m flask --app run.py db upgrade
```

### 5. Run the application

Start the server:

```powershell
.\.venv\Scripts\python.exe run.py
```

`run.py` binds to `0.0.0.0:5000` by default so phones and ESP32 boards on the
same Wi-Fi can reach it. On this computer open <http://127.0.0.1:5000/>; on a
phone or ESP32 use this computer's LAN address, for example
<http://192.168.1.10:5000/>. The Live Dashboard shows the detected LAN address.

`run.py` honours `HOST`, `PORT`, and `FLASK_DEBUG`. Set `HOST=127.0.0.1` if you
want plain HTTP restricted to this computer.

Registration QR codes need a LAN address. PowerAble auto-detects one; if the
detected address is wrong, set it explicitly in `.env`:

```text
PUBLIC_BASE_URL=http://192.168.1.10:5000
```

If the phone opens the registration link but the camera-based pairing scanner is
needed instead, use the HTTPS helper below. The self-registration QR only opens
a normal web page and does not require the camera.

### 6. Run over local HTTPS for the phone camera

Mobile browsers only allow `getUserMedia` on HTTPS or `localhost`. Use the
helper:

```powershell
.\.venv\Scripts\python.exe scripts\serve_https.py
```

It prints addresses such as `https://192.168.1.20:5443/`. On the phone, accept
the one-time self-signed certificate warning. The certificate covers `localhost`
and this computer's current LAN IPv4 addresses and is stored in
`instance/certs/` (git-ignored). Keep this on a trusted local network only.

If the camera is unavailable, the pairing page offers manual entry of the device
ID and pairing code over plain HTTP.

### 7. One-key start in PyCharm (F5)

Two shared run configurations are included in `.run/`:

- **PowerAble - Local HTTP** — starts `run.py` on this computer.
- **PowerAble - Phone HTTPS (camera)** — starts `scripts/serve_https.py` for the
  phone camera.

To start either one with a single key:

1. Open **Run → Edit Configurations** once and confirm both entries are listed.
2. Open **Settings → Keymap** (`Ctrl+Alt+S`, then search for `Keymap`).
3. Search for the exact action `Run 'PowerAble - Local HTTP'`.
4. Right-click it → **Add Keyboard Shortcut** → press **F5** → **OK**.
5. If PyCharm warns that F5 is already used (by *Copy* in the default keymap),
   confirm the reassignment.

You can then pick the configuration in the toolbar dropdown and press **F5** to
start or restart it. Bind the phone configuration to a second key (for example
`Shift+F5`) if you want both available at once.

Note: pressing F5 inside the browser only refreshes the page — it cannot start
Python. F5 has to be the PyCharm shortcut. The HTTP configuration runs with the
debug reloader, so Python edits restart it automatically; the HTTPS helper does
not auto-reload, so press F5 in PyCharm again after changing Python code.
Template, CSS, and JavaScript edits appear on a normal browser refresh.

### 8. Create the first administrator

Admin accounts are created only from the local installer CLI — never from the
public “Create account” form:

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py create-admin --username installer
```

The command prompts for a password without echo, stores only its hash, and writes
an `admin.created` audit entry. Running it again for an existing username
promotes that account to admin and replaces its password. Then sign in and open
<http://127.0.0.1:5000/admin/>.

From there an admin can generate/rotate/print QR labels, rename or revoke
outlets, delete devices with an exact-ID confirmation, change roles and account
status, reset passwords, delete accounts, and review the audit log. Admins
cannot change their own role/status or delete themselves, and the last active
admin is protected. See `docs/admin-console.md`.

For an admin, the five-item mobile dock replaces **Schedules** with **Admin**;
Scheduling is still available from the main navigation.

## Deploy on Render (optional)

Local development keeps using SQLite and `run.py`. The repository also includes
a Render Blueprint for a public HTTPS deployment:

- `render.yaml` — free web service + free Postgres by default;
- `wsgi.py` — production application factory;
- `scripts/render_start.sh` — runs migrations, bootstraps the first admin, then
  starts Gunicorn.

Quick start:

1. Push the repository to GitHub, GitLab, or Bitbucket.
2. In Render, choose **New → Blueprint** and connect the repository.
3. Enter `DEVICE_API_KEY` (paste the existing key to keep current firmware
   working) and optionally `ADMIN_USERNAME` / `ADMIN_PASSWORD`.
4. Apply the Blueprint and open the generated `https://….onrender.com` URL.

The production configuration refuses to start without a strong `SECRET_KEY` and
a persistent `DATABASE_URL`. QR codes automatically use Render's public URL, and
cookies are HTTPS-only.

Free Render services sleep after inactivity, have no Shell access, and free
Postgres expires after 30 days. See `docs/render-deployment.md` for the full
guide, paid persistent-disk SQLite option, secret rotation, and troubleshooting.

## Registering by Device ID and QR

1. Sign in or create an account.
2. Open **Add Smart Outlet** (`/devices/add`).
3. Enter the ID printed by the ESP32 Network Controller Serial Monitor, for
   example `PA-ESP32-A8F291C42D10`. The format is checked on the server.
4. Choose **Generate QR code**. PowerAble builds a URL such as
   `http://192.168.1.10:5000/register?device_id=PA-ESP32-A8F291C42D10`, shows a
   preview, and offers a PNG download.
5. Scan the code with a phone on the same Wi-Fi, or choose **Register this
   outlet now** on the computer. The outlet is linked to the signed-in account
   and named `SmartOutlet 1`, `SmartOutlet 2`, and so on.
6. Opening the same link again shows “already registered” instead of creating a
   duplicate.

The `PUBLIC_BASE_URL` setting overrides automatic LAN detection. QR links refuse
`localhost` and `127.0.0.1`, because a phone cannot reach those addresses on the
computer.

### Device API key

The ESP32 Network Handler authenticates with a shared secret. Configure it in
`app/config.py` through the `DEVICE_API_KEY` environment variable. The
development fallback is `PowerAble_Network_2026_Rey_X82k9P`; production has no
fallback and refuses device traffic until the variable is set.

Windows PowerShell (current session):

```powershell
$env:DEVICE_API_KEY = "PowerAble_Network_2026_Rey_X82k9P"
```

Persist it for new terminals:

```powershell
[System.Environment]::SetEnvironmentVariable(
  "DEVICE_API_KEY",
  "PowerAble_Network_2026_Rey_X82k9P",
  "User"
)
```

Every device request carries the exact header:

```http
X-Device-Key: PowerAble_Network_2026_Rey_X82k9P
```

Missing header → `401`; wrong key → `403`. The key is never written to the
dashboard, JavaScript, API responses, or logs.

### ESP32 heartbeat

Once registered, the network controller posts its state to
`/api/device/heartbeat`:

```http
POST /api/device/heartbeat
Content-Type: application/json
X-Device-Key: PowerAble_Network_2026_Rey_X82k9P

{
  "device_id": "PA-NET-12345678",
  "network_mode": "online",
  "connection_type": "router",
  "ip_address": "192.168.1.120",
  "hardware_status": "CONNECTED",
  "relay_state": "OFF"
}
```

`PA-NET-12345678` and the longer `PA-ESP32-A8F291C42D10` IDs are both accepted
by registration and heartbeat. Unknown or unregistered IDs are rejected; the
endpoint never auto-registers a device or creates a duplicate row.

Supported values:

- `network_mode`: `online` or `offline`.
- `connection_type`: `router` or `access_point`.
- `hardware_status`: `CONNECTED`, `DISCONNECTED`, or `UNKNOWN`.
- `relay_state`: `ON`, `OFF`, or `UNKNOWN`.

The earlier fields `outlet_state` (boolean) and `hardware_connected` (boolean)
still work. An `offline` / `access_point` heartbeat still updates
`last_seen_at`, but the dashboard shows the outlet as offline.
`DEVICE_HEARTBEAT_TIMEOUT_SECONDS` (default 60) marks a silent device offline.

## Pairing an outlet

1. Sign in or create an account.
2. Provision a physical outlet once, as the installer:

   ```powershell
   .\.venv\Scripts\python.exe -m flask --app run.py provision-outlet --device-id PA-ESP32-0001
   ```

   The command prints the pairing code once, stores only a scrypt hash, and
   writes a QR image to `instance/qr/PA-ESP32-0001.png`.

   An admin can do the same online from **Admin → Outlets & labels → Generate QR
   label**. That page renders a printable label and shows the plaintext code
   exactly once, with `Cache-Control: no-store`.

3. On the phone, open `/devices/pair` over HTTPS (or `localhost`) and tap
   **Start camera**.
4. Review the confirmation sheet and tap **Register This Outlet**.
5. The outlet appears on Home, My Outlets, and Scheduling. It stays
   “Waiting for first reading” until the ESP32 sends a heartbeat or real
   telemetry.

Useful installer commands:

```powershell
.\.venv\Scripts\python.exe -m flask --app run.py list-outlets
.\.venv\Scripts\python.exe -m flask --app run.py revoke-outlet --device-id PA-ESP32-0001 --yes
```

Pairing codes are single-use. Registering does not mark a device online and
does not send Wi-Fi credentials.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite uses an isolated in-memory database and covers registration, login,
CSRF enforcement, valid/revoked/claimed pairing, single-use codes,
ownership isolation, malformed payloads, throttling, outlet limits, admin
access control, QR-label generation with hash-only storage, outlet lifecycle,
account role/status/password changes, self/last-admin guards, disabled-session
enforcement, audit records, Device-ID validation, duplicate prevention, LAN QR
URL generation, PNG download, device API key authentication, online/offline
heartbeats, duplicate-free heartbeat updates, heartbeat validation, first-admin
bootstrap, dashboard device details, and the rendered pages.

## What works today

- Liquid Clear Glass, mobile-first interface with safe-area spacing and a fixed
  mobile dock
- Registration, login, logout, hashed passwords, session CSRF, login throttling
- Installer CLI: provision, list, revoke, create/promote admin
- Admin console: overview, printable one-time QR labels, rotate/rename/revoke/
  delete outlets, role/status/password/account management, audit log
- Disabled accounts are blocked at login and signed out of existing sessions
- Strict line-based QR payload parsing and validation
- Real rear-camera scanner with native `BarcodeDetector`, vendored jsQR fallback,
  torch where supported, camera switching where supported, stop/release
- Manual device-ID + pairing-code fallback
- Explicit confirmation before registration; single-use transaction claim
- Owner-scoped device list and device detail APIs
- Device-ID self-registration with LAN QR preview/download, duplicate rejection,
  and automatic `SmartOutlet N` naming
- `X-Device-Key` authenticated ESP32 heartbeat updating IP, network mode,
  connection type, relay, hardware-controller status, last communication, and
  online/offline display
- Optional Render deployment: Gunicorn, managed Postgres, automatic migrations,
  first-admin bootstrap, HTTPS cookies, and public QR links
- Honest empty states: no fabricated readings, classifications, or online states

## Remaining work

- ESP32-S3 + PZEM-004T telemetry and relay confirmation (hardware)
- Wi-Fi provisioning as a separate, deliberate workflow (not implemented)
- Heartbeat is implemented; PZEM telemetry ingestion, relay command APIs, and
  live polling remain (Stage 3–4)
- Saved schedules, energy history, and safe auto cutoff (Stage 5)
- Appliance classification (TinyML) and final thesis documentation

## Security notes

- Never commit `.env`, `instance/`, QR labels, or generated TLS keys.
- Passwords and pairing codes are stored only as one-way hashes.
- QR labels contain a public device ID and a single-use pairing code — never a
  Wi-Fi password or permanent device secret.
- All browser state-changing requests require an authenticated session and a
  CSRF token. Device endpoints under `/api/device/` are CSRF-exempt because
  hardware has no browser session; they require the `X-Device-Key` header and
  are limited to already-registered Device IDs.
- Admin routes require the explicit `admin` role on the server. Admins cannot
  change their own role/status or delete themselves, and the last active admin is
  protected. Administrative and account changes are audited without secrets.
- The plaintext pairing code appears once in the label response; it is never put
  in the session, audit log, or a stored QR file by the web console.
- A disabled account is signed out on its next request; API calls receive
  `account_disabled`.
- Device reads are scoped to the signed-in owner; other accounts receive a
  generic “not found”.
- A device is shown as online only after a recent heartbeat. Registration alone
  never implies connection.
- The heartbeat endpoint is authenticated with the shared `X-Device-Key`
  secret, compared in constant time. It only updates Device IDs that are
  already registered; unknown IDs are rejected. For stronger per-device
  authentication, add a unique secret or signed heartbeat in a future firmware
  revision.
- Production refuses to start without a strong `SECRET_KEY` and a persistent
  `DATABASE_URL`. Render sessions use HTTPS-only cookies and trust one proxy hop
  for the public scheme and host.

See `docs/pairing-security.md` for the pairing threat model,
`docs/admin-console.md` for administrator authority and guardrails,
`docs/render-deployment.md` for the Render deployment guide, and
`docs/architecture.md` for the development roadmap.
