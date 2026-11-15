#!/usr/bin/env bash
# Render start command: migrate, bootstrap the first admin, then serve.
set -euo pipefail

# Render injects GUNICORN_CMD_ARGS containing its own --bind value. Clear it
# so the flags below are the single source of truth.
unset GUNICORN_CMD_ARGS

echo "Applying database migrations..."
flask --app wsgi:app db upgrade

echo "Checking first-admin bootstrap..."
flask --app wsgi:app bootstrap-admin

echo "Starting Gunicorn on port ${PORT:-10000}..."
exec gunicorn \
  --bind "0.0.0.0:${PORT:-10000}" \
  --workers "${WEB_CONCURRENCY:-1}" \
  --worker-class gthread \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --access-logfile - \
  --error-logfile - \
  wsgi:app
