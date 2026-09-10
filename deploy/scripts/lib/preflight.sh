#!/usr/bin/env bash
# Preflight: prove a deploy can succeed before anything is mutated.
#
# Every function here is read-only. If one fails, the current release is still
# running and completely untouched — which is the point of running all of them
# before the pull, the migration and the recreate.
#
# Failures print one line beginning `preflight: ` and return non-zero. They
# report names, counts and booleans; never a value from a .env file, because a
# preflight that echoed one would put the database password in a CI log.
set -euo pipefail

#: Free space below which a pull is refused. A pull that fills the disk can
#: damage unrelated services on this host, so it is checked before fetching.
PI_MIN_FREE_GIB="${PI_MIN_FREE_GIB:-3}"

PI_REQUIRED_ENV_KEYS=(
    APP_DOMAIN
    APP_URL
    DJANGO_DEBUG
    DJANGO_SECRET_KEY
    DJANGO_ALLOWED_HOSTS
    CSRF_TRUSTED_ORIGINS
    POSTGRES_DB
    POSTGRES_USER
    POSTGRES_PASSWORD
    DATABASE_URL
    CREDENTIAL_ENCRYPTION_KEYS
    GOOGLE_CLIENT_ID
    GOOGLE_CLIENT_SECRET
    GOOGLE_OAUTH_REDIRECT_URI
    GUNICORN_WORKERS
    ACME_EMAIL
)

_pi_preflight_fail() {
    echo "preflight: $*" >&2
    return 1
}

# --- Environment file --------------------------------------------------------

pi_preflight_env_file() {
    local path="${1-}"

    [[ -f "$path" ]] || _pi_preflight_fail "missing env file: $path" || return 1

    local mode
    mode="$(stat -c '%a' "$path")"
    if [[ "$mode" != "600" ]]; then
        _pi_preflight_fail "env file mode is $mode, expected 0600: $path" || return 1
    fi

    local missing=()
    local key
    for key in "${PI_REQUIRED_ENV_KEYS[@]}"; do
        # Names only. The value is matched but never captured or printed.
        if ! grep -Eq "^${key}=.+$" "$path"; then
            missing+=("$key")
        fi
    done

    if (( ${#missing[@]} > 0 )); then
        _pi_preflight_fail "env file missing or empty: ${missing[*]}" || return 1
    fi
}

# --- Compose identity --------------------------------------------------------

# The §6.2 guard, and not a formality: COMPOSE_PROJECT_NAME overrides the
# manifest's `name:` key, and an inherited value would send every volume lookup
# to a project that does not exist. `env -u` strips it for this call rather
# than trusting the ambient environment, and -p is never passed.
pi_preflight_compose_project() {
    local manifest="${1-}" expected="${2-}"
    local resolved

    resolved="$(
        env -u COMPOSE_PROJECT_NAME docker compose -f "$manifest" config --format json 2>/dev/null |
            python3 -c 'import json,sys; print(json.load(sys.stdin).get("name",""))'
    )" || _pi_preflight_fail "could not render $manifest" || return 1

    if [[ "$resolved" != "$expected" ]]; then
        _pi_preflight_fail "compose project is '$resolved', expected '$expected'" || return 1
    fi
}

# --- Daemon, disk, external resources ----------------------------------------

pi_preflight_docker_available() {
    docker info >/dev/null 2>&1 || _pi_preflight_fail "docker daemon is not responding" || return 1
}

pi_preflight_disk_free() {
    local minimum="${1:-$PI_MIN_FREE_GIB}"
    local target="/var/lib/docker"
    [[ -d "$target" ]] || target="/"

    local available
    available="$(df -BG --output=avail "$target" 2>/dev/null | tail -1 | tr -dc '0-9')"
    [[ -n "$available" ]] || _pi_preflight_fail "could not read free space on $target" || return 1

    if (( available < minimum )); then
        _pi_preflight_fail "only ${available}GiB free on $target, need ${minimum}GiB" || return 1
    fi
}

pi_preflight_volume_exists() {
    local name="${1-}"
    docker volume inspect "$name" >/dev/null 2>&1 ||
        _pi_preflight_fail "external volume missing: $name" || return 1
}

pi_preflight_network_exists() {
    local name="${1-}"
    docker network inspect "$name" >/dev/null 2>&1 ||
        _pi_preflight_fail "external network missing: $name" || return 1
}

# --- Image labels ------------------------------------------------------------
# Read back from the image that was actually pulled. Nothing here trusts a
# caller's claim about what an image contains.

_pi_image_label() {
    local image="${1-}" label="${2-}"
    docker image inspect --format "{{ index .Config.Labels \"${label}\" }}" "$image" 2>/dev/null
}

# The requested SHA must be the commit that built this image. This is what stops
# a digest from another commit being deployed under a SHA it does not belong to
# — and it applies to the web image exactly as much as to the API image.
pi_preflight_image_revision() {
    local image="${1-}" expected_sha="${2-}"
    local revision

    revision="$(_pi_image_label "$image" "org.opencontainers.image.revision")" ||
        _pi_preflight_fail "could not inspect $image" || return 1

    if [[ "$revision" != "$expected_sha" ]]; then
        _pi_preflight_fail "image revision '$revision' does not match requested $expected_sha" || return 1
    fi
}

# Fail closed. Exactly the string `true` may pass.
#
# `false` is an honest no; the other near-misses — True, TRUE, 1, yes, an empty
# string from an absent label, `<no value>` from docker's formatter, an inspect
# that could not run — are ways of nearly saying yes, and treating any of them
# as consent would deploy a release whose migrations may break the previous
# application while it is still serving traffic (design §11.2).
pi_preflight_migration_compatibility() {
    local image="${1-}"
    local value

    value="$(_pi_image_label "$image" "org.arkav.pi.migrations-backward-compatible")" ||
        _pi_preflight_fail "could not read compatibility label from $image" || return 1

    if [[ "$value" != "true" ]]; then
        _pi_preflight_fail "image does not declare backward-compatible migrations (label: '${value}')" || return 1
    fi
}
