# PowerAble — Pairing and Security Design

## Goals

- Let an authenticated person claim a physical outlet without exposing permanent
  secrets in a printed label.
- Reject invalid, revoked, malformed, and already-claimed labels on the server,
  not only in JavaScript.
- Keep each account's outlets private.
- Never imply that a registered outlet is online or reporting.

## QR label format

The label is plain text with a fixed header and three required keys:

```
POWERABLE:PAIR
VERSION:1
DEVICE_ID:PA-ESP32-0001
PAIRING_CODE:ABCDEFGHIJKLMNOPQRSTUVWX
```

Rules enforced by `app/services/qr_service.py`:

- The header must be the first non-empty line.
- Unknown keys, duplicate keys, missing keys, and extra non-key lines are
  rejected.
- Only `VERSION:1` is accepted.
- Device IDs are uppercased and must match `^[A-Z0-9][A-Z0-9-]{2,63}$`.
- Pairing codes are uppercased, spaces and dashes are removed, and the result
  must match `^[A-Z2-7]{16,32}$`.
- Payloads longer than 512 bytes are rejected.

The label never contains a Wi-Fi password, account data, a permanent device
secret, or a URL. Wi-Fi provisioning is a separate future workflow.

## Pairing-code lifecycle

1. The installer runs `flask --app run.py provision-outlet --device-id ...`, or
   an admin generates the label from `/admin/outlets/new`.
2. The server creates or rotates a `SmartOutlet` row in state `available`.
3. A 120-bit random base32 code (24 characters) is generated.
4. Only a Werkzeug scrypt hash of the code is stored in
   `smart_outlets.pairing_token_hash`.
5. The plaintext code is shown once: the CLI writes a QR image under
   `instance/qr/` (git-ignored), while the admin console renders the code and
   inline QR into a `Cache-Control: no-store` response and never stores them.
6. A successful registration clears the hash in the same transaction and sets
   the state to `registered`.

Re-provisioning an unregistered outlet rotates the code. A registered outlet must
be revoked before a new label is issued.

## API contract

All endpoints return JSON and set `Cache-Control: no-store`.

### `POST /api/pairing/validate`

Authentication: signed-in session + `X-CSRF-Token` header.

Request body, either:

```json
{"payload": "POWERABLE:PAIR\nVERSION:1\n..."}
```

or manual credentials:

```json
{"device_id": "PA-ESP32-0001", "pairing_code": "ABCD-EFGH-IJKL-MNOP"}
```

Success `200`:

```json
{
  "ok": true,
  "can_register": true,
  "device": {
    "device_id": "PA-ESP32-0001",
    "device_type": "Smart Power Outlet",
    "registration_status": "Available",
    "registered": false,
    "connection_status": "unknown",
    "relay_state": "unknown",
    "readings": null
  }
}
```

Failure shape:

```json
{"ok": false, "error": "invalid_code", "message": "The pairing code is not valid for this outlet."}
```

| Status | `error` | Meaning |
|--------|---------|---------|
| 400 | `invalid_csrf` | Missing or stale CSRF token |
| 400 | `invalid_qr` | Payload is not a valid PowerAble label |
| 400 | `invalid_code` | Code does not match the stored hash |
| 400 | `missing_credentials` | Neither payload nor both manual fields supplied |
| 401 | `authentication_required` | Not signed in |
| 403 | `revoked` | Installer disabled this outlet |
| 404 | `unknown_device` | Device ID is not provisioned |
| 409 | `already_registered` | Another account already claimed it |
| 409 | `not_available` | State is not claimable |
| 409 | `limit_reached` | Account outlet limit reached |
| 429 | `too_many_attempts` | 12 failures in 10 minutes for this session |

### `POST /api/pairing/register`

Same request body. Re-validates everything inside a conditional update:

```sql
UPDATE smart_outlets
SET owner_id = :user, pairing_state = 'registered', pairing_token_hash = NULL, ...
WHERE id = :outlet
  AND owner_id IS NULL
  AND pairing_state = 'available'
  AND pairing_token_hash IS NOT NULL;
```

If the update affects zero rows, the request is refused, so two people cannot
claim the same outlet simultaneously. Success is `201` with `registered: true`.

### `GET /api/devices` and `GET /api/devices/<device_id>`

Both require a signed-in session. They return only outlets whose `owner_id`
matches the current user. A device owned by someone else returns `404`, not
`403`, so ownership is not disclosed.

## Security properties

- **Authentication required:** pairing cannot happen anonymously.
- **CSRF protection:** every POST, including the JSON API, requires the session
  token via `X-CSRF-Token` or a `csrf_token` form field.
- **Server-side authority:** the browser only collects intent. All decisions are
  re-checked in `app/services/device_service.py`.
- **Unpredictable codes:** 120 bits from `secrets.token_bytes`.
- **One-way storage:** scrypt hashes; responses and logs never echo the code.
- **Single use:** the code is cleared on registration.
- **Revocation:** installers can disable an outlet and unlink it.
- **Admin role:** every `/admin` route requires the explicit `admin` role; the
  service layer repeats the check. Admins cannot change their own role/status or
  delete themselves, and at least one active admin must remain.
- **Audited authority:** label, outlet, role, status, password, and deletion
  actions write an `AdminAction`. Passwords and pairing codes are never logged.
- **Disabled accounts:** an existing session is ended on the next request; JSON
  API calls receive `403 account_disabled`.
- **One-time label display:** the plaintext code is rendered once with
  `Cache-Control: no-store`, not stored in the session, and not written to the
  audit log.
- **Privacy:** no endpoint reveals another account's ownership or device list.
- **Throttling:** repeated failures are limited per session.
- **No fabricated state:** registration sets `connection_status = "unknown"`,
  `last_seen_at = NULL`, and `relay_state = "unknown"`.

## Camera and transport

Phones expose the camera only on a secure origin:

- `https://...`, or
- `http://localhost` / `http://127.0.0.1`.

`scripts/serve_https.py` creates a self-signed certificate for `localhost` and
the computer's LAN IPv4 addresses and serves Flask on port 5443. The phone must
accept the certificate warning once. Plain `http://192.168.x.x:5000` works for
manual entry but cannot start the camera.

The scanner decodes frames locally with `BarcodeDetector` when available, or the
vendored `jsQR` library otherwise. No image or video is uploaded; only the
decoded text is POSTed to the local Flask server.

## Out of scope

- Wi-Fi credential provisioning for the ESP32-S3
- Telemetry ingestion and relay commands
- Self-service password reset, email verification, and multi-factor authentication
- Internet-facing deployment or public certificate management
