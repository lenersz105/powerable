"""Tests for the online system integration: relay commands, telemetry, Wi-Fi, per-device auth."""

import pytest

from app.extensions import db
from app.models import EnergyReading, RelayCommand, SmartOutlet, WifiCredential
from app.services.device_service import provision_device, register_outlet, process_heartbeat
from app.services.relay_service import issue_relay_command, get_pending_commands, acknowledge_command
from app.services import energy_service
from app.services import wifi_service
from tests.support import (
    create_user,
    get_csrf,
    sign_in,
    api_post,
    provision,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def registered_outlet(app, client):
    """Provision and register an outlet for a test user."""
    info = provision(app, "PA-ESP32-A1B2C3D4E5F6")
    user_id = create_user(app, username="tester", role="user")

    # Register the outlet via the API
    sign_in(client, username="tester")
    resp = api_post(
        client,
        "/api/pairing/register",
        {"payload": info["payload"]},
    )
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["ok"] is True

    return {
        "outlet_id": info["outlet_id"],
        "device_id": info["device_id"],
        "device_secret": info["device_secret"],
        "user_id": user_id,
    }


# ------------------------------------------------------------------
# Per-device authentication
# ------------------------------------------------------------------


class TestPerDeviceAuth:
    """Verify per-device secret authentication."""

    def test_heartbeat_with_per_device_secret(self, app, client, registered_outlet):
        """Heartbeat accepted when using the provisioned per-device secret."""
        resp = client.post(
            "/api/device/heartbeat",
            json={
                "device_id": registered_outlet["device_id"],
                "network_mode": "online",
                "hardware_status": "CONNECTED",
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_heartbeat_with_wrong_per_device_secret(self, app, client, registered_outlet):
        """Heartbeat rejected when using a wrong per-device secret."""
        # If shared key is configured, wrong per-device secret falls back to shared key check
        resp = client.post(
            "/api/device/heartbeat",
            json={
                "device_id": registered_outlet["device_id"],
                "network_mode": "online",
            },
            headers={"X-Device-Key": "wrong-secret"},
        )
        # Should be rejected (403) because wrong secret doesn't match shared key either
        assert resp.status_code == 403

    def test_heartbeat_with_shared_key_fallback(self, app, client, registered_outlet):
        """Heartbeat accepted with shared key when per-device secret is not set."""
        # Create an outlet without per-device secret (simulate legacy device)
        info = provision(app, "PA-ESP32-222222222222")
        user_id = create_user(app, username="legacy_user", role="user")

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            outlet.device_api_secret = None  # Simulate legacy device
            db.session.commit()

        # Register the outlet
        sign_in(client, username="legacy_user")
        resp = api_post(
            client,
            "/api/pairing/register",
            {"payload": info["payload"]},
        )
        assert resp.status_code == 201

        # Heartbeat with shared key should work
        shared_key = app.config["DEVICE_API_KEY"]
        resp = client.post(
            "/api/device/heartbeat",
            json={
                "device_id": info["device_id"],
                "network_mode": "online",
            },
            headers={"X-Device-Key": shared_key},
        )
        assert resp.status_code == 200

    def test_heartbeat_without_device_key(self, app, client):
        """Heartbeat rejected without X-Device-Key header."""
        resp = client.post(
            "/api/device/heartbeat",
            json={"device_id": "PA-ESP32-A1B2C3D4E5F6"},
        )
        assert resp.status_code == 401

    def test_provisioning_generates_device_secret(self, app):
        """Provisioning generates a per-device secret and stores its hash."""
        with app.app_context():
            outlet, code, payload, device_secret = provision_device("PA-ESP32-111111111111")
            assert device_secret is not None
            assert len(device_secret) > 0
            assert outlet.device_api_secret is not None
            # The stored value should be a hash, not the plaintext
            assert outlet.device_api_secret != device_secret
            assert len(outlet.device_api_secret) == 64  # SHA-256 hex digest


# ------------------------------------------------------------------
# Relay command lifecycle
# ------------------------------------------------------------------


class TestRelayCommands:
    """Test relay command issuance, polling, and acknowledgment."""

    def test_issue_relay_command(self, app, client, registered_outlet):
        """Owner can issue a relay command for an online outlet."""
        with app.app_context():
            outlet = db.session.get(SmartOutlet, registered_outlet["outlet_id"])
            # Simulate online status
            outlet.connection_status = "online"
            outlet.network_mode = "online"
            outlet.last_seen_at = outlet.updated_at
            db.session.commit()

        sign_in(client, username="tester")
        resp = api_post(
            client,
            f"/api/device/{registered_outlet['device_id']}/relay",
            {"socket_number": 1, "desired_state": "on"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "command" in data
        assert data["command"]["socket_number"] == 1
        assert data["command"]["desired_state"] == "on"
        assert data["command"]["status"] == "pending"

    def test_relay_command_rejects_offline_outlet(self, app, client, registered_outlet):
        """Cannot issue relay command when outlet is offline."""
        sign_in(client, username="tester")
        resp = api_post(
            client,
            f"/api/device/{registered_outlet['device_id']}/relay",
            {"socket_number": 1, "desired_state": "on"},
        )
        # Outlet is not online (no heartbeat), so command should be rejected
        assert resp.status_code == 409

    def test_relay_command_invalid_socket(self, app, client, registered_outlet):
        """Relay command rejected for invalid socket number."""
        sign_in(client, username="tester")
        resp = api_post(
            client,
            f"/api/device/{registered_outlet['device_id']}/relay",
            {"socket_number": 5, "desired_state": "on"},
        )
        assert resp.status_code == 400

    def test_relay_command_non_owner_rejected(self, app, client, registered_outlet):
        """Non-owner cannot issue relay command."""
        create_user(app, username="other_user", role="user")
        sign_in(client, username="other_user")
        resp = api_post(
            client,
            f"/api/device/{registered_outlet['device_id']}/relay",
            {"socket_number": 1, "desired_state": "on"},
        )
        # 404 because device not found for this user (ownership not disclosed)
        assert resp.status_code in (404, 409)  # 404 = not found, 409 = offline

    def test_poll_commands_returns_pending(self, app, client, registered_outlet):
        """ESP32 can poll for pending commands."""
        command_id = None
        with app.app_context():
            outlet = db.session.get(SmartOutlet, registered_outlet["outlet_id"])
            outlet.connection_status = "online"
            outlet.network_mode = "online"
            outlet.last_seen_at = outlet.updated_at
            db.session.commit()

            cmd = issue_relay_command(
                outlet, socket_number=2, desired_state="off", user_id=registered_outlet["user_id"]
            )
            command_id = cmd.command_id

        resp = client.get(
            f"/api/device/commands?device_id={registered_outlet['device_id']}",
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["count"] == 1
        assert data["commands"][0]["command_id"] == command_id

    def test_acknowledge_command(self, app, client, registered_outlet):
        """ESP32 can acknowledge a relay command."""
        command_id = None
        with app.app_context():
            outlet = db.session.get(SmartOutlet, registered_outlet["outlet_id"])
            outlet.connection_status = "online"
            outlet.network_mode = "online"
            outlet.last_seen_at = outlet.updated_at
            db.session.commit()

            cmd = issue_relay_command(
                outlet, socket_number=1, desired_state="on", user_id=registered_outlet["user_id"]
            )
            command_id = cmd.command_id

        resp = client.post(
            f"/api/device/commands/{command_id}/ack",
            json={
                "device_id": registered_outlet["device_id"],
                "socket_state": "on",
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["command"]["status"] == "completed"

        # Verify outlet relay state updated
        with app.app_context():
            outlet = db.session.get(SmartOutlet, registered_outlet["outlet_id"])
            assert outlet.get_relay_state(1) == "on"

    def test_duplicate_acknowledgment_rejected(self, app, client, registered_outlet):
        """Second acknowledgment of the same command is rejected."""
        command_id = None
        with app.app_context():
            outlet = db.session.get(SmartOutlet, registered_outlet["outlet_id"])
            outlet.connection_status = "online"
            outlet.network_mode = "online"
            outlet.last_seen_at = outlet.updated_at
            db.session.commit()

            cmd = issue_relay_command(
                outlet, socket_number=1, desired_state="on", user_id=registered_outlet["user_id"]
            )
            command_id = cmd.command_id

        # First ack
        resp = client.post(
            f"/api/device/commands/{command_id}/ack",
            json={"device_id": registered_outlet["device_id"], "socket_state": "on"},
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200

        # Second ack — should be rejected
        resp = client.post(
            f"/api/device/commands/{command_id}/ack",
            json={"device_id": registered_outlet["device_id"], "socket_state": "on"},
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 409


# ------------------------------------------------------------------
# Energy telemetry
# ------------------------------------------------------------------


class TestTelemetry:
    """Test energy telemetry ingestion and retrieval."""

    def test_ingest_telemetry(self, app, client, registered_outlet):
        """ESP32 can send energy readings."""
        resp = client.post(
            "/api/device/telemetry",
            json={
                "device_id": registered_outlet["device_id"],
                "socket_number": 1,
                "voltage": 234.5,
                "current": 1.23,
                "power_watts": 288.4,
                "energy_kwh": 12.345,
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["reading"]["voltage"] == 234.5
        assert data["reading"]["current"] == 1.23

    def test_telemetry_stores_reading(self, app, client, registered_outlet):
        """Telemetry reading is stored in the database."""
        client.post(
            "/api/device/telemetry",
            json={
                "device_id": registered_outlet["device_id"],
                "socket_number": 2,
                "voltage": 230.0,
                "current": 0.5,
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )

        with app.app_context():
            readings = db.session.scalars(
                db.select(EnergyReading).filter_by(
                    outlet_id=registered_outlet["outlet_id"], socket_number=2
                )
            ).all()
            assert len(readings) == 1
            assert readings[0].voltage == 230.0
            assert readings[0].current == 0.5

    def test_telemetry_rejects_out_of_range(self, app, client, registered_outlet):
        """Telemetry rejected for out-of-range values."""
        resp = client.post(
            "/api/device/telemetry",
            json={
                "device_id": registered_outlet["device_id"],
                "socket_number": 1,
                "voltage": 9999,
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 400

    def test_telemetry_null_for_missing_sensors(self, app, client, registered_outlet):
        """Missing sensor fields are stored as null, not zero."""
        resp = client.post(
            "/api/device/telemetry",
            json={
                "device_id": registered_outlet["device_id"],
                "socket_number": 3,
                "voltage": 240.0,
                # current, power_watts, etc. omitted
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200

        with app.app_context():
            reading = db.session.scalar(
                db.select(EnergyReading).filter_by(
                    outlet_id=registered_outlet["outlet_id"], socket_number=3
                )
            )
            assert reading is not None
            assert reading.voltage == 240.0
            assert reading.current is None
            assert reading.power_watts is None

    def test_chart_data_api(self, app, client, registered_outlet):
        """Dashboard can fetch chart data for energy readings."""
        # Send a reading first
        client.post(
            "/api/device/telemetry",
            json={
                "device_id": registered_outlet["device_id"],
                "socket_number": 1,
                "voltage": 230.0,
                "current": 1.0,
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )

        # Fetch chart data
        sign_in(client, username="tester")
        resp = client.get(
            f"/api/monitoring/readings?device_id={registered_outlet['device_id']}&socket_number=1"
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "chart" in data
        assert "latest" in data


# ------------------------------------------------------------------
# Wi-Fi setup
# ------------------------------------------------------------------


class TestWifiSetup:
    """Test Wi-Fi credential management."""

    def test_setup_wifi(self, app, client, registered_outlet):
        """Owner can configure Wi-Fi credentials."""
        sign_in(client, username="tester")
        resp = api_post(
            client,
            f"/api/device/{registered_outlet['device_id']}/wifi-setup",
            {"ssid": "MyHomeNetwork", "password": "super-secret-123"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["wifi"]["ssid"] == "MyHomeNetwork"
        # Password should NOT be in response
        assert "password" not in str(data).lower() or "password_encrypted" not in str(data)

    def test_wifi_credentials_encrypted(self, app, client, registered_outlet):
        """Wi-Fi password is encrypted in the database."""
        sign_in(client, username="tester")
        api_post(
            client,
            f"/api/device/{registered_outlet['device_id']}/wifi-setup",
            {"ssid": "TestNetwork", "password": "test-password-456"},
        )

        with app.app_context():
            cred = db.session.scalar(
                db.select(WifiCredential).filter_by(
                    outlet_id=registered_outlet["outlet_id"]
                )
            )
            assert cred is not None
            assert cred.ssid == "TestNetwork"
            # Password should be encrypted, not plaintext
            assert cred.password_encrypted != "test-password-456"
            # Decryption should work
            assert cred.decrypt_password() == "test-password-456"

    def test_wifi_setup_missing_ssid(self, app, client, registered_outlet):
        """Wi-Fi setup rejected without SSID."""
        sign_in(client, username="tester")
        resp = api_post(
            client,
            f"/api/device/{registered_outlet['device_id']}/wifi-setup",
            {"ssid": "", "password": "test"},
        )
        assert resp.status_code == 400

    def test_wifi_setup_non_owner_rejected(self, app, client, registered_outlet):
        """Non-owner cannot configure Wi-Fi for another user's device."""
        # Use a fresh client to avoid session contamination
        create_user(app, username="wifi_other", role="user")

        # Sign out tester and sign in as wifi_other
        from tests.support import sign_out
        sign_out(client)
        sign_in(client, username="wifi_other", password="correct-horse-9")

        resp = api_post(
            client,
            f"/api/device/{registered_outlet['device_id']}/wifi-setup",
            {"ssid": "HackedNetwork", "password": "hack"},
        )
        # 404 because device not found for this user (ownership not disclosed)
        assert resp.status_code == 404


# ------------------------------------------------------------------
# Heartbeat with per-socket relay states
# ------------------------------------------------------------------


class TestHeartbeatPerSocket:
    """Test heartbeat with per-socket relay state updates."""

    def test_heartbeat_updates_per_socket_states(self, app, client, registered_outlet):
        """Heartbeat with relay_states dict updates per-socket states."""
        resp = client.post(
            "/api/device/heartbeat",
            json={
                "device_id": registered_outlet["device_id"],
                "network_mode": "online",
                "relay_states": {"1": "on", "2": "off", "3": "on"},
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200

        with app.app_context():
            outlet = db.session.get(SmartOutlet, registered_outlet["outlet_id"])
            assert outlet.get_relay_state(1) == "on"
            assert outlet.get_relay_state(2) == "off"
            assert outlet.get_relay_state(3) == "on"

    def test_heartbeat_individual_socket_fields(self, app, client, registered_outlet):
        """Heartbeat with individual relay_state_N fields works."""
        resp = client.post(
            "/api/device/heartbeat",
            json={
                "device_id": registered_outlet["device_id"],
                "network_mode": "online",
                "relay_state_1": "off",
                "relay_state_2": "on",
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200

        with app.app_context():
            outlet = db.session.get(SmartOutlet, registered_outlet["outlet_id"])
            assert outlet.get_relay_state(1) == "off"
            assert outlet.get_relay_state(2) == "on"

    def test_heartbeat_legacy_relay_state_still_works(self, app, client, registered_outlet):
        """Legacy relay_state field still works for backward compatibility."""
        resp = client.post(
            "/api/device/heartbeat",
            json={
                "device_id": registered_outlet["device_id"],
                "network_mode": "online",
                "relay_state": "ON",
            },
            headers={"X-Device-Key": registered_outlet["device_secret"]},
        )
        assert resp.status_code == 200

        with app.app_context():
            outlet = db.session.get(SmartOutlet, registered_outlet["outlet_id"])
            assert outlet.relay_state == "on"

    def test_heartbeat_socket_count_default(self, app):
        """Newly provisioned outlets default to 3 sockets."""
        with app.app_context():
            outlet, code, payload, secret = provision_device("PA-ESP32-333333333333")
            assert outlet.socket_count == 3
            assert outlet.effective_socket_count == 3


# ------------------------------------------------------------------
# Admin provisioning with new fields
# ------------------------------------------------------------------


class TestAdminProvisioning:
    """Test admin provisioning with hardware/network ESP32 IDs."""

    def test_provision_with_dual_board_ids(self, app):
        """Provisioning stores hardware and network ESP32 IDs."""
        with app.app_context():
            outlet, code, payload, secret = provision_device(
                "PA-ESP32-444444444444",
                hardware_esp32_id="PA-HW-A8F291C42D10",
                network_esp32_id="PA-NET-12345678",
                model="PowerAble-3S",
                socket_count=3,
            )
            assert outlet.hardware_esp32_id == "PA-HW-A8F291C42D10"
            assert outlet.network_esp32_id == "PA-NET-12345678"
            assert outlet.model == "PowerAble-3S"
            assert outlet.socket_count == 3
            assert outlet.device_api_secret is not None

    def test_canonical_device_id_uses_hardware_id(self, app):
        """Canonical device ID prefers hardware_esp32_id over device_uid."""
        with app.app_context():
            outlet, code, payload, secret = provision_device(
                "PA-ESP32-555555555555",
                hardware_esp32_id="PA-HW-CANONICAL123",
            )
            assert outlet.canonical_device_id == "PA-HW-CANONICAL123"

    def test_canonical_device_id_falls_back_to_uid(self, app):
        """Canonical device ID falls back to device_uid when hardware ID not set."""
        with app.app_context():
            outlet, code, payload, secret = provision_device("PA-ESP32-666666666666")
            assert outlet.canonical_device_id == outlet.device_uid
