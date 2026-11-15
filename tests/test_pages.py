"""Rendered pages: scanner wiring, honest empty states, and real outlet data."""

from tests.support import api_post, provision, register


def _claim(client, info):
    response = api_post(
        client, "/api/pairing/register", {"payload": info["payload"]}
    )
    assert response.status_code == 201


def test_pair_page_requires_authentication(client):
    response = client.get("/devices/pair")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_pair_page_wires_camera_and_manual_fallback(client):
    register(client, "alice")
    response = client.get("/devices/pair")
    assert response.status_code == 200

    body = response.get_data(as_text=True)
    assert "data-pairing-page" in body
    assert "/api/pairing/validate" in body
    assert "/api/pairing/register" in body
    assert "vendor/jsQR.js" in body
    assert "js/qr-scanner.js" in body
    assert "js/pairing.js" in body
    assert "Enter device details manually" in body
    assert "data-secure-warning" in body


def test_manual_documents_https_camera_requirement(client):
    response = client.get("/manual")
    assert response.status_code == 200

    body = response.get_data(as_text=True)
    assert 'id="camera"' in body
    assert "scripts/serve_https.py" in body
    assert "https://" in body
    assert "self-signed" in body


def test_automation_shows_honest_empty_state(client):
    register(client, "alice")
    response = client.get("/automation/")
    assert response.status_code == 200

    body = response.get_data(as_text=True)
    assert "No outlets to schedule yet" in body
    assert "Scheduling is not saving yet" in body


def test_automation_lists_registered_outlets(app, client):
    register(client, "alice")
    info = provision(app)
    _claim(client, info)

    response = client.get("/automation/")
    body = response.get_data(as_text=True)
    assert info["device_id"] in body
    assert "Outlets available to schedule" in body
    assert "CH 1" in body


def test_home_marks_placeholder_readings_as_missing(app, client):
    register(client, "alice")
    info = provision(app)
    _claim(client, info)

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert "Waiting for first reading" in body
    assert "No verified telemetry yet" in body
    assert "zeros below are placeholders" in body
    assert "0.00" in body
    assert 'data-state="missing"' in body


def test_devices_page_states_registration_is_not_connection(app, client):
    register(client, "alice")
    info = provision(app)
    _claim(client, info)

    response = client.get("/devices/")
    body = response.get_data(as_text=True)

    assert info["device_id"] in body
    assert "Registered does not mean connected" in body
    assert "Waiting for first reading" in body


def test_about_page_reflects_pairing_stage(client):
    response = client.get("/about")
    body = response.get_data(as_text=True)

    assert "Secure QR pairing" in body
    assert "Stage 2" in body
