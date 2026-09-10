#!/usr/bin/env bash
# Authenticate to GHCR from the root-only credential file, into a throwaway
# Docker config that is destroyed when the deploy ends.
#
# The credential lives at /etc/product-intelligence/ghcr.env, 0600 root:root,
# readable by the root-owned deploy scripts and by nothing the `deploy` user
# can reach. That file is the single source of truth for the token.
#
# Schema — exactly these two keys, each exactly once, and nothing else:
#
#   GHCR_USERNAME=<github username or bot account>
#   GHCR_TOKEN=<read-only packages token>
#
# Blank lines and whole-line `#` comments are allowed. Anything else — an
# unknown key, a duplicate, an indented assignment, a line without `=` — is a
# hard error. A parser that hunts for the keys it wants and ignores the rest
# accepts a file with a typo'd second token or a stray assignment, and then
# silently uses whichever line it reached first.
#
# The file is PARSED, never sourced. It is root-owned, so sourcing would not be
# a privilege escalation as such — but a credential file is data, and running
# data as root buys nothing.
#
# Three properties this exists to hold:
#
#   * **No reliance on ambient state.** A `docker login` typed once by an admin
#     persists in /root/.docker/config.json, and pulls keep working from it
#     until the day that token expires — at which point deploys fail with
#     "could not pull" and nothing on the host explains why. Authentication
#     happens on every deploy, from the file.
#   * **No persistence into ambient state either.** `docker login` writes the
#     credential into whatever Docker config it is given. Left at the default
#     that copies the token into /root/.docker/config.json, where it outlives
#     the deploy and duplicates the secret outside the one file meant to hold
#     it. Every authentication therefore gets a fresh DOCKER_CONFIG directory,
#     mode 0700, removed on every exit path — so the authenticated state lives
#     exactly as long as the deploy that needs it.
#   * **The token never reaches argv.** Arguments are world-readable in `ps`
#     for the lifetime of the call. It goes in on stdin, and is never echoed,
#     logged, or written to the ledger.
#
# Lifecycle, explicit:
#
#   pi_registry_login      # validate, create the session, authenticate
#   docker pull <api@digest>
#   docker pull <web@digest>
#   pi_registry_logout     # destroy the session
#
# with an EXIT trap as the backstop, so a deploy that aborts between the two
# never leaves an authenticated Docker config behind.

PI_GHCR_REGISTRY="${PI_GHCR_REGISTRY:-ghcr.io}"
PI_GHCR_CREDENTIAL_FILE="${PI_GHCR_CREDENTIAL_FILE:-/etc/product-intelligence/ghcr.env}"

# Set while a session is open; the EXIT trap reads it.
PI_REGISTRY_SESSION_DIR=""

_pi_registry_fail() {
    echo "registry: $1" >&2
    return 1
}

# --- Session lifecycle -------------------------------------------------------

_pi_registry_session_begin() {
    local dir
    dir="$(mktemp -d "${TMPDIR:-/tmp}/pi-registry-XXXXXXXX")" || {
        _pi_registry_fail "could not create a private Docker config directory"
        return 1
    }
    chmod 0700 "$dir" || {
        rm -rf "$dir"
        _pi_registry_fail "could not restrict the Docker config directory"
        return 1
    }

    PI_REGISTRY_SESSION_DIR="$dir"
    # Exported so the subsequent `docker pull` calls in this shell use the same
    # authenticated config, and nothing else does.
    export DOCKER_CONFIG="$dir"

    # Chain rather than replace: another EXIT handler may be installed later,
    # and clobbering it would silently drop that cleanup.
    local existing
    existing="$(trap -p EXIT)"
    if [[ -n "$existing" && "$existing" != *pi_registry_logout* ]]; then
        existing="${existing#trap -- \'}"
        existing="${existing%\' EXIT}"
        # shellcheck disable=SC2064
        trap "pi_registry_logout; ${existing}" EXIT
    else
        trap 'pi_registry_logout' EXIT
    fi
}

# Destroys the session. Idempotent, and safe to call when none was opened.
pi_registry_logout() {
    [[ -n "$PI_REGISTRY_SESSION_DIR" ]] || return 0
    rm -rf "$PI_REGISTRY_SESSION_DIR"
    PI_REGISTRY_SESSION_DIR=""
    unset DOCKER_CONFIG
    return 0
}

# --- Credential parsing ------------------------------------------------------

# Reads the credential file into PI_REGISTRY_USERNAME / PI_REGISTRY_TOKEN.
# Fails on anything the schema does not allow. Never executes the file, and
# never prints a value.
_pi_registry_parse() {
    local file="$1" line trimmed key value
    local seen_username=0 seen_token=0 lineno=0

    PI_REGISTRY_USERNAME=""
    PI_REGISTRY_TOKEN=""

    while IFS= read -r line || [[ -n "$line" ]]; do
        lineno=$(( lineno + 1 ))

        trimmed="${line//[$'\t\r ']/}"
        [[ -z "$trimmed" ]] && continue
        [[ "$line" == "#"* ]] && continue

        # KEY=value, anchored: no leading whitespace, no exported form, no
        # shell syntax. Anything else is a malformed line, not a value.
        if [[ ! "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            _pi_registry_fail "line ${lineno} of ${file} is not KEY=value"
            return 1
        fi

        key="${line%%=*}"
        value="${line#*=}"
        value="${value%\"}"; value="${value#\"}"
        value="${value%\'}"; value="${value#\'}"

        case "$key" in
            GHCR_USERNAME)
                seen_username=$(( seen_username + 1 ))
                PI_REGISTRY_USERNAME="$value"
                ;;
            GHCR_TOKEN)
                seen_token=$(( seen_token + 1 ))
                PI_REGISTRY_TOKEN="$value"
                ;;
            *)
                _pi_registry_fail "unknown key ${key} on line ${lineno} of ${file}"
                return 1
                ;;
        esac
    done < "$file"

    if (( seen_username != 1 )); then
        _pi_registry_fail "${file} must contain exactly one GHCR_USERNAME (found ${seen_username})"
        return 1
    fi
    if (( seen_token != 1 )); then
        _pi_registry_fail "${file} must contain exactly one GHCR_TOKEN (found ${seen_token})"
        return 1
    fi
    if [[ -z "$PI_REGISTRY_USERNAME" ]]; then
        _pi_registry_fail "GHCR_USERNAME is empty in ${file}"
        return 1
    fi
    if [[ -z "$PI_REGISTRY_TOKEN" ]]; then
        _pi_registry_fail "GHCR_TOKEN is empty in ${file}"
        return 1
    fi
    return 0
}

# --- Authentication ----------------------------------------------------------

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

    (( status == 0 )) && { _pi_registry_parse "$file" || status=1; }

    if (( status == 0 )); then
        _pi_registry_session_begin || status=1
    fi

    if (( status == 0 )); then
        # printf is a shell builtin, so the token never becomes a process
        # argument. Only the username and the registry are visible in `ps`.
        if ! printf '%s' "$PI_REGISTRY_TOKEN" \
            | docker login "$PI_GHCR_REGISTRY" --username "$PI_REGISTRY_USERNAME" --password-stdin; then
            _pi_registry_fail "authentication to ${PI_GHCR_REGISTRY} failed"
            status=1
        fi
    fi

    # A failed login leaves nothing authenticated behind.
    (( status == 0 )) || pi_registry_logout

    PI_REGISTRY_TOKEN=""
    (( _xtrace_was_on )) && set -x
    return "$status"
}
