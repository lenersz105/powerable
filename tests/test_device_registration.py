"""Self-registration, QR links, heartbeats, and dashboard device details."""

import pytest

from app.extensions import db
from app.models import SmartOutlet
from app.services import qr_service
from tests.support import form_post, provision, register

DEVICE = "PA-ESP32-A8F291C42D10"
SECOND_DEVICE = "PA-ESP32-001122334455"
HEARTBEAT = "/api/device/heartbeat"
DEVICE_KEY = "PowerAble_Network_2026_Rey_X82k9P"
DEVICE_HEADERS = {"X-Device-Key": DEVICE_KEY}


def _heartbeat(client, payload, key=DEVICE_KEY):
    headers = {"X-Device-Key": key} if key is not None else {}
    return client.post(HEARTBEAT, json=payload, headers=headers)


def _outlet(device_id):
    return db.session.scalar(
        db.select(SmartOutlet).filter_by(device_uid=device_id)
    )


def _count(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(SmartOutlet.id)))


def _register_via_qr(client, device_id=DEVICE):
    return client.get(f"/register?device_id={device_id}")


def test_add_page_requires_authentication(client):
    response = client.get("/devices/add")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_add_page_rejects_invalid_device_id(app, client):
    register(client, "alice")

    response = form_post(
        client,
        "/devices/add",
        {"device_id": "PA-ESP32-NOTHEX", "action": "preview"},
        csrf_path="/devices/add",
    )

    assert response.status_code == 400
    assert "hexadecimal" in response.get_data(as_text=True)
    assert _count(app) == 0


def test_preview_builds_lan_qr_without_registering(app, client):
    register(client, "alice")

    response = form_post(
        client,
        "/devices/add",
        {"device_id": DEVICE.lower(), "action": "preview"},
        csrf_path="/devices/add",
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert DEVICE in body
    assert f"http://192.168.1.10:5000/register?device_id={DEVICE}" in body
    assert f"/devices/qr/{DEVICE}.png" in body
    assert "localhost" not in body.split("Registration link", 1)[1].split("</code>", 1)[0]
    assert _count(app) == 0


def test_qr_png_is_served_and_can_be_downloaded(client):
    register(client, "alice")

    response = client.get(f"/devices/qr/{DEVICE}.png")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG")
    assert response.headers["Cache-Control"].startswith("no-store")

    response = client.get(f"/devices/qr/{DEVICE}.png?download=1")
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    assert f"{DEVICE}-registration-qr.png" in response.headers["Content-Disposition"]


def test_direct_register_assigns_name_and_owner(app, client):
    register(client, "alice")

    response = form_post(
        client,
        "/devices/add",
        {"device_id": DEVICE.lower(), "action": "register"},
        csrf_path="/devices/add",
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/devices/")

    with app.app_context():
        outlet = _outlet(DEVICE)
        assert outlet is not None
        assert outlet.display_name == "SmartOutlet 1"
        assert outlet.channel == 1
        assert outlet.owner.username == "alice"
        assert outlet.pairing_state == "registered"
        assert outlet.ip_address is None
        assert outlet.hardware_connected is None
        assert outlet.last_seen_at is None


def test_qr_registration_route_creates_once_and_shows_dashboard_link(app, client):
    register(client, "alice")

    response = _register_via_qr(client)
    assert response.status_code == 201
    body = response.get_data(as_text=True)
    assert "Smart outlet registered" in body
    assert DEVICE in body
    assert "SmartOutlet 1" in body
    assert "/dashboard/" in body

    response = _register_via_qr(client)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "already registered" in body
    assert _count(app) == 1


def test_qr_registration_validates_and_requires_sign_in(client):
    response = _register_via_qr(client)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    register(client, "alice")

    response = client.get("/register")
    assert response.status_code == 400
    assert "no device ID" in response.get_data(as_text=True)

    response = client.get("/register?device_id=PA-ESP32-SHORT")
    assert response.status_code == 400
    assert "hexadecimal" in response.get_data(as_text=True)


def test_another_account_cannot_claim_the_same_device(app, client):
    register(client, "alice")
    assert _register_via_qr(client).status_code == 201

    second = app.test_client()
    register(second, "bob")

    response = _register_via_qr(second)
    assert response.status_code == 409
    assert "another account" in response.get_data(as_text=True)
    assert _count(app) == 1


def test_preview_rejects_an_already_registered_device(app, client):
    register(client, "alice")
    assert _register_via_qr(client).status_code == 201

    response = form_post(
        client,
        "/devices/add",
        {"device_id": DEVICE, "action": "preview"},
        csrf_path="/devices/add",
    )

    assert response.status_code == 409
    assert "already registered to your account" in response.get_data(as_text=True)
    assert _count(app) == 1


def test_heartbeat_rejects_unknown_devices_without_creating_them(app, client):
    response = _heartbeat(
        client,
        {
            "device_id": DEVICE,
            "ip_address": "192.168.1.100",
            "outlet_state": False,
            "hardware_connected": True,
        },
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "unknown_device"
    assert _count(app) == 0


def test_heartbeat_rejects_provisioned_but_unregistered_devices(app, client):
    info = provision(app, SECOND_DEVICE)

    response = _heartbeat(
        client, {"device_id": info["device_id"], "outlet_state": True}
    )

    assert response.status_code == 403
    assert response.get_json()["error"] == "not_registered"

    with app.app_context():
        outlet = _outlet(SECOND_DEVICE)
        assert outlet.owner_id is None
        assert outlet.last_seen_at is None


def test_heartbeat_updates_ip_relay_hardware_and_last_seen(app, client):
    register(client, "alice")
    assert _register_via_qr(client).status_code == 201

    # No browser session or CSRF token: this is the ESP32 request path, which
    # authenticates with the X-Device-Key header instead.
    response = _heartbeat(
        client,
        {
            "device_id": DEVICE,
            "ip_address": "192.168.1.100",
            "outlet_state": True,
            "hardware_connected": True,
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["device"]["ip_address"] == "192.168.1.100"
    assert body["device"]["relay_state"] == "on"
    assert body["device"]["hardware_connected"] is True
    assert body["device"]["online"] is True
    assert body["device"]["last_seen_at"] is not None

    with app.app_context():
        outlet = _outlet(DEVICE)
        assert outlet.ip_address == "192.168.1.100"
        assert outlet.relay_state == "on"
        assert outlet.hardware_connected is True
        assert outlet.connection_status == "online"
        assert outlet.last_seen_at is not None
        assert outlet.is_online is True

    response = _heartbeat(
        client,
        {
            "device_id": DEVICE.lower(),
            "ip_address": "192.168.1.101",
            "outlet_state": False,
            "hardware_connected": False,
        },
    )
    assert response.status_code == 200

    with app.app_context():
        outlet = _outlet(DEVICE)
        assert outlet.ip_address == "192.168.1.101"
        assert outlet.relay_state == "off"
        assert outlet.hardware_connected is False


def test_heartbeat_validates_payload_types(client):
    response = client.post(
        HEARTBEAT,
        data="not json",
        content_type="text/plain",
        headers=DEVICE_HEADERS,
    )
    assert response.status_code == 400

    response = _heartbeat(client, {"device_id": DEVICE})
    assert response.status_code == 404

    register(client, "alice")
    assert _register_via_qr(client).status_code == 201

    bad_payloads = [
        {"device_id": "PA-ESP32-SHORT"},
        {"device_id": DEVICE, "ip_address": "999.1.1.1"},
        {"device_id": DEVICE, "outlet_state": "on"},
        {"device_id": DEVICE, "hardware_connected": 1},
    ]
    for payload in bad_payloads:
        response = _heartbeat(client, payload)
        assert response.status_code == 400
        assert response.get_json()["ok"] is False


def test_dashboard_shows_registration_and_heartbeat_details(app, client):
    register(client, "alice")
    assert _register_via_qr(client).status_code == 201

    response = _heartbeat(
        client,
        {
            "device_id": DEVICE,
            "ip_address": "192.168.1.100",
            "outlet_state": True,
            "hardware_connected": True,
        },
    )
    assert response.status_code == 200

    response = client.get("/dashboard/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert "SmartOutlet 1" in body
    assert DEVICE in body
    assert "192.168.1.100" in body
    assert "Online" in body
    assert "Connected" in body
    assert "Last communication" in body
    assert "Relay: ON" in body


def test_registration_url_auto_detects_lan_ip(app, monkeypatch):
    monkeypatch.setattr(qr_service, "detect_lan_ip", lambda: "192.168.1.55")
    app.config["PUBLIC_BASE_URL"] = None

    with app.app_context():
        url = qr_service.build_registration_url(DEVICE)
        assert url == f"http://192.168.1.55:5000/register?device_id={DEVICE}"


def test_registration_url_rejects_loopback_base(app):
    app.config["PUBLIC_BASE_URL"] = "http://127.0.0.1:5000"

    with app.app_context(), pytest.raises(qr_service.QRNetworkError):
        qr_service.build_registration_url(DEVICE)
