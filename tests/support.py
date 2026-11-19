"""Shared pytest helpers for the PowerAble test suite."""

from __future__ import annotations

from app.extensions import db
from app.models.user import User
from app.services.device_service import provision_device

DEFAULT_PASSWORD = "correct-horse-9"


def create_user(
    app,
    username="admin",
    password=DEFAULT_PASSWORD,
    role="user",
    active=True,
):
    """Create an account directly, optionally with the admin role."""

    with app.app_context():
        user = User(
            username=username,
            role=role,
            is_active_flag="active" if active else "disabled",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def get_csrf(client, path="/"):
    """Render a page so the session has a CSRF token, then return it."""

    client.get(path)
    with client.session_transaction() as session:
        return session.get("_csrf_token", "")


def form_post(client, path, data, csrf_path="/"):
    payload = dict(data)
    payload["csrf_token"] = get_csrf(client, csrf_path)
    return client.post(path, data=payload)


def register(client, username="alice", password=DEFAULT_PASSWORD):
    return form_post(
        client,
        "/auth/register",
        {
            "username": username,
            "password": password,
            "confirm_password": password,
        },
        csrf_path="/auth/register",
    )


def sign_in(client, username="alice", password=DEFAULT_PASSWORD):
    return form_post(
        client,
        "/auth/login",
        {"username": username, "password": password},
        csrf_path="/auth/login",
    )


def sign_out(client):
    return form_post(client, "/auth/logout", {})


def api_post(client, path, payload):
    return client.post(
        path,
        json=payload,
        headers={"X-CSRF-Token": get_csrf(client, "/")},
    )


def provision(app, device_id="PA-ESP32-0001"):
    """Provision a physical outlet directly through the service layer.

    Returns a dict with outlet_id, device_id, code, payload, and device_secret.
    """

    with app.app_context():
        outlet, code, payload, device_secret = provision_device(device_id)
        return {
            "outlet_id": outlet.id,
            "device_id": outlet.device_uid,
            "code": code,
            "payload": payload,
            "device_secret": device_secret,
        }
