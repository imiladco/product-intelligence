#!/usr/bin/env bash
# Collect static, apply migrations, then serve.
#
# Migrations run here rather than in a separate step because staging runs a
# single API replica, so there is no concurrent-migration hazard. With more
# than one replica this must move to a one-shot job.
set -euo pipefail

echo "==> collectstatic"
python manage.py collectstatic --noinput --clear

echo "==> migrate"
python manage.py migrate --noinput

echo "==> gunicorn"
exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --error-logfile - \
    --capture-output
