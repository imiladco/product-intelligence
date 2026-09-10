#!/usr/bin/env bash
# Rollback: images only.
#
# Image rollback and database rollback are different things, and this library
# does exactly one of them. There is no migrate here, no --fake, no dump and no
# restore — not because they were forgotten, but because reversing a migration
# automatically is how data is lost. When the schema cannot be walked back, the
# honest answer is to stop and say so.
#
# Rollback is permitted only when the candidate declared backward-compatible
# migrations. Otherwise the previous images would be started against a schema
# they cannot read, and "rolled back" would be a lie told to a monitoring
# dashboard.
#
# If the rollback's own health gate fails, everything stops: no retry, no
# second rollback, no loop. Diagnostics are written and a human decides.
set -euo pipefail

_pi_release_dir() {
    local environment="$1"
    printf '%s\n' "${PI_RELEASE_DIR:-/opt/product-intelligence/${environment}/.release}"
}

_pi_manifest() {
    local environment="$1"
    printf '%s\n' "${PI_COMPOSE_MANIFEST:-/opt/product-intelligence/${environment}/compose.${environment}.yaml}"
}

# Exactly `true`, and a previous release with both digests. Anything else — an
# honest `false`, an unreadable label, a first release with no predecessor —
# means there is nothing safe to fall back to.
pi_rollback_is_permitted() {
    local environment="${1-}" compat="${2-}"

    if [[ "$compat" != "true" ]]; then
        echo "rollback: refused, candidate did not declare backward-compatible migrations" >&2
        return 1
    fi

    local previous
    previous="$(
        python3 -c '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
previous = data.get("previous") or {}
if not previous.get("api_digest") or not previous.get("web_digest"):
    sys.exit(1)
print(previous["sha"], previous["api_digest"], previous["web_digest"])
' "${PI_STATE_DIR:-/opt/product-intelligence/state}/${environment}.json" 2>/dev/null
    )" || {
        echo "rollback: refused, no previous release recorded for ${environment}" >&2
        return 1
    }

    [[ -n "$previous" ]] || return 1
}

# Restore the previous images and re-run the gate. One attempt.
pi_rollback_execute() {
    local environment="${1-}" host="${2-}"
    local release_dir manifest previous sha api web

    release_dir="$(_pi_release_dir "$environment")"
    manifest="$(_pi_manifest "$environment")"

    previous="$(
        python3 -c '
import json, sys
data = json.load(open(sys.argv[1]))
p = data["previous"]
print(p["sha"], p["api_digest"], p["web_digest"])
' "${PI_STATE_DIR:-/opt/product-intelligence/state}/${environment}.json"
    )" || {
        echo "CRITICAL: rollback could not read the previous release for ${environment}" >&2
        return 1
    }

    read -r sha api web <<< "$previous"

    # Rebuild the references from the recorded digests. If either cannot be
    # built the recorded digest is malformed, and writing an empty API_IMAGE
    # would leave compose pulling nothing at all — a silent no-op dressed as a
    # rollback.
    local api_ref web_ref
    if ! api_ref="$(pi_api_image_ref "$api")" || [[ -z "$api_ref" ]]; then
        echo "CRITICAL: previous API digest is unusable for ${environment}" >&2
        return 1
    fi
    if ! web_ref="$(pi_web_image_ref "$web")" || [[ -z "$web_ref" ]]; then
        echo "CRITICAL: previous web digest is unusable for ${environment}" >&2
        return 1
    fi

    mkdir -p "$release_dir"
    local tmp="${release_dir}/images.env.tmp.$$"
    {
        printf 'API_IMAGE=%s\n' "$api_ref"
        printf 'WEB_IMAGE=%s\n' "$web_ref"
        printf 'RELEASE_SHA=%s\n' "$sha"
    } > "$tmp"
    chmod 0644 "$tmp"
    mv -f "$tmp" "${release_dir}/images.env"

    echo "rollback: restoring ${environment} to ${sha}"
    if ! env -u COMPOSE_PROJECT_NAME docker compose -f "$manifest" up -d; then
        echo "CRITICAL: rollback could not recreate ${environment}" >&2
        return 1
    fi

    # One health gate. If the previous release will not come back healthy, a
    # second attempt at the same thing changes nothing and delays the human.
    if ! pi_health_gate "$environment" "$host" "$sha"; then
        echo "CRITICAL: rollback of ${environment} to ${sha} failed its health gate" >&2
        return 1
    fi

    echo "rollback: ${environment} is serving ${sha}"
}

# What a human needs to see afterwards: container state and recent logs, scoped
# to this project. Deliberately not configuration — no environment dump, no
# .env, no image inspection — because a diagnostics directory outlives the
# deploy and would otherwise carry every secret with it.
pi_collect_diagnostics() {
    local environment="${1-}" outdir="${2-}"
    local manifest
    manifest="$(_pi_manifest "$environment")"

    mkdir -p "$outdir"

    env -u COMPOSE_PROJECT_NAME docker compose -f "$manifest" ps \
        > "${outdir}/ps.txt" 2>&1 || true
    env -u COMPOSE_PROJECT_NAME docker compose -f "$manifest" logs --tail 200 --no-color \
        > "${outdir}/logs.txt" 2>&1 || true

    echo "diagnostics: ${outdir}"
}
