"""Installer CLI commands for physical outlet provisioning and admin setup.

Examples (PowerShell):

    .\\.venv\\Scripts\\python.exe -m flask --app run.py provision-outlet --device-id PA-ESP32-0001
    .\\.venv\\Scripts\\python.exe -m flask --app run.py list-outlets
    .\\.venv\\Scripts\\python.exe -m flask --app run.py create-admin --username installer

The pairing code is printed once and stored only as a hash. The QR image
is written under instance/qr/ (git-ignored) by default.

Admin accounts are created only from this local CLI, never from the public
registration form.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
import segno
from flask import current_app

from app.extensions import db
from app.models import SmartOutlet, User
from app.services.admin_service import (
    AdminError,
    create_or_promote_admin,
    revoke_outlet as revoke_outlet_service,
)
from app.services.device_service import PairingError, provision_device


def _qr_path(qr_dir, device_id):
    directory = Path(qr_dir) if qr_dir else Path(current_app.instance_path) / "qr"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{device_id}.png"


def register_cli(app):
    @app.cli.command("provision-outlet")
    @click.option("--device-id", required=True, help="Device ID printed on the label, e.g. PA-ESP32-0001.")
    @click.option("--device-type", default="Smart Power Outlet", show_default=True)
    @click.option("--qr-dir", default=None, help="Folder for the QR image (default: instance/qr).")
    @click.option("--no-qr", is_flag=True, help="Do not write a QR image.")
    def provision_outlet(device_id, device_type, qr_dir, no_qr):
        """Create/rotate a single-use pairing label for a physical outlet."""

        try:
            outlet, code, payload = provision_device(
                device_id, device_type=device_type
            )
        except PairingError as error:
            raise click.ClickException(error.message) from error

        click.echo(f"Device ID : {outlet.device_uid}")
        click.echo(f"Type      : {outlet.device_type}")
        click.echo(f"Pair code : {code}")
        click.echo("This code is shown once and stored only as a hash.")

        if not no_qr:
            path = _qr_path(qr_dir, outlet.device_uid)
            segno.make(payload, error="m").save(
                str(path), scale=8, border=4, dark="#0a1220", light="#ffffff"
            )
            click.echo(f"QR image  : {path}")

        click.echo("Register it from /devices/pair while signed in.")

    @app.cli.command("list-outlets")
    def list_outlets():
        """List every outlet known to this database (admin view)."""

        outlets = db.session.scalars(
            db.select(SmartOutlet).order_by(SmartOutlet.device_uid)
        ).all()

        if not outlets:
            click.echo("No outlets provisioned yet.")
            return

        click.echo(
            f"{'DEVICE ID':22} {'STATE':13} {'OWNER':12} {'CH':>3} {'LAST SEEN':16}"
        )
        for outlet in outlets:
            owner = outlet.owner.username if outlet.owner else "-"
            last_seen = (
                outlet.last_seen_at.strftime("%Y-%m-%d %H:%M")
                if outlet.last_seen_at
                else "never"
            )
            click.echo(
                f"{outlet.device_uid:22} {outlet.pairing_state:13} {owner:12} "
                f"{str(outlet.channel or '-'):>3} {last_seen:16}"
            )

    @app.cli.command("revoke-outlet")
    @click.option("--device-id", required=True)
    @click.option("--yes", is_flag=True, help="Confirm revocation.")
    def revoke_outlet(device_id, yes):
        """Disable an outlet and remove it from any customer dashboard."""

        normalized = device_id.strip().upper()
        outlet = db.session.scalar(
            db.select(SmartOutlet).filter_by(device_uid=normalized)
        )
        if outlet is None:
            raise click.ClickException("No outlet with that device ID.")
        if not yes:
            raise click.ClickException("Re-run with --yes to confirm revocation.")

        try:
            revoke_outlet_service(
                outlet,
                actor=None,
                reason="Revoked from the installer CLI.",
            )
        except AdminError as error:
            raise click.ClickException(error.message) from error

        click.echo(f"Revoked {outlet.device_uid}.")

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True, help="Admin login name.")
    @click.option(
        "--password",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="Admin password (prompted, never stored in shell history).",
    )
    def create_admin(username, password):
        """Create an admin account, or promote/reset an existing one."""

        try:
            user, created = create_or_promote_admin(username, password)
        except AdminError as error:
            raise click.ClickException(error.message) from error

        if created:
            click.echo(f"Created admin account: {user.username}")
        else:
            click.echo(
                f"Promoted/reset existing account: {user.username} (role: {user.role})"
            )
        click.echo("Sign in at /auth/login and open /admin.")

    @app.cli.command("bootstrap-admin")
    def bootstrap_admin():
        """Create the first admin from ADMIN_USERNAME/ADMIN_PASSWORD.

        Render's free tier has no shell access, so the deploy start script
        runs this command. It skips silently when the variables are unset or
        when an active admin already exists, so later restarts never reset a
        password that was changed in the admin console.
        """

        username = (os.environ.get("ADMIN_USERNAME") or "").strip()
        password = os.environ.get("ADMIN_PASSWORD") or ""

        if not username or not password:
            click.echo(
                "ADMIN_USERNAME/ADMIN_PASSWORD are not set; "
                "skipping admin bootstrap."
            )
            return

        existing_admin = db.session.scalar(
            db.select(User)
            .filter_by(role="admin", is_active_flag="active")
            .limit(1)
        )
        if existing_admin is not None:
            click.echo(
                "An active admin already exists; skipping admin bootstrap."
            )
            return

        try:
            user, created = create_or_promote_admin(username, password)
        except AdminError as error:
            raise click.ClickException(error.message) from error

        action = "Created" if created else "Promoted"
        click.echo(f"{action} admin account: {user.username}")
