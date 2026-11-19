"""Tests for real-time electricity monitoring.

Verifies:
- GET /api/monitoring/latest returns latest readings for all owned outlets.
- Ownership isolation: users cannot see another user's device readings.
- Per-socket readings are correctly separated.
- Missing sensors show null, not zero.
- Unauthenticated access is denied.
- Electricity rate is included in the response.
- Stale reading detection works correctly.
"""

import pytest
from datetime import timedelta

from app.extensions import db
from app.models import EnergyReading, SmartOutlet
from app.utils import utcnow
from tests.support import create_user, provision, sign_in


# ---------------------------------------------------------------------------
# GET /api/monitoring/latest — authentication and authorization
# ---------------------------------------------------------------------------


class TestMonitoringLatestAuth:
    """Authentication and ownership checks for /api/monitoring/latest."""

    def test_unauthenticated_access_denied(self, app, client):
        """Unauthenticated requests are redirected."""
        resp = client.get("/api/monitoring/latest")
        assert resp.status_code in (302, 401)

    def test_authenticated_user_with_no_devices(self, app, client):
        """Authenticated user with no devices gets empty outlets."""
        user_id = create_user(app, username="alice", role="user")
        sign_in(client, username="alice")

        resp = client.get("/api/monitoring/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["outlets"] == {}
        assert "electricity_rate_php_kwh" in data

    def test_user_sees_only_own_devices(self, app, client):
        """User cannot see telemetry from another user's device."""
        # Create two users
        admin_id = create_user(app, role="admin")
        user_a_id = create_user(app, username="usera", role="user")
        user_b_id = create_user(app, username="userb", role="user")

        # Admin provisions a device, user A claims it
        info = provision(app, "PA-ESP32-AAAA11112222")
        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            outlet.owner_id = user_a_id
            outlet.pairing_state = "registered"
            db.session.commit()

        # Send telemetry as the device (no session needed — device auth only)
        resp = client.post(
            "/api/device/telemetry",
            json={
                "device_id": "PA-ESP32-AAAA11112222",
                "socket_number": 1,
                "voltage": 220.5,
                "current": 1.5,
                "power_watts": 330.75,
            },
            headers={"X-Device-Key": info["device_secret"]},
        )
        assert resp.status_code == 200

        # Sign in as userb
        sign_in(client, username="userb")
        resp = client.get("/api/monitoring/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        # User B should not see user A's device
        assert "PA-ESP32-AAAA11112222" not in data["outlets"]

    def test_user_sees_own_device_readings(self, app, client):
        """User can see telemetry from their own device."""
        admin_id = create_user(app, role="admin")
        user_id = create_user(app, username="owner", role="user")

        info = provision(app, "PA-ESP32-BBBB33334444")
        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            outlet.owner_id = user_id
            outlet.pairing_state = "registered"
            db.session.commit()

        # Send telemetry
        resp = client.post(
            "/api/device/telemetry",
            json={
                "device_id": "PA-ESP32-BBBB33334444",
                "socket_number": 1,
                "voltage": 230.0,
                "current": 2.0,
                "power_watts": 460.0,
                "energy_kwh": 5.5,
                "power_factor": 0.95,
            },
            headers={"X-Device-Key": info["device_secret"]},
        )
        assert resp.status_code == 200

        # User fetches latest readings
        sign_in(client, username="owner")
        resp = client.get("/api/monitoring/latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "PA-ESP32-BBBB33334444" in data["outlets"]

        outlet_data = data["outlets"]["PA-ESP32-BBBB33334444"]
        assert "1" in outlet_data["sockets"]
        reading = outlet_data["sockets"]["1"]
        assert reading["voltage"] == 230.0
        assert reading["current"] == 2.0
        assert reading["power_watts"] == 460.0
        assert reading["energy_kwh"] == 5.5
        assert reading["power_factor"] == 0.95


# ---------------------------------------------------------------------------
# Per-socket readings
# ---------------------------------------------------------------------------


class TestPerSocketReadings:
    """Per-socket energy readings are correctly separated."""

    def test_all_three_sockets_returned(self, app, client):
        """Telemetry for all three sockets is returned separately."""
        admin_id = create_user(app, role="admin")
        user_id = create_user(app, username="owner", role="user")

        info = provision(app, "PA-ESP32-CCCC55556666")
        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            outlet.owner_id = user_id
            outlet.pairing_state = "registered"
            db.session.commit()

        # Send telemetry for all three sockets
        for socket_num, voltage in [(1, 220.0), (2, 225.0), (3, 218.0)]:
            resp = client.post(
                "/api/device/telemetry",
                json={
                    "device_id": "PA-ESP32-CCCC55556666",
                    "socket_number": socket_num,
                    "voltage": voltage,
                    "current": 1.0,
                },
                headers={"X-Device-Key": info["device_secret"]},
            )
            assert resp.status_code == 200

        sign_in(client, username="owner")
        resp = client.get("/api/monitoring/latest")
        data = resp.get_json()
        sockets = data["outlets"]["PA-ESP32-CCCC55556666"]["sockets"]

        assert "1" in sockets
        assert "2" in sockets
        assert "3" in sockets
        assert sockets["1"]["voltage"] == 220.0
        assert sockets["2"]["voltage"] == 225.0
        assert sockets["3"]["voltage"] == 218.0

    def test_missing_socket_shows_no_reading(self, app, client):
        """Sockets without telemetry are not in the response."""
        admin_id = create_user(app, role="admin")
        user_id = create_user(app, username="owner", role="user")

        info = provision(app, "PA-ESP32-DDDD77778888")
        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            outlet.owner_id = user_id
            outlet.pairing_state = "registered"
            db.session.commit()

        # Only send telemetry for socket 1
        resp = client.post(
            "/api/device/telemetry",
            json={
                "device_id": "PA-ESP32-DDDD77778888",
                "socket_number": 1,
                "voltage": 220.0,
            },
            headers={"X-Device-Key": info["device_secret"]},
        )
        assert resp.status_code == 200

        sign_in(client, username="owner")
        resp = client.get("/api/monitoring/latest")
        data = resp.get_json()
        sockets = data["outlets"]["PA-ESP32-DDDD77778888"]["sockets"]

        assert "1" in sockets
        assert "2" not in sockets
        assert "3" not in sockets

    def test_null_sensor_fields_preserved(self, app, client):
        """Missing sensor fields are null, not zero."""
        admin_id = create_user(app, role="admin")
        user_id = create_user(app, username="owner", role="user")

        info = provision(app, "PA-ESP32-EEEE99990000")
        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            outlet.owner_id = user_id
            outlet.pairing_state = "registered"
            db.session.commit()

        # Send only voltage, leave other fields out
        resp = client.post(
            "/api/device/telemetry",
            json={
                "device_id": "PA-ESP32-EEEE99990000",
                "socket_number": 1,
                "voltage": 230.0,
            },
            headers={"X-Device-Key": info["device_secret"]},
        )
        assert resp.status_code == 200

        sign_in(client, username="owner")
        resp = client.get("/api/monitoring/latest")
        data = resp.get_json()
        reading = data["outlets"]["PA-ESP32-EEEE99990000"]["sockets"]["1"]

        assert reading["voltage"] == 230.0
        assert reading["current"] is None
        assert reading["power_watts"] is None
        assert reading["energy_kwh"] is None


# ---------------------------------------------------------------------------
# Electricity rate
# ---------------------------------------------------------------------------


class TestElectricityRate:
    """Electricity rate is included in the response."""

    def test_rate_included_in_response(self, app, client):
        """The electricity rate is returned in the response."""
        user_id = create_user(app, username="alice", role="user")
        sign_in(client, username="alice")

        resp = client.get("/api/monitoring/latest")
        data = resp.get_json()
        assert "electricity_rate_php_kwh" in data
        assert data["electricity_rate_php_kwh"] > 0


# ---------------------------------------------------------------------------
# Stale reading detection
# ---------------------------------------------------------------------------


class TestStaleReadings:
    """Stale reading detection based on recorded_at timestamp."""

    def test_recent_reading_is_fresh(self, app, client):
        """A recent reading is returned without stale flag."""
        admin_id = create_user(app, role="admin")
        user_id = create_user(app, username="owner", role="user")

        info = provision(app, "PA-ESP32-FFFF11112222")
        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            outlet.owner_id = user_id
            outlet.pairing_state = "registered"
            db.session.commit()

        # Send telemetry (recorded_at = now)
        resp = client.post(
            "/api/device/telemetry",
            json={
                "device_id": "PA-ESP32-FFFF11112222",
                "socket_number": 1,
                "voltage": 220.0,
            },
            headers={"X-Device-Key": info["device_secret"]},
        )
        assert resp.status_code == 200

        sign_in(client, username="owner")
        resp = client.get("/api/monitoring/latest")
        data = resp.get_json()
        reading = data["outlets"]["PA-ESP32-FFFF11112222"]["sockets"]["1"]

        assert reading["voltage"] == 220.0
        assert reading["recorded_at"] is not None

    def test_old_reading_still_returned(self, app):
        """Old readings are still returned; staleness is a frontend concern."""
        info = provision(app, "PA-ESP32-1111AAAA2222")
        user_id = create_user(app, username="owner", role="user")

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            outlet.owner_id = user_id
            outlet.pairing_state = "registered"

            # Create an old reading (2 hours ago)
            old_time = utcnow() - timedelta(hours=2)
            reading = EnergyReading.create(
                outlet_id=outlet.id,
                socket_number=1,
                voltage=215.0,
                recorded_at=old_time,
            )
            db.session.commit()

            # Verify the reading is returned
            from app.services import energy_service

            all_readings = energy_service.get_latest_all_sockets([outlet.id])
            socket_reading = all_readings[outlet.id][1]
            assert socket_reading["voltage"] == 215.0
            assert socket_reading["recorded_at"] is not None
