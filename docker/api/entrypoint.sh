#!/usr/bin/env bash
# Serve the application, or run exactly the command given.
#
# Migrations and collectstatic are deliberately NOT here. Until M7 they ran on
# every container start, which meant a container restarted by the Docker daemon
# applied database migrations with nobody watching. They are now an explicit
# one-shot release step run by the deploy scripts (design §9.2), so ordinary
# startup never mutates schema.
#
#   <no args> | serve    -> start gunicorn, and nothing else
#   anything else        -> exec it verbatim
#
# The passthrough is what makes `docker compose run --rm api python manage.py
# migrate` actually run migrate. Without it the arguments were silently
# discarded and the server started instead — succeeding while doing something
# else entirely.
set -euo pipefail

if [ "$#" -gt 0 ] && [ "$1" != "serve" ]; then
    exec "$@"
fi

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
