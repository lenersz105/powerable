"""Admin authority: access control, labels, outlet lifecycle, accounts, and audit."""

import re

import pytest

from app.extensions import db
from app.models import AdminAction, DeviceEvent, SmartOutlet, User
from app.services import qr_service
from app.services.admin_service import AdminError, delete_user, set_user_role, set_user_status
from tests.support import (
    DEFAULT_PASSWORD,
    api_post,
    create_user,
    form_post,
    provision,
    register,
    sign_in,
)

CODE_PATTERN = re.compile(r'data-pairing-code="([A-Z2-7]{16,32})"')


def login_admin(app, client, username="root"):
    user_id = create_user(app, username, role="admin")
    response = sign_in(client, username)
    assert response.status_code == 302
    return user_id


def outlet_state(app, outlet_id):
    with app.app_context():
        outlet = db.session.get(SmartOutlet, outlet_id)
        if outlet is None:
            return None
        return {
            "device_id": outlet.device_uid,
            "name": outlet.outlet_name,
            "state": outlet.pairing_state,
            "owner_id": outlet.owner_id,
            "channel": outlet.channel,
            "hash": outlet.pairing_token_hash,
            "registered_at": outlet.registered_at,
            "connection": outlet.connection_status,
            "last_seen": outlet.last_seen_at,
            "type": outlet.device_type,
        }


def user_state(app, user_id):
    with app.app_context():
        user = db.session.get(User, user_id)
        if user is None:
            return None
        return {
            "username": user.username,
            "role": user.role,
            "status": user.is_active_flag,
        }


def admin_actions(app):
    with app.app_context():
        return [
            (row.action, row.target, row.description)
            for row in db.session.scalars(
                db.select(AdminAction).order_by(AdminAction.id)
            )
        ]


# ----------------------------------------------------------------------
# Access control
# ----------------------------------------------------------------------


def test_admin_requires_sign_in(client):
    response = client.get("/admin/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_regular_user_is_forbidden(app, client):
    register(client, "alice")

    response = client.get("/admin/")
    assert response.status_code == 403

    response = form_post(
        client, "/admin/outlets/new", {"device_id": "PA-ESP32-0001"}
    )
    assert response.status_code == 403

    with app.app_context():
        assert db.session.scalar(db.select(AdminAction)) is None
        assert (
            db.session.scalar(
                db.select(SmartOutlet).filter_by(device_uid="PA-ESP32-0001")
            )
            is None
        )


def test_admin_overview_shows_admin_navigation(app, client):
    login_admin(app, client)

    response = client.get("/admin/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Admin console" in html
    assert "Generate QR label" in html
    assert 'href="/admin/"' in html
    assert ">Schedules<" not in html

    response = client.get("/admin/outlets")
    assert response.status_code == 200
    assert "Outlets &amp; labels" in response.get_data(as_text=True)

    create_user(app, "bob")
    provision(app, "PA-ESP32-0003")
    for path in (
        "/admin/outlets/new",
        "/admin/users",
        "/admin/activity",
        "/admin/outlets?search=ESP32&state=available",
    ):
        response = client.get(path)
        assert response.status_code == 200, path


def test_admin_mutations_require_csrf(app, client):
    login_admin(app, client)

    response = client.post("/admin/outlets/new", data={"device_id": "PA-ESP32-0002"})
    assert response.status_code == 400

    with app.app_context():
        assert (
            db.session.scalar(
                db.select(SmartOutlet).filter_by(device_uid="PA-ESP32-0002")
            )
            is None
        )


# ----------------------------------------------------------------------
# QR labels and outlets
# ----------------------------------------------------------------------


def test_admin_generates_label_and_stores_only_a_hash(app, client):
    login_admin(app, client)

    response = form_post(
        client,
        "/admin/outlets/new",
        {
            "device_id": "pa-esp32-0001",
            "device_type": "Smart Plug",
        },
        csrf_path="/admin",
    )
    assert response.status_code == 200

    html = response.get_data(as_text=True)
    assert "PA-ESP32-0001" in html
    assert response.headers["Cache-Control"].startswith("no-store")
    assert response.headers["Pragma"] == "no-cache"

    match = CODE_PATTERN.search(html)
    assert match, "the one-time pairing code must be rendered"
    code = match.group(1)

    with app.app_context():
        outlet = db.session.scalar(
            db.select(SmartOutlet).filter_by(device_uid="PA-ESP32-0001")
        )
        assert outlet is not None
        assert outlet.pairing_state == "available"
        assert outlet.device_type == "Smart Plug"
        assert outlet.owner_id is None
        assert outlet.connection_status == "unknown"
        assert outlet.last_seen_at is None
        assert outlet.pairing_token_hash
        assert outlet.pairing_token_hash != code
        assert code not in outlet.pairing_token_hash
        assert qr_service.verify_pairing_code(outlet.pairing_token_hash, code)

        action = db.session.scalar(
            db.select(AdminAction).filter_by(action="outlet.provisioned")
        )
        assert action is not None
        assert action.target == "PA-ESP32-0001"
        assert code not in action.description

        event = db.session.scalar(
            db.select(DeviceEvent).filter_by(event_type="provisioned")
        )
        assert event is not None

    response = client.get("/admin/outlets")
    assert response.status_code == 200
    assert "PA-ESP32-0001" in response.get_data(as_text=True)


def test_admin_outlet_lifecycle_and_rotated_code(app, client):
    login_admin(app, client)
    info = provision(app, "PA-ESP32-0005")
    outlet_id = info["outlet_id"]

    response = form_post(
        client,
        f"/admin/outlets/{outlet_id}/rename",
        {"outlet_name": "Kitchen counter"},
        csrf_path="/admin",
    )
    assert response.status_code == 302
    assert outlet_state(app, outlet_id)["name"] == "Kitchen counter"

    response = form_post(
        client,
        f"/admin/outlets/{outlet_id}/rotate",
        {},
        csrf_path="/admin",
    )
    assert response.status_code == 200
    match = CODE_PATTERN.search(response.get_data(as_text=True))
    assert match
    new_code = match.group(1)
    assert new_code != info["code"]

    state = outlet_state(app, outlet_id)
    assert state["state"] == "available"
    assert qr_service.verify_pairing_code(state["hash"], new_code)
    assert not qr_service.verify_pairing_code(state["hash"], info["code"])

    response = form_post(
        client,
        f"/admin/outlets/{outlet_id}/revoke",
        {"reason": "Returned by customer"},
        csrf_path="/admin",
    )
    assert response.status_code == 302

    state = outlet_state(app, outlet_id)
    assert state["state"] == "revoked"
    assert state["hash"] is None
    assert state["owner_id"] is None
    assert state["channel"] is None
    assert state["last_seen"] is None

    response = form_post(
        client,
        f"/admin/outlets/{outlet_id}/delete",
        {"confirm_device_id": "wrong-id"},
        csrf_path="/admin",
    )
    assert response.status_code == 302
    assert outlet_state(app, outlet_id) is not None

    response = form_post(
        client,
        f"/admin/outlets/{outlet_id}/delete",
        {"confirm_device_id": info["device_id"]},
        csrf_path="/admin",
    )
    assert response.status_code == 302
    assert outlet_state(app, outlet_id) is None

    with app.app_context():
        assert db.session.scalar(db.select(DeviceEvent)) is None
        actions = {row.action for row in db.session.scalars(db.select(AdminAction))}
    assert {"outlet.renamed", "outlet.revoked", "outlet.deleted"} <= actions


def test_registered_outlet_needs_revocation_before_a_new_label(app, client):
    login_admin(app, client)
    info = provision(app, "PA-ESP32-0006")

    register_client = app.test_client()
    register(register_client, "alice")
    assert (
        api_post(register_client, "/api/pairing/register", {"payload": info["payload"]})
        .status_code
        == 201
    )

    response = form_post(
        client,
        f"/admin/outlets/{info['outlet_id']}/rotate",
        {},
        csrf_path="/admin",
    )
    assert response.status_code == 302

    state = outlet_state(app, info["outlet_id"])
    assert state["state"] == "registered"
    assert state["owner_id"] is not None
    assert state["hash"] is None


# ----------------------------------------------------------------------
# Account authority
# ----------------------------------------------------------------------


def test_admin_cannot_change_own_role_status_or_delete_self(app, client):
    admin_id = login_admin(app, client, "owner")

    response = form_post(
        client,
        f"/admin/users/{admin_id}/role",
        {"role": "user"},
        csrf_path="/admin",
    )
    assert response.status_code == 302
    assert user_state(app, admin_id)["role"] == "admin"

    response = form_post(
        client,
        f"/admin/users/{admin_id}/status",
        {"status": "disabled"},
        csrf_path="/admin",
    )
    assert response.status_code == 302
    assert user_state(app, admin_id)["status"] == "active"

    response = form_post(
        client,
        f"/admin/users/{admin_id}/delete",
        {"confirm_username": "owner"},
        csrf_path="/admin",
    )
    assert response.status_code == 302
    assert user_state(app, admin_id) is not None


def test_last_admin_guard_is_enforced_in_the_service_layer(app):
    admin_id = create_user(app, "solo", role="admin")

    class GhostActor:
        id = 999999
        username = "ghost"
        is_authenticated = True
        is_admin = True

    ghost = GhostActor()

    with app.app_context():
        user = db.session.get(User, admin_id)
        with pytest.raises(AdminError) as role_error:
            set_user_role(ghost, user, "user")
        assert role_error.value.code == "last_admin"

        with pytest.raises(AdminError) as status_error:
            set_user_status(ghost, user, "disabled")
        assert status_error.value.code == "last_admin"

        with pytest.raises(AdminError) as delete_error:
            delete_user(ghost, user, "solo")
        assert delete_error.value.code == "last_admin"

        assert db.session.get(User, admin_id).role == "admin"


def test_admin_resets_password_and_disabled_session_is_ended(app, client):
    login_admin(app, client)
    bob_id = create_user(app, "bob")

    response = form_post(
        client,
        f"/admin/users/{bob_id}/password",
        {"password": "new-password-1", "confirm_password": "new-password-1"},
        csrf_path="/admin",
    )
    assert response.status_code == 302

    bob_client = app.test_client()
    assert sign_in(bob_client, "bob", "new-password-1").status_code == 302

    bob_api = app.test_client()
    assert sign_in(bob_api, "bob", "new-password-1").status_code == 302

    response = form_post(
        client,
        f"/admin/users/{bob_id}/status",
        {"status": "disabled"},
        csrf_path="/admin",
    )
    assert response.status_code == 302
    assert user_state(app, bob_id)["status"] == "disabled"

    # The next JSON request is refused without revealing the account state.
    response = bob_api.get("/api/devices")
    assert response.status_code == 403
    assert response.get_json()["error"] == "account_disabled"

    # The next HTML request ends the existing session and redirects.
    response = bob_client.get("/dashboard/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]

    fresh = app.test_client()
    response = sign_in(fresh, "bob", "new-password-1")
    assert response.status_code == 200
    assert "disabled" in response.get_data(as_text=True).lower()


def test_deleting_an_account_revokes_its_outlets(app, client):
    login_admin(app, client)
    bob_id = create_user(app, "bob")
    info = provision(app, "PA-ESP32-0007")

    bob_client = app.test_client()
    sign_in(bob_client, "bob")
    assert (
        api_post(bob_client, "/api/pairing/register", {"payload": info["payload"]})
        .status_code
        == 201
    )

    state = outlet_state(app, info["outlet_id"])
    assert state["owner_id"] == bob_id

    response = form_post(
        client,
        f"/admin/users/{bob_id}/delete",
        {"confirm_username": "bob"},
        csrf_path="/admin",
    )
    assert response.status_code == 302
    assert user_state(app, bob_id) is None

    state = outlet_state(app, info["outlet_id"])
    assert state["state"] == "revoked"
    assert state["owner_id"] is None
    assert state["hash"] is None

    with app.app_context():
        action = db.session.scalar(
            db.select(AdminAction).filter_by(action="user.deleted")
        )
        assert action is not None
        assert action.target == "bob"


def test_admin_audit_log_never_contains_secrets(app, client):
    login_admin(app, client)
    bob_id = create_user(app, "bob")

    password = "super-secret-42"
    form_post(
        client,
        f"/admin/users/{bob_id}/password",
        {"password": password, "confirm_password": password},
        csrf_path="/admin",
    )

    actions = admin_actions(app)
    assert actions
    assert all(password not in description for _action, _target, description in actions)
    assert all(password not in target for _action, target, _description in actions)
