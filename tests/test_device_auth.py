"""Device API key authentication and ESP32 heartbeat contract."""

from app.extensions import db
from app.models import SmartOutlet
from tests.support import register

HEARTBEAT = "/api/device/heartbeat"
DEVICE_KEY = "PowerAble_Network_2026_Rey_X82k9P"
HEADERS = {"X-Device-Key": DEVICE_KEY}
NET_DEVICE = "PA-NET-12345678"


def _post(client, payload, headers=HEADERS):
    return client.post(HEARTBEAT, json=payload, headers=headers)


def _register_device(client, device_id=NET_DEVICE):
    return client.get(f"/register?device_id={device_id}")


def _outlet(device_id=NET_DEVICE):
    return db.session.scalar(
        db.select(SmartOutlet).filter_by(device_uid=device_id)
    )


def _count():
    return db.session.scalar(db.select(db.func.count(SmartOutlet.id)))


ONLINE = {
    "device_id": NET_DEVICE,
    "network_mode": "online",
    "connection_type": "router",
    "ip_address": "192.168.1.120",
    "hardware_status": "CONNECTED",
    "relay_state": "OFF",
}


def test_heartbeat_without_key_returns_401(client):
    response = _post(client, ONLINE, headers={})

    assert response.status_code == 401
    body = response.get_json()
    assert body["ok"] is False
    assert body["error"] == "missing_device_key"
    assert DEVICE_KEY not in response.get_data(as_text=True)


def test_heartbeat_with_wrong_key_returns_403(client):
    response = _post(client, ONLINE, headers={"X-Device-Key": "wrong-key"})

    assert response.status_code == 403
    body = response.get_json()
    assert body["ok"] is False
    assert body["error"] == "invalid_device_key"
    assert "wrong-key" not in response.get_data(as_text=True)
    assert DEVICE_KEY not in response.get_data(as_text=True)


def test_heartbeat_with_valid_key_registers_online_router(app, client):
    register(client, "alice")
    assert _register_device(client).status_code == 201

    response = _post(client, ONLINE)
    assert response.status_code == 200

    body = response.get_json()
    assert body["ok"] is True
    assert body["device"]["device_id"] == NET_DEVICE
    assert body["device"]["network_mode"] == "online"
    assert body["device"]["connection_type"] == "router"
    assert body["device"]["ip_address"] == "192.168.1.120"
    assert body["device"]["hardware_status"] == "CONNECTED"
    assert body["device"]["relay_state"] == "off"
    assert body["device"]["online"] is True
    assert body["device"]["last_seen_at"] is not None

    with app.app_context():
        outlet = _outlet()
        assert outlet is not None
        assert outlet.network_mode == "online"
        assert outlet.connection_type == "router"
        assert outlet.ip_address == "192.168.1.120"
        assert outlet.hardware_connected is True
        assert outlet.relay_state == "off"
        assert outlet.connection_status == "online"
        assert outlet.last_seen_at is not None
        assert _count() == 1


def test_offline_access_point_heartbeat_is_not_online(app, client):
    register(client, "alice")
    assert _register_device(client).status_code == 201
    assert _post(client, ONLINE).status_code == 200

    with app.app_context():
        first_seen = _outlet().last_seen_at

    offline = {
        "device_id": NET_DEVICE,
        "network_mode": "offline",
        "connection_type": "access_point",
        "ip_address": "192.168.4.1",
        "hardware_status": "DISCONNECTED",
        "relay_state": "ON",
    }
    response = _post(client, offline)
    assert response.status_code == 200

    body = response.get_json()["device"]
    assert body["network_mode"] == "offline"
    assert body["connection_type"] == "access_point"
    assert body["ip_address"] == "192.168.4.1"
    assert body["hardware_status"] == "DISCONNECTED"
    assert body["relay_state"] == "on"
    assert body["online"] is False

    with app.app_context():
        outlet = _outlet()
        assert outlet.network_mode == "offline"
        assert outlet.connection_type == "access_point"
        assert outlet.hardware_connected is False
        assert outlet.relay_state == "on"
        assert outlet.connection_status == "offline"
        assert outlet.is_online is False
        assert outlet.connection_label == "Offline"
        assert outlet.last_seen_at >= first_seen
        assert _count() == 1


def test_repeated_heartbeats_update_one_row(app, client):
    register(client, "alice")
    assert _register_device(client).status_code == 201

    for _ in range(3):
        assert _post(client, ONLINE).status_code == 200

    with app.app_context():
        assert _count() == 1
        outlet_id = _outlet().id

    assert _post(
        client,
        {
            "device_id": NET_DEVICE.lower(),
            "network_mode": "online",
            "connection_type": "router",
            "relay_state": "ON",
        },
    ).status_code == 200

    with app.app_context():
        assert _count() == 1
        assert _outlet().id == outlet_id
        assert _outlet().relay_state == "on"


def test_heartbeat_still_rejects_unknown_and_unregistered_devices(app, client):
    assert _post(client, ONLINE).status_code == 404

    with app.app_context():
        assert _count() == 0

    register(client, "alice")
    assert _register_device(client).status_code == 201

    response = _post(
        client,
        {
            "device_id": "PA-NET-99999999",
            "network_mode": "online",
            "connection_type": "router",
        },
    )
    assert response.status_code == 404
    assert response.get_json()["error"] == "unknown_device"
    with app.app_context():
        assert _count() == 1


def test_heartbeat_validates_new_payload_fields(app, client):
    missing_id = _post(
        client, {"network_mode": "online", "connection_type": "router"}
    )
    assert missing_id.status_code == 400
    assert missing_id.get_json()["error"] == "invalid_request"

    register(client, "alice")
    assert _register_device(client).status_code == 201

    bad_values = [
        {"device_id": NET_DEVICE, "network_mode": "sometimes"},
        {"device_id": NET_DEVICE, "connection_type": "bluetooth"},
        {"device_id": NET_DEVICE, "hardware_status": "MAYBE"},
        {"device_id": NET_DEVICE, "relay_state": "MAYBE"},
        {"device_id": NET_DEVICE, "hardware_status": "CONNECTED", "hardware_connected": False},
        {"device_id": NET_DEVICE, "relay_state": "ON", "outlet_state": False},
    ]
    for payload in bad_values:
        response = _post(client, payload)
        assert response.status_code == 400, payload
        assert response.get_json()["ok"] is False

    legacy = _post(
        client,
        {
            "device_id": NET_DEVICE,
            "outlet_state": True,
            "hardware_connected": True,
        },
    )
    # The earlier boolean field names still work.
    assert legacy.status_code == 200


def test_website_user_routes_and_ownership_still_work(app, client):
    register(client, "alice")
    assert _register_device(client).status_code == 201
    assert _post(client, ONLINE).status_code == 200

    dashboard = client.get("/dashboard/")
    devices_page = client.get("/devices/")
    api_devices = client.get("/api/devices")
    api_device = client.get(f"/api/devices/{NET_DEVICE}")

    assert dashboard.status_code == 200
    assert devices_page.status_code == 200
    assert api_devices.status_code == 200
    assert api_device.status_code == 200
    assert NET_DEVICE in dashboard.get_data(as_text=True)
    assert NET_DEVICE in devices_page.get_data(as_text=True)
    assert api_devices.get_json()["devices"][0]["device_id"] == NET_DEVICE
    assert api_device.get_json()["device"]["device_id"] == NET_DEVICE

    # The shared device secret must never reach a browser response.
    for response in (dashboard, devices_page, api_devices, api_device):
        assert DEVICE_KEY not in response.get_data(as_text=True)

    # A second account cannot read or heartbeat-own the first account's device.
    second = app.test_client()
    register(second, "bob")
    assert second.get("/api/devices").get_json()["devices"] == []
    assert second.get(f"/api/devices/{NET_DEVICE}").status_code == 404

    # Browser routes still require a session.
    anonymous = app.test_client()
    assert anonymous.get("/devices/").status_code == 302


def test_pa_net_device_id_works_with_registration_qr(client):
    register(client, "alice")

    from tests.support import form_post

    response = form_post(
        client,
        "/devices/add",
        {"device_id": NET_DEVICE.lower(), "action": "preview"},
        csrf_path="/devices/add",
    )

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert NET_DEVICE in body
    assert f"/register?device_id={NET_DEVICE}" in body
