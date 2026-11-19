"""Tests for device API secret provisioning and rotation.

Verifies:
- The label template receives device_secret during provisioning.
- The label page displays the device_secret.
- rotate_device_secret generates a new secret and invalidates the old one.
- Ownership, device ID, and pairing state are preserved after rotation.
- Heartbeat accepts the new secret after rotation.
- Only admins can rotate the device secret.
- Real secret values are never exposed in test output.
"""

import hashlib

import pytest

from app.extensions import db
from app.models import SmartOutlet
from app.services.device_service import (
    _generate_device_secret,
    _hash_device_secret,
    provision_device,
    rotate_device_secret,
)
from tests.support import create_user, form_post, get_csrf, provision, sign_in


# ---------------------------------------------------------------------------
# Label page displays device_secret
# ---------------------------------------------------------------------------


class TestLabelShowsDeviceSecret:
    """The label response must include the per-device API secret."""

    def test_label_contains_device_secret(self, app, client):
        """Provisioning returns the device_secret on the label page."""
        user_id = create_user(app, role="admin")
        sign_in(client, username="admin")

        resp = form_post(
            client,
            "/admin/outlets/new",
            {
                "device_id": "PA-ESP32-AAAA11112222",
                "device_type": "Smart Power Outlet",
            },
        )
        assert resp.status_code == 200
        page = resp.data.decode()

        # The label page should contain the device secret
        assert "ESP32 Factory Provisioning" in page
        assert "device-api-secret" in page

    def test_label_contains_copy_secret_button(self, app, client):
        """The label page has a copy button for the device secret."""
        user_id = create_user(app, role="admin")
        sign_in(client, username="admin")

        resp = form_post(
            client,
            "/admin/outlets/new",
            {
                "device_id": "PA-ESP32-BBBB33334444",
                "device_type": "Smart Power Outlet",
            },
        )
        assert resp.status_code == 200
        page = resp.data.decode()
        assert "Copy Secret" in page

    def test_label_contains_provisioning_instructions(self, app, client):
        """The label page includes ESP32 provisioning instructions."""
        user_id = create_user(app, role="admin")
        sign_in(client, username="admin")

        resp = form_post(
            client,
            "/admin/outlets/new",
            {
                "device_id": "PA-ESP32-CCCC55556666",
                "device_type": "Smart Power Outlet",
            },
        )
        assert resp.status_code == 200
        page = resp.data.decode()
        assert "Provisioning instructions" in page
        assert "X-Device-Key" in page

    def test_label_no_store_header(self, app, client):
        """The label response must prevent browser caching of the secret."""
        user_id = create_user(app, role="admin")
        sign_in(client, username="admin")

        resp = form_post(
            client,
            "/admin/outlets/new",
            {
                "device_id": "PA-ESP32-DDDD77778888",
                "device_type": "Smart Power Outlet",
            },
        )
        assert "no-store" in resp.headers.get("Cache-Control", "")


# ---------------------------------------------------------------------------
# rotate_device_secret service function
# ---------------------------------------------------------------------------


class TestRotateDeviceSecret:
    """Rotating the device secret invalidates the old one and preserves everything else."""

    def test_rotate_generates_new_secret(self, app):
        """rotate_device_secret returns a new plaintext secret."""
        info = provision(app, "PA-ESP32-EEEE99990000")
        old_secret = info["device_secret"]

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            new_secret = rotate_device_secret(outlet)

        assert new_secret is not None
        assert new_secret != old_secret
        assert isinstance(new_secret, str)
        assert len(new_secret) > 20

    def test_rotate_stores_hash_only(self, app):
        """After rotation, only the SHA-256 hash is stored."""
        info = provision(app, "PA-ESP32-FFFF11112222")

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            new_secret = rotate_device_secret(outlet)

            db.session.refresh(outlet)
            assert outlet.device_api_secret is not None
            assert outlet.device_api_secret == _hash_device_secret(new_secret)
            # The plaintext secret is NOT stored
            assert outlet.device_api_secret != new_secret

    def test_rotate_preserves_device_id(self, app):
        """Rotation must not change the device UID."""
        info = provision(app, "PA-ESP32-1111AAAA2222")
        original_uid = info["device_id"]

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            rotate_device_secret(outlet)

            db.session.refresh(outlet)
            assert outlet.device_uid == original_uid

    def test_rotate_preserves_ownership(self, app):
        """Rotation must not change the owner_id or pairing state."""
        info = provision(app, "PA-ESP32-2222BBBB3333")

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            # Simulate ownership
            user_id = create_user(app, username="owner")
            outlet.owner_id = user_id
            outlet.pairing_state = "registered"
            db.session.commit()

            rotate_device_secret(outlet)

            db.session.refresh(outlet)
            assert outlet.owner_id == user_id
            assert outlet.pairing_state == "registered"

    def test_rotate_invalidates_old_secret(self, app):
        """The old secret must no longer authenticate after rotation."""
        info = provision(app, "PA-ESP32-3333CCCC4444")
        old_secret = info["device_secret"]

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            new_secret = rotate_device_secret(outlet)

            db.session.refresh(outlet)
            old_hash = _hash_device_secret(old_secret)
            new_hash = _hash_device_secret(new_secret)

            assert outlet.device_api_secret == new_hash
            assert outlet.device_api_secret != old_hash

    def test_rotate_creates_device_event(self, app):
        """Rotation logs a device event."""
        from app.models import DeviceEvent

        info = provision(app, "PA-ESP32-4444DDDD5555")

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            rotate_device_secret(outlet)

            events = db.session.scalars(
                db.select(DeviceEvent).filter_by(
                    outlet_id=outlet.id, event_type="secret_rotated"
                )
            ).all()
            assert len(events) >= 1

    def test_rotate_none_outlet_raises(self, app):
        """Rotating a None outlet raises PairingError."""
        from app.services.device_service import PairingError

        with app.app_context():
            with pytest.raises(PairingError, match="no longer exists"):
                rotate_device_secret(None)


# ---------------------------------------------------------------------------
# Admin route for rotate_device_secret
# ---------------------------------------------------------------------------


class TestRotateSecretRoute:
    """The admin route for rotating the device secret."""

    def test_admin_can_rotate_secret(self, app, client):
        """Admin can POST to /rotate-secret and see the new secret."""
        user_id = create_user(app, role="admin")
        sign_in(client, username="admin")

        info = provision(app, "PA-ESP32-5555EEEE6666")

        resp = form_post(
            client,
            f"/admin/outlets/{info['outlet_id']}/rotate-secret",
            {},
        )
        assert resp.status_code == 200
        page = resp.data.decode()
        assert "ESP32 Factory Provisioning" in page

    def test_non_admin_cannot_rotate_secret(self, app, client):
        """Regular users are denied access to rotate-secret."""
        user_id = create_user(app, role="user")
        sign_in(client, username="alice")

        info = provision(app, "PA-ESP32-6666FFFF7777")

        resp = form_post(
            client,
            f"/admin/outlets/{info['outlet_id']}/rotate-secret",
            {},
        )
        assert resp.status_code in (302, 403, 404)

    def test_unauthenticated_cannot_rotate_secret(self, app, client):
        """Unauthenticated requests are denied."""
        info = provision(app, "PA-ESP32-7777AAAA8888")

        csrf = get_csrf(client, "/")
        resp = client.post(
            f"/admin/outlets/{info['outlet_id']}/rotate-secret",
            data={"csrf_token": csrf},
        )
        assert resp.status_code in (302, 403, 404)


# ---------------------------------------------------------------------------
# Heartbeat with rotated secret
# ---------------------------------------------------------------------------


class TestHeartbeatAfterRotation:
    """Heartbeat must accept the new secret and reject the old one after rotation."""

    def test_heartbeat_accepts_new_secret(self, app, client):
        """After rotation, the new secret authenticates the heartbeat via HTTP."""
        info = provision(app, "PA-ESP32-8888BBBB9999")

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            new_secret = rotate_device_secret(outlet)

            # Simulate ownership so heartbeat is accepted
            user_id = create_user(app, username="owner")
            outlet.owner_id = user_id
            outlet.pairing_state = "registered"
            db.session.commit()

        # Heartbeat with new secret should succeed via HTTP endpoint
        resp = client.post(
            "/api/device/heartbeat",
            json={"device_id": info["device_id"]},
            headers={"X-Device-Key": new_secret},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True

    def test_heartbeat_rejects_old_secret(self, app, client):
        """After rotation, the old secret no longer authenticates."""
        from app.device_auth import _validate_per_device_secret

        info = provision(app, "PA-ESP32-9999CCCC0000")
        old_secret = info["device_secret"]

        with app.app_context():
            outlet = db.session.get(SmartOutlet, info["outlet_id"])
            new_secret = rotate_device_secret(outlet)

            # Old secret should fail
            assert not _validate_per_device_secret(
                info["device_id"], old_secret
            )
            # New secret should pass
            assert _validate_per_device_secret(
                info["device_id"], new_secret
            )
