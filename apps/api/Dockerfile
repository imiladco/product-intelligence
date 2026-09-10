# Django control plane.
#
# python:3.11-slim matches the interpreter the dependency locks were resolved
# against, so the pinned set installs exactly as tested.
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libpq for psycopg. Build tools are not needed: every wheel in the lock is
# published as a binary wheel for this platform.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first, so application edits do not invalidate this layer.
COPY apps/api/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.lock.txt

COPY apps/api/ /app/

# Non-root. /srv/static is shared with the reverse proxy, which serves
# /static/ itself — Django never serves static files in a container.
RUN useradd --system --uid 10001 --home /app appuser \
    && mkdir -p /srv/static \
    && chown -R appuser:appuser /app /srv/static
USER appuser

ENV DJANGO_SETTINGS_MODULE=config.settings \
    DJANGO_STATIC_ROOT=/srv/static

EXPOSE 8000

COPY --chown=appuser:appuser docker/api/entrypoint.sh /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
# Explicit, so the default is a word the entrypoint recognises rather than an
# empty argument list. `docker compose run api python manage.py migrate`
# replaces it and runs that instead (design §9.1).
CMD ["serve"]

# --- Release provenance ------------------------------------------------------
# Two facts the deploy path must be able to establish about an image without
# trusting whoever asked for the deploy: which commit built it, and whether its
# migrations are backward compatible. Both ride on the image, so they are fixed
# by the same digest the deploy pulls, and the server reads them back with
# `docker image inspect` rather than believing a request (design §11.2).
#
# Last in the file on purpose: these change on every commit, and above a COPY or
# RUN they would invalidate the dependency layers on every build.
#
# Both defaults fail closed. `unknown` can never equal a requested 40-hex SHA,
# so an unstamped image is refused by the revision check; `false` means a build
# that forgets the argument produces an artifact production will not deploy.
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA

ARG MIGRATIONS_BACKWARD_COMPATIBLE=false
LABEL org.arkav.pi.migrations-backward-compatible=$MIGRATIONS_BACKWARD_COMPATIBLE
