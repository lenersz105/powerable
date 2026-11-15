"""Session-based CSRF protection and shared HTTP error handling.

PowerAble does not use Flask-WTF, so this module provides a small,
auditable CSRF implementation:

* A random token is stored in the signed Flask session.
* HTML forms send it in a hidden ``csrf_token`` field.
* JavaScript sends it in the ``X-CSRF-Token`` header.
* Every state-changing request is checked in ``before_request``.

The token is never written to logs.
"""

import hmac
import secrets

from flask import (
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, logout_user

from app.extensions import login_manager

CSRF_SESSION_KEY = "_csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf_token"


def csrf_token():
    """Return the session CSRF token, creating one when necessary."""

    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _wants_json():
    return request.path.startswith("/api/")


def _csrf_failure():
    message = "The security token is missing or expired. Refresh the page and try again."
    if _wants_json():
        return jsonify(ok=False, error="invalid_csrf", message=message), 400
    abort(400, description=message)


def init_security(app):
    """Register CSRF checks, view helpers, and friendly error pages."""

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def _block_disabled_accounts():
        """End existing sessions as soon as an account is disabled.

        Flask-Login loads the account from the session before the view runs,
        so a disabled user with an old session would otherwise stay signed
        in. We sign them out immediately and refuse API calls with JSON.
        """

        if request.endpoint == "static":
            return None
        # Flask-Login's UserMixin reports a disabled account as "not
        # authenticated", so test anonymity separately from status.
        if getattr(current_user, "is_anonymous", True):
            return None
        if getattr(current_user, "is_active", True):
            return None

        logout_user()
        message = "This account has been disabled. Contact the administrator."

        if _wants_json():
            return (
                jsonify(ok=False, error="account_disabled", message=message),
                403,
            )

        if request.endpoint == "auth.logout":
            flash("You have been signed out.", "info")
            return redirect(url_for("main.index"))
        if request.endpoint == "auth.login":
            return None

        flash(message, "error")
        return redirect(url_for("auth.login"))

    @app.before_request
    def _protect_state_changing_requests():
        if not current_app.config.get("CSRF_ENABLED", True):
            return None
        if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
            return None

        # Hardware requests are machine-to-machine and carry no browser
        # session, so CSRF does not apply. Every /api/device/* view is wrapped
        # in app.device_auth.require_device_key, which enforces the shared
        # X-Device-Key secret instead.
        if request.endpoint == "api.device_heartbeat" or request.path.startswith(
            "/api/device/"
        ):
            return None

        expected = session.get(CSRF_SESSION_KEY)
        supplied = request.headers.get(CSRF_HEADER) or request.form.get(CSRF_FIELD)

        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return _csrf_failure()
        return None

    @app.context_processor
    def _inject_user_helpers():
        return {"current_user": current_user}

    def _error_response(status, title, message):
        if _wants_json():
            return jsonify(ok=False, error=title.lower().replace(" ", "_"), message=message), status
        return (
            render_template(
                "errors/error.html",
                status=status,
                title=title,
                message=message,
            ),
            status,
        )

    @app.errorhandler(400)
    def _bad_request(error):
        message = getattr(error, "description", "The request could not be understood.")
        return _error_response(400, "Bad request", message)

    @app.errorhandler(401)
    def _unauthorized(error):
        return _error_response(401, "Sign in required", "Please sign in to continue.")

    @app.errorhandler(403)
    def _forbidden(error):
        return _error_response(
            403, "Not allowed", "You do not have permission to perform this action."
        )

    @app.errorhandler(404)
    def _not_found(error):
        return _error_response(404, "Page not found", "That page or device was not found.")

    @app.errorhandler(413)
    def _too_large(error):
        return _error_response(
            413, "Request too large", "The submitted data is larger than allowed."
        )

    @app.errorhandler(429)
    def _too_many(error):
        message = getattr(error, "description", "Too many attempts. Please wait and try again.")
        return _error_response(429, "Too many attempts", message)

    @app.errorhandler(500)
    def _server_error(error):
        # Do not echo exception details to the browser.
        return _error_response(
            500,
            "Something went wrong",
            "The local server could not complete the request. Check the terminal for details.",
        )

    @app.errorhandler(503)
    def _unavailable(error):
        return _error_response(
            503, "Temporarily unavailable", "The requested service is not available right now."
        )

    # Used by Flask-Login for HTML requests. API paths are handled above.
    @login_manager.unauthorized_handler
    def _login_required():
        if _wants_json():
            return (
                jsonify(
                    ok=False,
                    error="authentication_required",
                    message="Sign in to continue.",
                ),
                401,
            )
        return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))


__all__ = ["init_security", "csrf_token", "CSRF_HEADER", "CSRF_FIELD"]
