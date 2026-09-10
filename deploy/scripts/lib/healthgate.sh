#!/usr/bin/env bash
# The health gate: what "the release worked" is allowed to mean.
#
# Every check is bounded — a fixed attempt count and a fixed interval, never a
# loop that waits until something happens. A deploy that cannot conclude is a
# failed deploy, not a hung one.
#
# The public check requires the response to NAME the release just deployed. A
# hostname answering 200 with a different release is a crossed environment —
# production served by staging — and that fails the deploy rather than passing
# it (design §14 row 8).
#
# The cross-environment check treats the other environment's ledger as the only
# evidence that it exists. DNS, a listening port and a valid certificate can all
# be true for an environment that has never had a successful release; during
# production bootstrap they are. So a missing or empty sibling ledger records
# `not_applicable` and does not fail the deploy, and the sibling host is not
# even contacted.
set -euo pipefail

PI_HEALTH_INTERVAL="${PI_HEALTH_INTERVAL:-3}"

PI_HEALTH_ATTEMPTS_CONTAINERS="${PI_HEALTH_ATTEMPTS_CONTAINERS:-30}"
PI_HEALTH_ATTEMPTS_INTERNAL="${PI_HEALTH_ATTEMPTS_INTERNAL:-10}"
PI_HEALTH_ATTEMPTS_PUBLIC="${PI_HEALTH_ATTEMPTS_PUBLIC:-20}"
PI_HEALTH_ATTEMPTS_ROUTE="${PI_HEALTH_ATTEMPTS_ROUTE:-10}"

#: Distinct exit codes, so a caller can record which check failed.
PI_HEALTH_EXIT_CONTAINERS=10
PI_HEALTH_EXIT_INTERNAL=11
PI_HEALTH_EXIT_PUBLIC=12
PI_HEALTH_EXIT_LOGIN=13
PI_HEALTH_EXIT_STATIC=14
PI_HEALTH_EXIT_CROSS=15

_pi_health_sleep() {
    (( $(printf '%.0f' "$PI_HEALTH_INTERVAL") > 0 )) && sleep "$PI_HEALTH_INTERVAL"
    return 0
}

_pi_health_manifest() {
    local environment="$1"
    printf '/opt/product-intelligence/%s/compose.%s.yaml\n' "$environment" "$environment"
}

# --- Individual checks -------------------------------------------------------

pi_health_containers() {
    local environment="${1-}"
    local manifest="${PI_COMPOSE_MANIFEST:-$(_pi_health_manifest "$environment")}"
    local attempt

    for (( attempt = 1; attempt <= PI_HEALTH_ATTEMPTS_CONTAINERS; attempt++ )); do
        if env -u COMPOSE_PROJECT_NAME docker compose -f "$manifest" ps >/dev/null 2>&1; then
            return 0
        fi
        _pi_health_sleep
    done

    echo "health: containers for $environment did not become healthy" >&2
    return "$PI_HEALTH_EXIT_CONTAINERS"
}

pi_health_internal() {
    local environment="${1-}"
    local manifest="${PI_COMPOSE_MANIFEST:-$(_pi_health_manifest "$environment")}"
    local attempt

    for (( attempt = 1; attempt <= PI_HEALTH_ATTEMPTS_INTERNAL; attempt++ )); do
        if env -u COMPOSE_PROJECT_NAME docker compose -f "$manifest" \
                exec -T api curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
            return 0
        fi
        _pi_health_sleep
    done

    echo "health: in-container API health failed for $environment" >&2
    return "$PI_HEALTH_EXIT_INTERNAL"
}

# 200 AND the reported release equals the SHA just deployed.
pi_health_public() {
    local host="${1-}" expected_sha="${2-}"
    local attempt body reported

    for (( attempt = 1; attempt <= PI_HEALTH_ATTEMPTS_PUBLIC; attempt++ )); do
        if body="$(curl -fsS --max-time 10 "https://${host}/api/health" 2>/dev/null)"; then
            reported="$(
                printf '%s' "$body" |
                python3 -c 'import json,sys
try:
    print(json.load(sys.stdin).get("release",""))
except Exception:
    print("")' 2>/dev/null
            )"
            if [[ "$reported" == "$expected_sha" ]]; then
                return 0
            fi
        fi
        _pi_health_sleep
    done

    echo "health: ${host} did not report release ${expected_sha}" >&2
    return "$PI_HEALTH_EXIT_PUBLIC"
}

pi_health_login() {
    local host="${1-}"
    local attempt

    for (( attempt = 1; attempt <= PI_HEALTH_ATTEMPTS_ROUTE; attempt++ )); do
        if curl -fsS --max-time 10 -o /dev/null "https://${host}/login" 2>/dev/null; then
            return 0
        fi
        _pi_health_sleep
    done

    echo "health: ${host}/login did not respond" >&2
    return "$PI_HEALTH_EXIT_LOGIN"
}

# Proves the release step's collectstatic reached the volume Caddy serves, and
# that this host's static root is the right one.
pi_health_static() {
    local host="${1-}"
    local attempt

    for (( attempt = 1; attempt <= PI_HEALTH_ATTEMPTS_ROUTE; attempt++ )); do
        if curl -fsS --max-time 10 -o /dev/null \
                "https://${host}/static/admin/css/base.css" 2>/dev/null; then
            return 0
        fi
        _pi_health_sleep
    done

    echo "health: ${host} did not serve the admin stylesheet" >&2
    return "$PI_HEALTH_EXIT_STATIC"
}

# The other environment must still be standing — but only once it exists.
#
# Existence means: its ledger records a current release. Nothing else counts,
# and when it does not, the sibling host is not contacted at all.
pi_health_cross_environment() {
    local other_environment="${1-}" other_host="${2-}"
    local other_sha

    if ! other_sha="$(pi_ledger_read_current_sha "$other_environment" 2>/dev/null)" \
       || [[ -z "$other_sha" ]]; then
        echo "cross_environment: not_applicable (${other_environment} has no current release)"
        return 0
    fi

    if pi_health_public "$other_host" "$other_sha"; then
        echo "cross_environment: ok (${other_environment} still serving ${other_sha})"
        return 0
    fi

    echo "health: ${other_environment} on ${other_host} is not serving its current release" >&2
    return "$PI_HEALTH_EXIT_CROSS"
}

# --- The gate ----------------------------------------------------------------
# In order, stopping at the first failure: a broken container makes every HTTP
# check meaningless, so none of them runs.

pi_health_gate() {
    local environment="${1-}" host="${2-}" sha="${3-}"
    local other_environment other_host

    if [[ "$environment" == "staging" ]]; then
        other_environment="production"
        other_host="${PI_PRODUCTION_HOST:-app.arkav.lol}"
    else
        other_environment="staging"
        other_host="${PI_STAGING_HOST:-staging.arkav.lol}"
    fi

    pi_health_containers "$environment" || return $?
    pi_health_internal "$environment" || return $?
    pi_health_public "$host" "$sha" || return $?
    pi_health_login "$host" || return $?
    pi_health_static "$host" || return $?
    pi_health_cross_environment "$other_environment" "$other_host" || return $?
}
