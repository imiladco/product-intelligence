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
