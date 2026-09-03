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
# Access log format: identical to gunicorn's default except that the request
# line %(r)s is replaced by method, PATH and protocol.
#
# %(r)s logs the raw request target, which includes the query string. The
# Google OAuth callback necessarily receives ?code=...&state=... there, so the
# default format writes both secrets to the access log on every callback.
# %(U)s is the path alone, and %(q)s (the query string) is deliberately absent.
#
# Django's own LOGGING filters cannot help here: gunicorn configures the
# gunicorn.access logger itself with propagate=False, so those records never
# reach the root handler or its redaction filter.
#
# This applies to every route, not just the callback, because any future
# endpoint may receive a sensitive query parameter.
ACCESS_LOG_FORMAT='%(h)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s "%(f)s" "%(a)s"'

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --access-logformat "$ACCESS_LOG_FORMAT" \
    --error-logfile - \
    --capture-output
