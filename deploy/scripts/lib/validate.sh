#!/usr/bin/env bash
# Input validation and the deploy command grammar.
#
# Everything a client can influence arrives here as one string, and this file
# is the only place it is interpreted. Two structural rules do most of the
# security work, and neither is a matter of care:
#
#   * The ENVIRONMENT is a parameter of this library's caller, supplied by the
#     forced command in authorized_keys. It is never read from the client
#     string, so a staging key cannot address production however it asks.
#
#   * The REPOSITORY is a constant below. No function accepts a registry, a
#     repository or a path from a caller, so a deploy cannot be pointed at
#     another account's image — not because a prefix is validated, but because
#     none is ever accepted.
#
# There is no eval, no `bash -c`, and no unquoted expansion of client input.
# Client text reaches no filename, no docker argument other than a validated
# digest, and no shell.
set -euo pipefail

# --- Server-controlled constants ---------------------------------------------
# Literal. If these ever become variables, the guarantee above is gone.
readonly PI_API_REPOSITORY="ghcr.io/imiladco/product-intelligence/api"
readonly PI_WEB_REPOSITORY="ghcr.io/imiladco/product-intelligence/web"

# --- Field validators --------------------------------------------------------
# Each is anchored and total: no prefix, suffix, whitespace or case variation
# is accepted. Callers use them before a value reaches anything else.

pi_validate_sha() {
    local value="${1-}"
    [[ "$value" =~ ^[0-9a-f]{40}$ ]]
}

pi_validate_digest() {
    local value="${1-}"
    [[ "$value" =~ ^sha256:[0-9a-f]{64}$ ]]
}

pi_validate_environment() {
    local value="${1-}"
    [[ "$value" == "staging" || "$value" == "production" ]]
}

# --- Image references --------------------------------------------------------
# The digest is validated first, so a caller cannot append arbitrary text to a
# repository name and have it treated as an image reference.

pi_api_image_ref() {
    local digest="${1-}"
    pi_validate_digest "$digest" || return 1
    printf '%s@%s\n' "$PI_API_REPOSITORY" "$digest"
}

pi_web_image_ref() {
    local digest="${1-}"
    pi_validate_digest "$digest" || return 1
    printf '%s@%s\n' "$PI_WEB_REPOSITORY" "$digest"
}

# --- The grammar -------------------------------------------------------------
# pi_parse_command <environment> <client-string>
#
#   staging:     deploy <sha> <api-digest> <web-digest>   | status
#   production:  deploy <sha>                             | status
#
# Echoes the accepted command as space-separated words for the caller to read
# into an array. Exits 2 on anything else, printing a fixed string and never
# the offending input — which may carry control characters, and which nobody
# downstream should be tempted to log.
#
# Production takes no digests: it reads them from the staging ledger, and a
# grammar that accepted them from a client would make the staging proof
# decorative.
pi_parse_command() {
    local environment="${1-}"
    local client="${2-}"

    if ! pi_validate_environment "$environment"; then
        echo "invalid command" >&2
        return 2
    fi

    # Reject control characters outright, newlines included. A `read` here-string
    # only consumes the first line, so a multi-line input would otherwise be
    # half-parsed and half-discarded — accepted without having been understood.
    if [[ "$client" =~ [[:cntrl:]] ]]; then
        echo "invalid command" >&2
        return 2
    fi

    # An array, not word-splitting of an unquoted expansion: glob characters in
    # the input must never be matched against filenames on this host.
    local -a words=()
    read -r -a words <<< "$client"

    local verb="${words[0]-}"

    if [[ "$verb" == "status" ]]; then
        if (( ${#words[@]} != 1 )); then
            echo "invalid command" >&2
            return 2
        fi
        printf 'status\n'
        return 0
    fi

    if [[ "$verb" != "deploy" ]]; then
        echo "invalid command" >&2
        return 2
    fi

    local sha="${words[1]-}"
    if ! pi_validate_sha "$sha"; then
        echo "invalid command" >&2
        return 2
    fi

    if [[ "$environment" == "production" ]]; then
        if (( ${#words[@]} != 2 )); then
            echo "invalid command" >&2
            return 2
        fi
        printf 'deploy %s\n' "$sha"
        return 0
    fi

    # staging
    if (( ${#words[@]} != 4 )); then
        echo "invalid command" >&2
        return 2
    fi

    local api_digest="${words[2]-}"
    local web_digest="${words[3]-}"
    if ! pi_validate_digest "$api_digest" || ! pi_validate_digest "$web_digest"; then
        echo "invalid command" >&2
        return 2
    fi

    printf 'deploy %s %s %s\n' "$sha" "$api_digest" "$web_digest"
}
