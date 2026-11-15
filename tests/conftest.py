"""Pytest fixtures: a fresh isolated application and in-memory database."""

import pytest

from app import create_app
from app.extensions import db


@pytest.fixture
def app():
    application = create_app("testing")

    # Create the schema, then release the context. Holding a context for the
    # whole test would let Flask-Login cache current_user across requests.
    with application.app_context():
        db.create_all()

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
