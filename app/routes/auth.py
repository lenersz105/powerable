"""Auth blueprint: register, sign in, and sign out.

Only a username and a hashed password are stored. The session cookie is
signed with SECRET_KEY, and every POST is CSRF-protected by
app/security.py.
"""

import re
import time
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from app.extensions import db
from app.models.user import User

auth_bp = Blueprint("auth", __name__)

USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
LOGIN_WINDOW_SECONDS = 300
MAX_LOGIN_FAILURES = 8
LOGIN_ATTEMPTS_KEY = "login_failures"


def _safe_next(target):
    """Only allow redirects to local paths."""

    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


def _recent_login_failures():
    now = time.time()
    attempts = [
        stamp
        for stamp in session.get(LOGIN_ATTEMPTS_KEY, [])
        if isinstance(stamp, (int, float)) and now - stamp < LOGIN_WINDOW_SECONDS
    ]
    session[LOGIN_ATTEMPTS_KEY] = attempts
    return attempts


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    """Create an account with a hashed password."""

    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    values = {"username": ""}
    errors = []

    if request.method == "POST":
        values["username"] = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        confirmation = request.form.get("confirm_password", "")

        if not USERNAME_PATTERN.match(values["username"]):
            errors.append(
                "Username must be 3–32 characters: lowercase letters, numbers, dot, dash, or underscore."
            )
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        elif len(password) > 128:
            errors.append("Password must be 128 characters or fewer.")
        if password != confirmation:
            errors.append("The two passwords do not match.")

        if not errors:
            existing = db.session.scalar(
                db.select(User).filter_by(username=values["username"])
            )
            if existing:
                errors.append("That username is already taken.")

        if not errors:
            user = User(username=values["username"])
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            login_user(user)
            flash("Account created. You are signed in.", "success")
            return redirect(url_for("main.index"))

        for error in errors:
            flash(error, "error")

    return render_template(
        "auth/register.html",
        title="Create account",
        values=values,
    )


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Start a session for an existing account."""

    if current_user.is_authenticated:
        return redirect(url_for("main.index"))

    next_url = _safe_next(request.values.get("next"))
    username = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"

        if len(_recent_login_failures()) >= MAX_LOGIN_FAILURES:
            flash("Too many sign-in attempts. Wait five minutes and try again.", "error")
            return render_template(
                "auth/login.html", title="Sign in", username=username, next_url=next_url
            )

        user = db.session.scalar(db.select(User).filter_by(username=username))
        if user is None or not user.check_password(password):
            session[LOGIN_ATTEMPTS_KEY] = _recent_login_failures() + [time.time()]
            flash("Incorrect username or password.", "error")
        elif user.is_active_flag != "active":
            flash("This account is disabled.", "error")
        else:
            session.pop(LOGIN_ATTEMPTS_KEY, None)
            login_user(user, remember=remember)
            flash("Signed in.", "success")
            return redirect(next_url or url_for("main.index"))

    return render_template(
        "auth/login.html", title="Sign in", username=username, next_url=next_url
    )


@auth_bp.post("/logout")
@login_required
def logout():
    """End the current session. POST-only so links cannot sign users out."""

    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("main.index"))
