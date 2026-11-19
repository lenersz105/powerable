# PowerAble — Firmware Integration Contract

**Version:** 1.0
**Date:** 2026-11-19
**Purpose:** Exact API specification for the Network ESP32 firmware to integrate with the PowerAble Flask backend.

---

## 1. Overview

The Network ESP32 board handles all cloud communication for the SmartOutlet. It authenticates with the Flask backend using a per-device API secret provisioned at manufacturing, sends periodic heartbeats, polls for relay commands, acknowledges command execution, and reports energy telemetry.

### Communication Flow

```
Customer Dashboard → Flask Backend → Network ESP32 → UART → Hardware ESP32 → Physical Relay
                                                                 ← actual state ←
Network ESP32 ←────────────────────────────────────────────────────────────────────
         → Flask Backend (acknowledge) → Customer Dashboard (UI update)
```

---

## 2. Authentication

### 2.1 Per-Device Secret

Each Network ESP32 is provisioned with a unique `device_api_secret` at manufacturing. This secret is:
- A 32-byte URL-safe base64 token (e.g., `Vc5a3k9Lm2pQr7sT1xYz4bN6dF8gH0jK`)
- Stored in the ESP32 non-volatile storage (NVS or flash)
- Sent in the `X-Device-Key` header on every API request
- **Never** included in QR codes, JavaScript, or log output

### 2.2 Backward Compatibility

If the backend has not yet provisioned a per-device secret for a device, the shared `DEVICE_API_KEY` is accepted as a fallback. New firmware should always use the per-device secret.

### 2.3 Authentication Header

Every API request must include:
```
X-Device-Key: <per-device-secret>
```

---

## 3. API Endpoints

All endpoints use HTTPS in production. The base URL is configured in the ESP32 firmware (e.g., `https://powerable.on.render.com`).

### 3.1 Heartbeat

**Endpoint:** `POST /api/device/heartbeat`

**Purpose:** Report device status and per-socket relay states to the backend.

**Authentication:** `X-Device-Key` header

**Request Body:**
```json
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

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `device_id` | string | Yes | Permanent Hardware ESP32 ID or device UID |
| `network_mode` | string | No | `"online"` (router) or `"offline"` (AP fallback) |
| `connection_type` | string | No | `"router"` or `"access_point"` |
| `ip_address` | string | No | Current LAN IP address |
| `hardware_status` | string | No | `"CONNECTED"`, `"DISCONNECTED"`, or `"UNKNOWN"` |
| `relay_state` | string | No | Legacy single relay: `"ON"`, `"OFF"`, or `"UNKNOWN"` |
| `relay_states` | object | No | Per-socket states: `{"1": "on", "2": "off", "3": "off"}` |

**Alternative relay_states format** (individual fields):
```json
{
  "device_id": "PA-ESP32-A8F291C42D10",
  "relay_state_1": "on",
  "relay_state_2": "off",
  "relay_state_3": "off"
}
```

**Success Response (200):**
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
      "1": {"relay_state": "on", "is_on": true, "is_off": false, "is_unknown": false},
      "2": {"relay_state": "off", "is_on": false, "is_off": true, "is_unknown": false},
      "3": {"relay_state": "off", "is_on": false, "is_off": true, "is_unknown": false}
    }
  }
}
```

**Error Responses:**
- `401` — Missing `X-Device-Key` header
- `403` — Invalid device key
- `404` — Device not registered
- `503` — Device authentication not configured on server

**Recommended Interval:** Every 15-30 seconds when online.

---

### 3.2 Poll for Relay Commands

**Endpoint:** `GET /api/device/commands?device_id=<device_id>`

**Purpose:** Check for pending relay commands from the backend.

**Authentication:** `X-Device-Key` header

**Query Parameters:**

| Parameter | Required | Description |
|-----------|----------|-------------|
| `device_id` | Yes | The device's Hardware ESP32 ID or device UID |

**Success Response (200):**
```json
{
  "ok": true,
  "commands": [
    {
      "command_id": "rc-abc123def456",
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

**Empty Response (200):**
```json
{
  "ok": true,
  "commands": [],
  "count": 0
}
```

**Recommended Polling Interval:** Every 5-10 seconds.

---

### 3.3 Acknowledge Relay Command

**Endpoint:** `POST /api/device/commands/<command_id>/ack`

**Purpose:** Confirm that a relay command has been executed by the Hardware ESP32.

**Authentication:** `X-Device-Key` header

**Path Parameter:**
- `command_id` — The unique command ID from the poll response (e.g., `rc-abc123def456`)

**Request Body:**
```json
{
  "device_id": "PA-ESP32-A8F291C42D10",
  "socket_state": "on"
}
```

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `device_id` | string | Yes | The device's Hardware ESP32 ID |
| `socket_state` | string | No | Actual relay state after execution: `"on"` or `"off"` |

**Success Response (200):**
```json
{
  "ok": true,
  "message": "Command acknowledged.",
  "command": {
    "command_id": "rc-abc123def456",
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

**Error Responses:**
- `404` — Command not found
- `409` — Command already processed (duplicate acknowledgment)
- `410` — Command has expired

---

### 3.4 Send Energy Telemetry

**Endpoint:** `POST /api/device/telemetry`

**Purpose:** Report electrical measurements from PZEM-004T sensors.

**Authentication:** `X-Device-Key` header

**Request Body:**
```json
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

**Fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `device_id` | string | Yes | The device's Hardware ESP32 ID |
| `socket_number` | integer | Yes | Socket number: 1, 2, or 3 |
| `voltage` | float | No | Voltage in volts (0-500V) |
| `current` | float | No | Current in amps (0-100A) |
| `power_watts` | float | No | Active power in watts |
| `energy_kwh` | float | No | Accumulated energy in kWh |
| `frequency` | float | No | Frequency in Hz |
| `power_factor` | float | No | Power factor (0-1) |

**Note:** At least one measurement field must be present. Missing sensors should be omitted (not sent as `null` or `0`). The backend distinguishes `null` (sensor unavailable) from `0` (actual zero reading).

**Success Response (200):**
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

**Error Responses:**
- `400` — Invalid values (out of range, wrong type)
- `404` — Device not registered

**Recommended Interval:** Every 10-30 seconds per socket.

---

## 4. UART Protocol (Network ESP32 ↔ Hardware ESP32)

The Network ESP32 communicates with the Hardware ESP32 over UART using JSON messages.

### 4.1 Relay Command from Network to Hardware

```json
{
  "type": "relay_command",
  "socket": 2,
  "state": "on"
}
```

### 4.2 Relay Acknowledgment from Hardware to Network

```json
{
  "type": "relay_ack",
  "socket": 2,
  "state": "on"
}
```

### 4.3 Energy Reading from Hardware to Network

```json
{
  "type": "energy_reading",
  "socket": 1,
  "voltage": 234.5,
  "current": 1.23,
  "power_watts": 288.4,
  "energy_kwh": 12.345,
  "frequency": 50.0,
  "power_factor": 0.95
}
```

### 4.4 Hardware Status from Hardware to Network

```json
{
  "type": "status",
  "relay_states": {
    "1": "on",
    "2": "off",
    "3": "off"
  }
}
```

---

## 5. Command Execution Flow

When the Network ESP32 receives a pending relay command from the backend:

1. **Receive command** from `GET /api/device/commands?device_id=...`
2. **Parse command** — extract `socket_number` and `desired_state`
3. **Send UART message** to Hardware ESP32:
   ```json
   {"type": "relay_command", "socket": 2, "state": "on"}
   ```
4. **Wait for UART acknowledgment** from Hardware ESP32 (timeout: 5 seconds):
   ```json
   {"type": "relay_ack", "socket": 2, "state": "on"}
   ```
5. **Send acknowledgment** to backend:
   ```
   POST /api/device/commands/rc-abc123def456/ack
   {"device_id": "PA-ESP32-...", "socket_state": "on"}
   ```
6. **If UART timeout** — retry once, then mark command as failed locally (backend will expire it)

---

## 6. Heartbeat Flow

The Network ESP32 sends a heartbeat every 15-30 seconds:

1. **Read relay states** from Hardware ESP32 via UART
2. **Determine network mode** — `"online"` if connected to router, `"offline"` if in AP mode
3. **Send heartbeat** to backend with current states
4. **On connection loss** — continue sending heartbeats; if backend unreachable, queue commands locally
5. **On reconnection** — send heartbeat, then immediately poll for pending commands

---

## 7. Telemetry Flow

The Network ESP32 reports energy readings:

1. **Read PZEM data** from Hardware ESP32 via UART
2. **Send telemetry** for each socket that has valid readings
3. **Skip sockets** with no PZEM sensor connected (omit from request)
4. **Report at 10-30 second intervals** per socket

---

## 8. Error Handling

| Scenario | Action |
|----------|--------|
| Backend returns 401/403 | Check device secret, retry with exponential backoff |
| Backend returns 404 | Device not registered — log error, continue heartbeats |
| Backend returns 503 | Server misconfigured — log error, retry after 60s |
| Network unreachable | Queue commands locally, retry connection |
| UART timeout | Retry once, then skip command (backend will expire) |
| Command expired | Skip command, poll for next |
| Duplicate command | Skip (backend deduplication) |

---

## 9. Security Requirements

1. **Always use HTTPS** in production — verify server certificate
2. **Store device secret** in secure NVS partition, not in plaintext config
3. **Never log the device secret** — log only the device_id
4. **Never send device secret** in QR codes or to the local web portal
5. **Validate backend responses** — check `ok` field before acting
6. **Implement command deduplication** — track acknowledged command IDs

---

## 10. Configuration

The Network ESP32 firmware must be configured with:

| Parameter | Source | Description |
|-----------|--------|-------------|
| `device_id` | Factory provisioning | Hardware ESP32 ID |
| `device_api_secret` | Factory provisioning | Per-device API secret |
| `backend_url` | Factory provisioning | Flask backend URL |
| `wifi_ssid` | Customer setup | Router Wi-Fi SSID |
| `wifi_password` | Customer setup | Router Wi-Fi password |
| `heartbeat_interval` | Default: 15s | Heartbeat frequency |
| `command_poll_interval` | Default: 5s | Command polling frequency |
| `telemetry_interval` | Default: 15s | Telemetry reporting frequency |

---

## 11. C++ Example: Arduino/ESP32

```cpp
#include <HTTPClient.h>
#include <ArduinoJson.h>

const char* DEVICE_ID = "PA-ESP32-A8F291C42D10";
const char* DEVICE_SECRET = "Vc5a3k9Lm2pQr7sT1xYz4bN6dF8gH0jK";
const char* BACKEND_URL = "https://powerable.on.render.com";

void sendHeartbeat() {
  HTTPClient http;
  http.begin(BACKEND_URL "/api/device/heartbeat");
  http.addHeader("X-Device-Key", DEVICE_SECRET);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(256);
  doc["device_id"] = DEVICE_ID;
  doc["network_mode"] = "online";
  doc["connection_type"] = "router";
  doc["hardware_status"] = "CONNECTED";

  // Per-socket relay states
  JsonVariant relayStates = doc.createNestedObject("relay_states");
  relayStates["1"] = "on";
  relayStates["2"] = "off";
  relayStates["3"] = "off";

  String body;
  serializeJson(doc, body);
  int code = http.POST(body);
  http.end();
}

void pollCommands() {
  HTTPClient http;
  String url = String(BACKEND_URL) + "/api/device/commands?device_id=" + DEVICE_ID;
  http.begin(url);
  http.addHeader("X-Device-Key", DEVICE_SECRET);

  int code = http.GET();
  if (code == 200) {
    String body = http.getString();
    DynamicJsonDocument doc(512);
    DeserializationError err = deserializeJson(doc, body);
    if (!err && doc["ok"] == true) {
      JsonArray commands = doc["commands"];
      for (JsonObject cmd : commands) {
        executeCommand(cmd);
      }
    }
  }
  http.end();
}

void executeCommand(JsonObject cmd) {
  int socket = cmd["socket_number"];
  String desired = cmd["desired_state"];
  String commandId = cmd["command_id"];

  // Send UART command to Hardware ESP32
  String uartMsg = "{\"type\":\"relay_command\",\"socket\":" + 
                   String(socket) + ",\"state\":\"" + desired + "\"}";
  Serial1.println(uartMsg);

  // Wait for acknowledgment (5 second timeout)
  unsigned long start = millis();
  while (millis() - start < 5000) {
    if (Serial1.available()) {
      String ack = Serial1.readStringUntil('\n');
      DynamicJsonDocument ackDoc(128);
      if (deserializeJson(ackDoc, ack) == DeserializationOK) {
        if (ackDoc["type"] == "relay_ack") {
          String actualState = ackDoc["state"];
          acknowledgeCommand(commandId, actualState);
          return;
        }
      }
    }
  }
  // Timeout — command will be expired by backend
}

void acknowledgeCommand(const String& commandId, const String& socketState) {
  HTTPClient http;
  String url = String(BACKEND_URL) + "/api/device/commands/" + commandId + "/ack";
  http.begin(url);
  http.addHeader("X-Device-Key", DEVICE_SECRET);
  http.addHeader("Content-Type", "application/json");

  DynamicJsonDocument doc(128);
  doc["device_id"] = DEVICE_ID;
  doc["socket_state"] = socketState;

  String body;
  serializeJson(doc, body);
  http.POST(body);
  http.end();
}
```

---

## 12. Testing Checklist

Before declaring firmware integration complete:

- [ ] Heartbeat accepted by backend with per-device secret
- [ ] Heartbeat accepted with per-socket relay states
- [ ] Command polling returns empty array when no commands
- [ ] Command polling returns pending command
- [ ] Command acknowledgment updates backend relay state
- [ ] Telemetry ingestion stores readings correctly
- [ ] Telemetry with missing sensors sends null/omitted fields
- [ ] Command timeout handling works
- [ ] Duplicate command rejection works
- [ ] Network recovery after disconnection
- [ ] UART communication with Hardware ESP32
- [ ] HTTPS certificate verification in production
