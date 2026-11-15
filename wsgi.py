"""Production WSGI entry point.

Render starts Gunicorn with ``wsgi:app`` (see scripts/render_start.sh).
Development keeps using ``python run.py``, which selects the development
configuration and the Flask reloader.
"""

from app import create_app

app = create_app("production")
