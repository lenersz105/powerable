# PowerAble — API Examples

**Version:** 1.0
**Date:** 2026-11-19

OpenAPI-like request/response examples for all PowerAble backend endpoints.

---

## 1. User Authentication

### 1.1 User Registration

```
POST /auth/register
Content-Type: application/x-www-form-urlencoded

username=newuser
password=securepassword123
confirm_password=securepassword123
csrf_token=<token>
```

**Response (302 Redirect to /dashboard):**
Redirects to dashboard on success.

### 1.2 User Login

```
POST /auth/login
Content-Type: application/x-www-form-urlencoded

username=newuser
password=securepassword123
csrf_token=<token>
```

**Response (302 Redirect to /dashboard):**
Redirects to dashboard on success.

### 1.3 User Logout

```
POST /auth/logout
Content-Type: application/x-www-form-urlencoded

csrf_token=<token>
```

**Response (302 Redirect to /):**
Redirects to home page.

---

## 2. QR Pairing

### 2.1 Validate QR Code

```
POST /api/pairing/validate
Content-Type: application/json
X-CSRF-Token: <token>
Cookie: session=<session_cookie>

{
  "payload": "PA-ESP32-A8F291C42D10:ABC123DEF456"
}
```

**Response (200 OK):**
```json
{
  "ok": true,
  "can_register": true,
  "device": {
    "device_id": "PA-ESP32-A8F291C42D10",
    "device_type": "Smart Power Outlet",
    "name": "Outlet 1",
    "registration_status": "Available",
    "pairing_state": "available"
  }
}
```

### 2.2 Register (Claim) Outlet

```
POST /api/pairing/register
Content-Type: application/json
X-CSRF-Token: <token>
Cookie: session=<session_cookie>

{
  "payload": "PA-ESP32-A8F291C42D10:ABC123DEF456"
}
```

**Response (201 Created):**
```json
{
  "ok": true,
  "message": "Smart outlet registered.",
  "device": {
    "device_id": "PA-ESP32-A8F291C42D10",
    "name": "Outlet 1",
    "registered": true,
    "connection_status": "unknown"
  }
}
```

---

## 3. Device Management

### 3.1 List User's Devices

```
GET /api/devices
Cookie: session=<session_cookie>
```

**Response (200 OK):**
```json
{
  "ok": true,
  "devices": [
    {
      "device_id": "PA-ESP32-A8F291C42D10",
      "device_type": "Smart Power Outlet",
      "name": "Living Room",
      "channel": 1,
      "registered": true,
      "online": true,
      "connection_status": "online",
      "socket_count": 3,
      "sockets": {
        "1": {"relay_state": "on", "is_on": true, "is_off": false, "is_unknown": false},
        "2": {"relay_state": "off", "is_on": false, "is_off": true, "is_unknown": false},
        "3": {"relay_state": "off", "is_on": false, "is_off": true, "is_unknown": false}
      },
      "hardware_esp32_id": "PA-ESP32-A8F291C42D10",
      "network_esp32_id": "PA-NET-12345678",
      "model": "PowerAble-3S",
      "last_seen_at": "2026-11-19T14:30:00.000000"
    }
  ]
}
```

### 3.2 Get Single Device

```
GET /api/devices/PA-ESP32-A8F291C42D10
Cookie: session=<session_cookie>
```

**Response (200 OK):**
```json
{
  "ok": true,
  "device": {
    "device_id": "PA-ESP32-A8F291C42D10",
    "name": "Living Room",
    "online": true,
    "socket_count": 3,
    "sockets": {
      "1": {"relay_state": "on", "is_on": true},
      "2": {"relay_state": "off", "is_off": true},
      "3": {"relay_state": "off", "is_off": true}
    }
  }
}
```

---

## 4. Relay Control

### 4.1 Issue Relay Command

```
POST /api/device/PA-ESP32-A8F291C42D10/relay
Content-Type: application/json
X-CSRF-Token: <token>
Cookie: session=<session_cookie>

{
  "socket_number": 2,
  "desired_state": "on"
}
```

**Response (200 OK):**
```json
{
  "ok": true,
  "message": "Relay command queued for socket 2.",
  "command": {
    "command_id": "rc-abc123def456ghi789",
    "outlet_id": 1,
    "socket_number": 2,
    "desired_state": "on",
    "status": "pending",
    "acknowledged_at": null,
    "expires_at": "2026-11-19T14:35:00.000000",
    "created_at": "2026-11-19T14:33:00.000000"
  }
}
```

**Error (409 Conflict — outlet offline):**
```json
{
  "ok": false,
  "error": "outlet_offline",
  "message": "The outlet is not online. Commands can only be sent to online devices."
}
```

**Error (409 Conflict — duplicate command):**
```json
{
  "ok": false,
  "error": "duplicate_command",
  "message": "A command for this socket is already pending. Wait for it to complete."
}
```

### 4.2 ESP32 Polls for Commands

```
GET /api/device/commands?device_id=PA-ESP32-A8F291C42D10
X-Device-Key: <per-device-secret>
```

**Response (200 OK):**
```json
{
  "ok": true,
  "commands": [
    {
      "command_id": "rc-abc123def456ghi789",
      "outlet_id": 1,
      "socket_number": 2,
      "desired_state": "on",
      "status": "pending",
      "acknowledged_at": null,
      "expires_at": "2026-11-19T14:35:00.000000",
      "created_at": "2026-11-19T14:33:00.000000"
    }
  ],
  "count": 1
}
```

**Empty Response (200 OK):**
```json
{
  "ok": true,
  "commands": [],
  "count": 0
}
```

### 4.3 ESP32 Acknowledges Command

```
POST /api/device/commands/rc-abc123def456ghi789/ack
Content-Type: application/json
X-Device-Key: <per-device-secret>

{
  "device_id": "PA-ESP32-A8F291C42D10",
  "socket_state": "on"
}
```

**Response (200 OK):**
```json
{
  "ok": true,
  "message": "Command acknowledged.",
  "command": {
    "command_id": "rc-abc123def456ghi789",
    "outlet_id": 1,
    "socket_number": 2,
    "desired_state": "on",
    "status": "completed",
    "acknowledged_at": "2026-11-19T14:33:05.000000",
    "expires_at": "2026-11-19T14:35:00.000000",
    "created_at": "2026-11-19T14:33:00.000000"
  }
}
```

**Error (409 Conflict — already processed):**
```json
{
  "ok": false,
  "error": "already_processed",
  "message": "Command has already been processed."
}
```

**Error (410 Gone — expired):**
```json
{
  "ok": false,
  "error": "command_expired",
  "message": "Command has expired."
}
```

---

## 5. Energy Telemetry

### 5.1 ESP32 Sends Telemetry

```
POST /api/device/telemetry
Content-Type: application/json
X-Device-Key: <per-device-secret>

{
  "device_id": "PA-ESP32-A8F291C42D10",
  "socket_number": 1,
  "voltage": 234.5,
  "current": 1.23,
  "power_watts": 288.4,
  "energy_kwh": 12.345,
  "frequency": 50.0,
  "power_factor": 0.95
}
```

**Response (200 OK):**
```json
{
  "ok": true,
  "message": "Telemetry received.",
  "reading": {
    "outlet_id": 1,
    "socket_number": 1,
    "voltage": 234.5,
    "current": 1.23,
    "power_watts": 288.4,
    "energy_kwh": 12.345,
    "frequency": 50.0,
    "power_factor": 0.95,
    "recorded_at": "2026-11-19T14:33:10.000000"
  }
}
```

**Error (400 Bad Request — out of range):**
```json
{
  "ok": false,
  "error": "out_of_range",
  "message": "voltage is out of range (0-500V)."
}
```

### 5.2 Dashboard Fetches Chart Data

```
GET /api/monitoring/readings?device_id=PA-ESP32-A8F291C42D10&socket_number=1&hours=24&interval=5
Cookie: session=<session_cookie>
```

**Response (200 OK):**
```json
{
  "ok": true,
  "chart": [
    {
      "timestamp": "2026-11-19T14:00:00",
      "voltage": 234.2,
      "current": 1.20,
      "power_watts": 281.0,
      "energy_kwh": 12.300,
      "frequency": 50.0,
      "power_factor": 0.95
    }
  ],
  "latest": {
    "outlet_id": 1,
    "socket_number": 1,
    "voltage": 234.5,
    "current": 1.23,
    "power_watts": 288.4,
    "energy_kwh": 12.345,
    "frequency": 50.0,
    "power_factor": 0.95,
    "recorded_at": "2026-11-19T14:33:10.000000"
  },
  "socket_number": 1
}
```

---

## 6. Wi-Fi Setup

### 6.1 Configure Wi-Fi Credentials

```
POST /api/device/PA-ESP32-A8F291C42D10/wifi-setup
Content-Type: application/json
X-CSRF-Token: <token>
Cookie: session=<session_cookie>

{
  "ssid": "MyHomeNetwork",
  "password": "super-secret-password"
}
```

**Response (200 OK):**
```json
{
  "ok": true,
  "message": "Wi-Fi credentials saved.",
  "wifi": {
    "configured": true,
    "ssid": "MyHomeNetwork"
  }
}
```

**Error (400 Bad Request — missing SSID):**
```json
{
  "ok": false,
  "error": "missing_ssid",
  "message": "SSID is required."
}
```

---

## 7. Device Heartbeat

### 7.1 ESP32 Sends Heartbeat

```
POST /api/device/heartbeat
Content-Type: application/json
X-Device-Key: <per-device-secret>

{
  "device_id": "PA-ESP32-A8F291C42D10",
  "network_mode": "online",
  "connection_type": "router",
  "ip_address": "192.168.1.120",
  "hardware_status": "CONNECTED",
  "relay_state": "ON",
  "relay_states": {
    "1": "on",
    "2": "off",
    "3": "off"
  }
}
```

**Response (200 OK):**
```json
{
  "ok": true,
  "message": "Heartbeat accepted.",
  "device": {
    "device_id": "PA-ESP32-A8F291C42D10",
    "online": true,
    "connection_status": "online",
    "socket_count": 3,
    "sockets": {
      "1": {"relay_state": "on", "is_on": true},
      "2": {"relay_state": "off", "is_off": true},
      "3": {"relay_state": "off", "is_off": true}
    }
  }
}
```

---

## 8. Admin Console

### 8.1 Dashboard Stats

```
GET /admin/
Cookie: session=<session_cookie>
```

**Response (200 HTML):**
Renders admin dashboard with stats.

### 8.2 Provision New Outlet

```
POST /admin/outlets/new
Content-Type: application/x-www-form-urlencoded
Cookie: session=<session_cookie>

device_id=PA-ESP32-A8F291C42D10
device_type=Smart+Power+Outlet
hardware_esp32_id=PA-ESP32-A8F291C42D10
network_esp32_id=PA-NET-12345678
model=PowerAble-3S
socket_count=3
csrf_token=<token>
```

**Response (200 HTML):**
Renders the pairing label page with QR code and device secret.

---

## 9. Authentication Notes

### 9.1 Browser Requests (User-facing)
- **Authentication:** Session cookie (`session=<cookie>`)
- **CSRF Protection:** `X-CSRF-Token` header or `csrf_token` form field
- **Endpoints:** `/api/pairing/*`, `/api/devices`, `/api/device/<id>/relay`, `/api/device/<id>/wifi-setup`, `/api/monitoring/*`

### 9.2 Device Requests (ESP32-to-server)
- **Authentication:** `X-Device-Key` header with per-device secret
- **No CSRF:** Machine-to-machine, no browser session
- **Fallback:** Shared `DEVICE_API_KEY` accepted for legacy devices without per-device secret
- **Endpoints:** `/api/device/heartbeat`, `/api/device/commands`, `/api/device/commands/<id>/ack`, `/api/device/telemetry`

### 9.3 Admin Requests
- **Authentication:** Session cookie + admin role check
- **CSRF Protection:** Required for all POST requests
- **Endpoints:** `/admin/*`

---

## 10. Error Response Format

All API errors follow a consistent format:

```json
{
  "ok": false,
  "error": "<error_code>",
  "message": "Human-readable error message."
}
```

Common error codes:
- `missing_device_key` — 401: X-Device-Key header missing
- `invalid_device_key` — 403: Invalid device authentication
- `device_key_not_configured` — 503: Server not configured
- `not_found` — 404: Resource not found or not owned
- `invalid_request` — 400: Malformed request
- `outlet_offline` — 409: Device not online
- `duplicate_command` — 409: Command already pending
- `already_processed` — 409: Command already acknowledged
- `command_expired` — 410: Command TTL exceeded
- `out_of_range` — 400: Sensor value out of valid range
