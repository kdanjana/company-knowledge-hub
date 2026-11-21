#!/bin/sh
# Start-up script for the backend image.
#   web     -> apply migrations, collect static files, start gunicorn
#   worker  -> process audit runs queued from the UI
#   other   -> run that command (e.g. "python manage.py makemigrations")
set -e

case "$1" in
  web)
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    # gthread = several threads per worker process, so a streaming chat answer does not block other users.
    # No gunicorn access log: Django logs every request itself (JSON, with request IDs; config/observability.py).
    exec gunicorn config.wsgi:application \
      --bind 0.0.0.0:8000 \
      --worker-class gthread \
      --workers "${GUNICORN_WORKERS:-2}" \
      --threads "${GUNICORN_THREADS:-8}" \
      --timeout 180
    ;;
  worker)
    exec python manage.py run_worker
    ;;
  *)
    exec "$@"
    ;;
esac
