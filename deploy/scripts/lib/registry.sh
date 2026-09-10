#!/usr/bin/env bash
# Authenticate to GHCR from the root-only credential file.
#
# Both application images are private, so every deploy authenticates before it
# pulls. The credential lives at /etc/product-intelligence/ghcr.env, 0600
# root:root, readable by the root-owned deploy scripts and by nothing the
# `deploy` user can reach.
#
# Schema — exactly these two keys, parsed as data:
#
#   GHCR_USERNAME=<github username or bot account>
#   GHCR_TOKEN=<read-only packages token>
#
# The file is PARSED, never sourced. It is root-owned, so sourcing would not be
# a privilege escalation as such — but a credential file is data, and running
# data as root buys nothing.
#
# Two properties this exists to hold:
#
#   * **No ambient state.** A `docker login` typed once by an admin persists in
#     /root/.docker/config.json, and pulls keep working from it until the day
#     that token expires — at which point deploys fail with "could not pull"
#     and nothing on the host explains why. Authentication happens on every
#     deploy, from the file, so the credential the documentation describes is
#     the credential actually in use.
#   * **The token never reaches argv.** Arguments are world-readable in `ps`
#     for the lifetime of the call. It goes in on stdin, and is never echoed,
#     logged, or written to the ledger.
#
# Fail-closed throughout: a missing file, wrong owner, loose mode, absent key
# or rejected credential all stop the deploy before any pull.

PI_GHCR_REGISTRY="${PI_GHCR_REGISTRY:-ghcr.io}"
PI_GHCR_CREDENTIAL_FILE="${PI_GHCR_CREDENTIAL_FILE:-/etc/product-intelligence/ghcr.env}"

_pi_registry_fail() {
    echo "registry: $1" >&2
    return 1
}

# Reads one key out of the credential file without executing it. Prints the
# value; prints nothing when the key is absent.
_pi_registry_value() {
    local key="$1" file="$2" line value
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ "$line" == "#"* ]] && continue
        [[ "$line" != "${key}="* ]] && continue
        value="${line#"${key}="}"
        # Tolerate a quoted value, which is common in env files.
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"
        printf '%s' "$value"
        return 0
    done < "$file"
    return 1
}

pi_registry_login() {
    # No xtrace while a secret is in scope, whatever the caller set.
    local _xtrace_was_on=0
    case "$-" in *x*) _xtrace_was_on=1; set +x ;; esac

    local file="$PI_GHCR_CREDENTIAL_FILE"
    local status=0

    if [[ ! -f "$file" ]]; then
        _pi_registry_fail "credential file not found: ${file}"
        status=1
    fi

    if (( status == 0 )); then
        local owner mode
        owner="$(stat -c '%u' "$file" 2>/dev/null)" || owner=""
        mode="$(stat -c '%a' "$file" 2>/dev/null)" || mode=""

        # Owned by whoever is running this script. On a deploy host that is
        # root, because sudo runs the deploy scripts as root -- so in
        # production this is exactly "must be owned by root". Stating it as
        # EUID rather than a literal 0 keeps the real invariant ("no account
        # but the one performing the deploy can read this") true wherever it
        # runs, with no environment override to weaken it.
        if [[ "$owner" != "$EUID" ]]; then
            _pi_registry_fail "credential file must be owned by the deploying user (uid ${EUID}): ${file} (uid ${owner:-unknown})"
            status=1
        elif [[ "$mode" != "600" ]]; then
            # Anything a second user can read is not a secret any more.
            _pi_registry_fail "credential file must be mode 0600: ${file} (is ${mode:-unknown})"
            status=1
        fi
    fi

    local username="" token=""
    if (( status == 0 )); then
        username="$(_pi_registry_value GHCR_USERNAME "$file")" || username=""
        token="$(_pi_registry_value GHCR_TOKEN "$file")" || token=""

        if [[ -z "$username" ]]; then
            _pi_registry_fail "GHCR_USERNAME is missing or empty in ${file}"
            status=1
        elif [[ -z "$token" ]]; then
            _pi_registry_fail "GHCR_TOKEN is missing or empty in ${file}"
            status=1
        fi
    fi

    if (( status == 0 )); then
        # printf is a shell builtin, so the token never becomes a process
        # argument. Only the username and the registry are visible in `ps`.
        if ! printf '%s' "$token" \
            | docker login "$PI_GHCR_REGISTRY" --username "$username" --password-stdin; then
            _pi_registry_fail "authentication to ${PI_GHCR_REGISTRY} failed"
            status=1
        fi
    fi

    token=""
    (( _xtrace_was_on )) && set -x
    return "$status"
}
