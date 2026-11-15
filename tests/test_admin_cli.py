"""First-admin bootstrap used by the Render start command."""

from app.extensions import db
from app.models import User


def _admin(app):
    with app.app_context():
        return db.session.scalar(db.select(User).filter_by(role="admin"))


def test_bootstrap_admin_creates_once_and_never_resets_password(
    app, monkeypatch
):
    runner = app.test_cli_runner()
    monkeypatch.setenv("ADMIN_USERNAME", "installer")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse-9")

    first = runner.invoke(args=["bootstrap-admin"])
    assert first.exit_code == 0
    assert "Created admin account" in first.output

    admin = _admin(app)
    assert admin is not None
    assert admin.username == "installer"
    assert admin.is_admin is True

    # A later deploy with a different password must not reset the live one.
    monkeypatch.setenv("ADMIN_PASSWORD", "another-password-1")
    second = runner.invoke(args=["bootstrap-admin"])
    assert second.exit_code == 0
    assert "already exists" in second.output

    with app.app_context():
        user = db.session.scalar(db.select(User).filter_by(username="installer"))
        assert user.check_password("correct-horse-9") is True
        assert user.check_password("another-password-1") is False
        assert db.session.scalar(db.select(db.func.count(User.id))) == 1


def test_bootstrap_admin_skips_without_credentials(app, monkeypatch):
    monkeypatch.delenv("ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)

    result = app.test_cli_runner().invoke(args=["bootstrap-admin"])

    assert result.exit_code == 0
    assert "skipping admin bootstrap" in result.output
    assert _admin(app) is None
