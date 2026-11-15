"""Authentication, session, and CSRF behaviour."""

from app.extensions import db
from app.models import User
from tests.support import (
    DEFAULT_PASSWORD,
    form_post,
    get_csrf,
    register,
    sign_in,
    sign_out,
)


def test_registration_creates_user_with_hashed_password(app, client):
    response = register(client, "alice")
    assert response.status_code == 302

    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(username="alice"))
        assert user is not None
        assert user.password_hash != DEFAULT_PASSWORD
        assert user.check_password(DEFAULT_PASSWORD)
        assert user.is_active_flag == "active"


def test_registration_rejects_duplicate_username(app, client):
    register(client, "alice")
    sign_out(client)

    response = register(client, "alice")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "already taken" in body


def test_login_and_logout(client):
    register(client, "alice")
    sign_out(client)

    response = sign_in(client, "alice")
    assert response.status_code == 302

    # Protected page is reachable while signed in.
    assert client.get("/devices/").status_code == 200

    response = sign_out(client)
    assert response.status_code == 302
    assert client.get("/devices/").status_code == 302


def test_login_rejects_wrong_password(client):
    register(client, "alice")
    sign_out(client)

    response = sign_in(client, "alice", "not-the-password")
    assert response.status_code == 200
    assert "Incorrect username or password" in response.get_data(as_text=True)


def test_login_requires_csrf_token(client):
    register(client, "alice")
    sign_out(client)

    client.get("/auth/login")
    response = client.post(
        "/auth/login",
        data={"username": "alice", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 400
    assert "security token" in response.get_data(as_text=True).lower()


def test_api_post_requires_csrf_header(client):
    register(client, "alice")

    response = client.post(
        "/api/pairing/validate", json={"payload": "POWERABLE:PAIR"}
    )
    assert response.status_code == 400
    data = response.get_json()
    assert data["ok"] is False
    assert data["error"] == "invalid_csrf"


def test_protected_page_redirects_anonymous(client):
    response = client.get("/devices/")
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


def test_api_returns_json_401_for_anonymous(client):
    response = client.get("/api/devices")
    assert response.status_code == 401
    data = response.get_json()
    assert data["ok"] is False
    assert data["error"] == "authentication_required"


def test_logout_is_post_only(client):
    register(client, "alice")
    response = client.get("/auth/logout")
    assert response.status_code == 405


def test_csrf_token_is_stable_within_a_session(client):
    first = get_csrf(client, "/")
    second = get_csrf(client, "/")
    assert first
    assert first == second
