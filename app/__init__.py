"""PowerAble application factory.

create_app() builds a fresh Flask application every time it is called.
"""

import os
from pathlib import Path

from flask import Flask

# Importing config also loads the .env file (see app/config.py).
from app.config import config_by_name
from app.extensions import db, login_manager, migrate
from app.security import init_security


def _validate_production_config(app):
    """Fail fast when production would run with unsafe defaults."""

    secret = (app.config.get("SECRET_KEY") or "").strip()
    if not secret or secret == "dev-only-fallback-key" or len(secret) < 32:
        raise RuntimeError(
            "SECRET_KEY must be set to a random value of at least 32 "
            "characters in production."
        )

    database_uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if database_uri.startswith("sqlite") and os.environ.get(
        "ALLOW_SQLITE_IN_PRODUCTION"
    ) != "1":
        raise RuntimeError(
            "DATABASE_URL must point to a persistent database in production. "
            "Set ALLOW_SQLITE_IN_PRODUCTION=1 only when SQLite lives on a "
            "mounted persistent disk."
        )


def create_app(config_name=None):
    """Create and configure the PowerAble Flask application."""

    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "development")

    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(
        config_by_name.get(config_name, config_by_name["development"])
    )

    if config_name == "production":
        _validate_production_config(app)

    # Render (and similar hosts) terminate TLS and forward HTTP. ProxyFix
    # makes request.scheme and generated absolute URLs reflect the public
    # HTTPS address instead of the internal http://host:10000 hop.
    if app.config.get("TRUST_PROXY_HEADERS"):
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1
        )

    # Make sure the instance folder exists; SQLite lives there.
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # Initialize extensions with the app.
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        """Return the User for a session, or None if the ID is invalid."""

        from app.models.user import User

        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # Import models so Flask-Migrate can detect them.
    from app import models  # noqa: F401

    # Register blueprints (URL groups).
    from app.routes.admin import admin_bp
    from app.routes.api import api_bp
    from app.routes.auth import auth_bp
    from app.routes.automation import automation_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.devices import devices_bp
    from app.routes.main import main_bp
    from app.routes.monitoring import monitoring_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(dashboard_bp, url_prefix="/dashboard")
    app.register_blueprint(devices_bp, url_prefix="/devices")
    app.register_blueprint(monitoring_bp, url_prefix="/monitoring")
    app.register_blueprint(automation_bp, url_prefix="/automation")
    app.register_blueprint(api_bp, url_prefix="/api")
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # CSRF, JSON/HTML error handling, and login redirects.
    init_security(app)

    # Flask CLI commands for installer provisioning.
    from app.cli import register_cli

    register_cli(app)

    return app
