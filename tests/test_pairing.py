"""Server-side pairing validation, registration, and ownership rules."""

from app.extensions import db
from app.models import SmartOutlet
from app.services import qr_service
from tests.support import api_post, provision, register

VALIDATE = "/api/pairing/validate"
REGISTER = "/api/pairing/register"


def _outlet(app, outlet_id):
    return db.session.get(SmartOutlet, outlet_id)


def test_validate_then_register_consumes_single_use_code(app, client):
    register(client, "alice")
    info = provision(app)

    response = api_post(client, VALIDATE, {"payload": info["payload"]})
    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["can_register"] is True
    assert body["device"]["device_id"] == info["device_id"]
    assert body["device"]["registered"] is False

    response = api_post(client, REGISTER, {"payload": info["payload"]})
    assert response.status_code == 201
    body = response.get_json()
    assert body["ok"] is True
    assert body["device"]["registered"] is True
    assert body["device"]["connection_status"] == "unknown"
    assert body["device"]["readings"] is None

    with app.app_context():
        outlet = _outlet(app, info["outlet_id"])
        assert outlet.owner.username == "alice"
        assert outlet.channel == 1
        assert outlet.pairing_state == "registered"
        assert outlet.pairing_token_hash is None
        assert outlet.last_seen_at is None

    # The same label cannot be claimed twice.
    response = api_post(client, VALIDATE, {"payload": info["payload"]})
    assert response.status_code == 409
    assert response.get_json()["error"] == "already_registered"


def test_manual_credentials_follow_the_same_rules(app, client):
    register(client, "alice")
    info = provision(app)

    credentials = {
        "device_id": info["device_id"].lower(),
        "pairing_code": f"{info['code'][:4]}-{info['code'][4:8]}-{info['code'][8:]}",
    }

    response = api_post(client, VALIDATE, credentials)
    assert response.status_code == 200

    response = api_post(client, REGISTER, credentials)
    assert response.status_code == 201


def test_revoked_outlet_is_rejected(app, client):
    register(client, "alice")
    info = provision(app)

    with app.app_context():
        outlet = _outlet(app, info["outlet_id"])
        outlet.pairing_state = "revoked"
        db.session.commit()

    response = api_post(client, VALIDATE, {"payload": info["payload"]})
    assert response.status_code == 403
    assert response.get_json()["error"] == "revoked"


def test_unknown_device_is_rejected(client):
    register(client, "alice")
    payload = qr_service.build_payload(
        "PA-ESP32-9999", qr_service.generate_pairing_code()
    )

    response = api_post(client, VALIDATE, {"payload": payload})
    assert response.status_code == 404
    assert response.get_json()["error"] == "unknown_device"


def test_wrong_pairing_code_is_rejected(app, client):
    register(client, "alice")
    info = provision(app)
    wrong = qr_service.build_payload(
        info["device_id"], qr_service.generate_pairing_code()
    )

    response = api_post(client, VALIDATE, {"payload": wrong})
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_code"


def test_claimed_outlet_cannot_be_registered_by_another_account(app, client):
    register(client, "alice")
    info = provision(app)
    assert api_post(client, REGISTER, {"payload": info["payload"]}).status_code == 201

    second = app.test_client()
    register(second, "bob")

    response = api_post(second, VALIDATE, {"payload": info["payload"]})
    assert response.status_code == 409
    assert response.get_json()["error"] == "already_registered"

    response = api_post(second, REGISTER, {"payload": info["payload"]})
    assert response.status_code == 409


def test_device_reads_are_owner_scoped(app, client):
    register(client, "alice")
    info = provision(app)
    assert api_post(client, REGISTER, {"payload": info["payload"]}).status_code == 201

    response = client.get("/api/devices")
    assert response.status_code == 200
    devices = response.get_json()["devices"]
    assert [device["device_id"] for device in devices] == [info["device_id"]]
    assert "pairing_code" not in response.get_data(as_text=True)

    response = client.get(f"/api/devices/{info['device_id']}")
    assert response.status_code == 200

    second = app.test_client()
    register(second, "bob")

    response = second.get("/api/devices")
    assert response.status_code == 200
    assert response.get_json()["devices"] == []

    response = second.get(f"/api/devices/{info['device_id']}")
    assert response.status_code == 404
    assert response.get_json()["error"] == "not_found"


def test_malformed_payloads_are_rejected_without_leaking_details(client):
    register(client, "alice")

    bad_payloads = [
        {"payload": "https://example.com"},
        {"payload": "POWERABLE:PAIR\nVERSION:1\nDEVICE_ID:PA-ESP32-0001"},
        {"payload": "POWERABLE:PAIR\nVERSION:2\nDEVICE_ID:PA-ESP32-0001\nPAIRING_CODE:ABCDEFGHIJKLMNOP"},
        {"payload": "POWERABLE:PAIR\nVERSION:1\nDEVICE_ID:PA-ESP32-0001\nPAIRING_CODE:ABCDEFGHIJKLMNOP\nEXTRA:1"},
        {},
        {"device_id": "PA-ESP32-0001"},
    ]

    for payload in bad_payloads:
        response = api_post(client, VALIDATE, payload)
        assert response.status_code == 400
        body = response.get_json()
        assert body["ok"] is False
        assert body["error"] in {"invalid_qr", "missing_credentials"}


def test_repeated_failures_are_throttled(client):
    register(client, "alice")

    for _ in range(12):
        response = api_post(client, VALIDATE, {"payload": "not-a-powerable-label"})
        assert response.status_code == 400

    response = api_post(client, VALIDATE, {"payload": "not-a-powerable-label"})
    assert response.status_code == 429
    assert response.get_json()["error"] == "too_many_attempts"


def test_outlet_limit_is_enforced(app, client):
    register(client, "alice")
    app.config["MAX_OUTLETS_PER_USER"] = 1

    first = provision(app, "PA-ESP32-0001")
    second = provision(app, "PA-ESP32-0002")

    assert api_post(client, REGISTER, {"payload": first["payload"]}).status_code == 201

    response = api_post(client, REGISTER, {"payload": second["payload"]})
    assert response.status_code == 409
    assert response.get_json()["error"] == "limit_reached"
