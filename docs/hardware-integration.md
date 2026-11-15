# PowerAble — Hardware Integration

## Status

| Area | State |
|------|-------|
| Outlet provisioning (installer CLI) | Implemented |
| QR label generation | Implemented (local, `instance/qr/`) |
| Account-bound pairing and ownership | Implemented |
| Device-ID self-registration + LAN QR | Implemented (`/devices/add`, `/register`) |
| Device-key authenticated ESP32 heartbeat | Implemented (`POST /api/device/heartbeat`) |
| ESP32-S3 + PZEM-004T telemetry | **Not implemented** |
| Relay command delivery and confirmation | **Not implemented** |
| Wi-Fi provisioning for the ESP32 | **Not implemented — separate future workflow** |

Registration, IP reporting, relay state, hardware-controller status, and last
communication are real and tested. Electrical measurements are still not
implemented, so voltage, current, power, and energy stay `0.00` and are marked
“awaiting data”. Heartbeats do not manufacture readings.

## Planned device: ESP32-S3 smart outlet

ESP32-S3 with a PZEM-004T energy sensor and a relay, driving one outlet channel.

## Workflow: what is done and what remains

1. A user registers and logs into PowerAble. — **Done**
2. The user enters the ESP32 Device ID at `/devices/add`, or the installer
   provisions the device and prints a QR label. — **Done**
3. Flask validates the scanned/linked ID, prevents duplicates, assigns a name,
   and links the outlet to the signed-in account. — **Done**
4. The user configures the outlet's Wi-Fi through a separate secure
   provisioning workflow (e.g. access-point captive portal on the ESP32).
   — **Not implemented**
5. The ESP32 joins the local network and sends
   `POST /api/device/heartbeat`. — **Heartbeat implemented; firmware pending**
6. The backend receives authenticated device status and energy readings.
   — Heartbeat status updates are implemented; energy telemetry is **not**.
7. Authorized users can control only their own outlets. — Ownership rules are
   enforced; relay command delivery is still pending.

Pairing or self-registering a device does **not** configure Wi-Fi and does
**not** put it online. The outlet is shown online only after a heartbeat
arrives, and only while the heartbeat is fresh.

## QR label contents

The label is generated locally by `flask --app run.py provision-outlet`, or by an
admin at `/admin/outlets/new`. Both use the same payload rules:

```
POWERABLE:PAIR
VERSION:1
DEVICE_ID:PA-ESP32-0001
PAIRING_CODE:<24-char base32>
```

- Only the public device ID and a single-use pairing code.
- No Wi-Fi password, no account data, no permanent device secret.
- The code is stored only as a scrypt hash and stops working after use.
- The CLI writes the QR image to `instance/qr/<device_id>.png` (git-ignored).
  The admin console renders it once directly in a `no-store` page instead.

See `docs/pairing-security.md` for the full contract.

## Heartbeat endpoint

The ESP32 network controller sends a JSON heartbeat over the local network:

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

- Authenticated by the shared `X-Device-Key` secret (`DEVICE_API_KEY`).
  Missing header → `401`; wrong key → `403`; unconfigured server → `503`.
- CSRF-exempt because hardware has no browser session. The key check lives in
  `app/device_auth.py`; CSRF exemption does not bypass it.
- The `device_id` must already be registered from the website. Both
  `PA-ESP32-...` and `PA-NET-...` IDs are accepted.
- Unknown or unowned IDs are rejected with `404`/`403`; the endpoint never
  auto-registers a device or creates a duplicate row.
- `network_mode` (`online`/`offline`), `connection_type`
  (`router`/`access_point`), `ip_address`, `hardware_status`
  (`CONNECTED`/`DISCONNECTED`/`UNKNOWN`), and `relay_state`
  (`ON`/`OFF`/`UNKNOWN`) update the existing `smart_outlets` row.
- The earlier boolean fields `outlet_state` and `hardware_connected` remain
  supported.
- Every accepted request sets `last_seen_at`. An `offline` heartbeat sets
  `connection_status="offline"` even though the device just contacted the
  server, so access-point fallback is not shown as online. Silence longer than
  `DEVICE_HEARTBEAT_TIMEOUT_SECONDS` also marks the device offline.

## Communication: MQTT over TLS (later phase)

- Device messages (status, readings, commands) will travel over MQTT, not
  through normal web routes.
- Device communication code will live in its own module, kept separate from
  blueprints and models.
- TLS is required so credentials and data are encrypted in transit.

Nothing in this section is implemented yet.

## Hard security rules

- **Never** place Wi-Fi passwords or permanent device credentials inside QR
  codes.
- QR codes carry only a device identifier and a single-use pairing code.
- Every device message must be authenticated with unique per-device credentials.
- A device is only shown as “online” after a verified message has been received.
- Registration alone never implies connection; `last_seen_at` stays `NULL`
  until real telemetry arrives.
- Wi-Fi provisioning is never mixed into the pairing request.

## Separation of concerns

```
installer CLI ──> services/qr_service + device_service ──> smart_outlets

ESP32 ──MQTT/TLS──> device bridge (future module)
                        │
                        ▼
                   services + models
                        │
                        ▼
                   routes → dashboard
```

Routes never talk to hardware directly. Services translate device messages into
database records. The dashboard only reads the database.

## What the backend must add before hardware can connect

- A telemetry ingestion endpoint or MQTT bridge with per-device authentication
  for PZEM-004T voltage, current, power, power factor, and energy samples.
- An `energy_readings` write path fed only by verified samples.
- Relay command endpoints with acknowledgement, plus `DeviceEvent` entries for
  real state changes.
- The separate Wi-Fi provisioning workflow for the ESP32.
- Optional hardening: a per-device shared secret or signed heartbeat in addition
  to the currently registered Device ID check.
