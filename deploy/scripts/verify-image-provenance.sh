#!/usr/bin/env bash
# Verify that an image carries the provenance it should.
#
#   verify-image-provenance.sh <image-ref> <expected-sha> [<expected-compat>]
#
# Exits 0 only when:
#   org.opencontainers.image.revision == <expected-sha>
#   and, when <expected-compat> is given,
#       org.arkav.pi.migrations-backward-compatible == <expected-compat>
#
# This closes the one link the rest of the repository cannot check. Every other
# provenance assertion reads a Dockerfile or stubs `docker image inspect`; none
# of them can show that a declared label is the label a *built* image carries.
# This is run in CI, on both images, against real builds — and on main against
# the pushed digest, which is the only place the digest half of the chain can
# be established.
#
# It reports drift; it does not decide policy. Whether a `false` compatibility
# label may be deployed is production's question, answered by the deploy
# script's fail-closed gate.
#
# It verifies and mutates nothing: the only docker subcommand it runs is
# `image inspect`. It is handed image references by CI, so anything else would
# be a way to turn a verification step into an action.
set -euo pipefail

REVISION_LABEL="org.opencontainers.image.revision"
COMPAT_LABEL="org.arkav.pi.migrations-backward-compatible"

image="${1-}"
expected_sha="${2-}"
expected_compat="${3-}"

if [[ -z "$image" || -z "$expected_sha" ]]; then
    echo "usage: verify-image-provenance.sh <image-ref> <expected-sha> [<expected-compat>]" >&2
    exit 2
fi

read_label() {
    local label="$1"
    docker image inspect --format "{{ index .Config.Labels \"${label}\" }}" "$image" 2>/dev/null
}

revision="$(read_label "$REVISION_LABEL")" || {
    echo "provenance: could not inspect ${image}" >&2
    exit 1
}

if [[ "$revision" != "$expected_sha" ]]; then
    echo "provenance: ${image}" >&2
    echo "  ${REVISION_LABEL} is '${revision}', expected '${expected_sha}'" >&2
    exit 1
fi

if [[ -n "$expected_compat" ]]; then
    compat="$(read_label "$COMPAT_LABEL")" || {
        echo "provenance: could not read ${COMPAT_LABEL} from ${image}" >&2
        exit 1
    }
    if [[ "$compat" != "$expected_compat" ]]; then
        echo "provenance: ${image}" >&2
        echo "  ${COMPAT_LABEL} is '${compat}', expected '${expected_compat}'" >&2
        exit 1
    fi
fi

echo "provenance: ${image} carries revision ${revision}"
