"""Application configuration.

Configuration is split by environment so development and future
production settings never get mixed up.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Folder one level above the "app" package (the project root).
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env BEFORE the config classes below read os.environ.
# Doing this here (rather than in app/__init__.py) guarantees that
# SECRET_KEY and DATABASE_URL from .env are available no matter how
# the app is started (python run.py, flask CLI, or tests).
load_dotenv(BASE_DIR / ".env")


def normalize_database_url(raw_url):
    """Return a SQLAlchemy URL for SQLite or Render Postgres.

    Render (and Heroku) hand out ``postgres://`` or ``postgresql://``
    connection strings. SQLAlchemy's default PostgreSQL driver is psycopg2,
    but this project installs psycopg 3, so the scheme is rewritten to
    ``postgresql+psycopg://``. An explicit driver in the URL is preserved.
    """

    url = (raw_url or "").strip()
    if not url:
        return "sqlite:///" + str(BASE_DIR / "instance" / "powerable.sqlite3")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


class Config:
    """Settings shared by every environment."""

    # Secret key: read from the .env file, never hardcode real secrets.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-fallback-key")

    # SQLite locally; managed Postgres on Render via DATABASE_URL.
    SQLALCHEMY_DATABASE_URI = normalize_database_url(
        os.environ.get("DATABASE_URL")
    )

    # SQLAlchemy 2.x requires this flag to be set explicitly.
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session security basics (Flask's built-in features).
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Set to 1 only when the site is served over HTTPS. The local HTTPS
    # helper uses its own certificate; cookies can stay non-secure there
    # because the certificate is self-signed and HTTP is still used for
    # desktop development.
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"

    # CSRF protection is implemented in app/security.py.
    CSRF_ENABLED = True

    # Reject oversized request bodies (QR payloads are under 1 KB).
    MAX_CONTENT_LENGTH = 1024 * 256

    # Pairing / device limits.
    MAX_OUTLETS_PER_USER = int(os.environ.get("MAX_OUTLETS_PER_USER", "16"))

    # Shared secret used by the ESP32 Network Handler when it calls the
    # machine-to-machine API. The environment variable always wins; the
    # fallback exists only for local development. ProductionConfig removes
    # the fallback so a deployed server cannot silently use the dev key.
    DEVICE_API_KEY = (
        os.environ.get("DEVICE_API_KEY") or "PowerAble_Network_2026_Rey_X82k9P"
    ).strip()

    # Self-registration QR links. PUBLIC_BASE_URL overrides automatic LAN
    # detection. Render injects RENDER_EXTERNAL_URL, so deployed QR codes point
    # at the public HTTPS address without hardcoding the service name.
    PUBLIC_BASE_URL = (
        os.environ.get("PUBLIC_BASE_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or ""
    ).strip() or None
    PUBLIC_PORT = int(os.environ.get("PUBLIC_PORT", os.environ.get("PORT", "5000")))

    # A device is shown as online only while its last heartbeat is fresh.
    DEVICE_HEARTBEAT_TIMEOUT_SECONDS = int(
        os.environ.get("DEVICE_HEARTBEAT_TIMEOUT_SECONDS", "60")
    )

    # Application-level metadata shown on the About page.
    APP_NAME = "PowerAble"


class DevelopmentConfig(Config):
    """Local development."""

    DEBUG = True


class TestingConfig(Config):
    """Isolated configuration for pytest."""

    TESTING = True
    DEBUG = False
    WTF_CSRF_ENABLED = False  # Flask-WTF is not used; kept for clarity.
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SERVER_NAME = "localhost"
    MAX_OUTLETS_PER_USER = 16
    # Deterministic, non-loopback address so QR assertions never depend on
    # the machine running the tests.
    PUBLIC_BASE_URL = "http://192.168.1.10:5000"
    PUBLIC_PORT = 5000
    DEVICE_HEARTBEAT_TIMEOUT_SECONDS = 60
    # Deterministic device key for tests; never used outside pytest.
    DEVICE_API_KEY = "PowerAble_Network_2026_Rey_X82k9P"


class ProductionConfig(Config):
    """Production settings.

    Required environment variables:

    * ``SECRET_KEY`` — long random value used to sign session cookies;
    * ``DATABASE_URL`` — Render Postgres connection string, or a SQLite path
      when ``ALLOW_SQLITE_IN_PRODUCTION=1`` (persistent-disk deployments).

    Recommended:

    * ``DEVICE_API_KEY`` — shared ESP32 secret (device routes return 503 when
      unset, so the API fails closed);
    * ``PUBLIC_BASE_URL`` — only needed for a custom domain. It otherwise
      falls back to Render's ``RENDER_EXTERNAL_URL``.
    """

    DEBUG = False

    # No development fallback in production: the device API stays closed
    # (503) until DEVICE_API_KEY is set in the server environment.
    DEVICE_API_KEY = (os.environ.get("DEVICE_API_KEY") or "").strip()

    # Render terminates TLS and forwards HTTP to Gunicorn. Secure cookies are
    # required in production; set SESSION_COOKIE_SECURE=0 only for a local
    # production-style smoke test over plain HTTP.
    SESSION_COOKIE_SECURE = (
        os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"
    )
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    PREFERRED_URL_SCHEME = "https"

    # Trust one proxy hop (Render) for X-Forwarded-Proto / Host / For.
    TRUST_PROXY_HEADERS = True

    # Recycle connections before Render Postgres drops idle ones.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }


# Simple mapping so the factory can pick a config by name.
config_by_name = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
