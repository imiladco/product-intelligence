#!/usr/bin/env bash
# The release ledger: what this host knows about each environment.
#
# One JSON file per environment plus an append-only history, all root-owned,
# under /opt/product-intelligence/state (override with PI_STATE_DIR for tests).
# It is deliberately not a database and not a service: the questions it answers
# are "what is running", "what was running before", and "did this SHA pass
# staging", and files answer those without another moving part.
#
# Two properties the rest of the system leans on:
#
#   * Production's refusal to deploy an unproven SHA is exactly as trustworthy
#     as `pi_ledger_has_successful_release`, so `result: success` is written in
#     exactly one place — promotion — which runs only after a health gate
#     returns clean.
#
#   * Reads FAIL CLOSED. A missing, truncated or corrupt ledger yields no
#     output and a non-zero status, so a caller that forgets to check gets
#     nothing rather than an empty string it might mistake for an answer.
#
# Writes are atomic: temporary file in the same directory, then rename. A crash
# mid-write leaves the previous ledger intact, never a half-written one.
#
# JSON is read and written by python3. jq is not guaranteed on an Ubuntu host.
set -euo pipefail

PI_STATE_DIR="${PI_STATE_DIR:-/opt/product-intelligence/state}"

pi_ledger_path() {
    local environment="${1-}"
    case "$environment" in
        staging|production) ;;
        *) return 1 ;;
    esac
    printf '%s/%s.json\n' "$PI_STATE_DIR" "$environment"
}

pi_ledger_history_path() {
    local environment="${1-}"
    case "$environment" in
        staging|production) ;;
        *) return 1 ;;
    esac
    printf '%s/history/%s.jsonl\n' "$PI_STATE_DIR" "$environment"
}

# --- Reads -------------------------------------------------------------------
# Each shells out to python3 with the file path as an argument. Nothing from a
# client is interpolated into the program text.

_pi_ledger_query() {
    local path="$1" program="$2"
    shift 2
    [[ -f "$path" ]] || return 1
    python3 -c "$program" "$path" "$@"
}

pi_ledger_read_current_sha() {
    local path
    path="$(pi_ledger_path "${1-}")" || return 1
    _pi_ledger_query "$path" '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
current = data.get("current") or {}
sha = current.get("sha") or ""
if not sha:
    sys.exit(1)
print(sha)
'
}

pi_ledger_read_current_digests() {
    local path
    path="$(pi_ledger_path "${1-}")" || return 1
    _pi_ledger_query "$path" '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)
current = data.get("current") or {}
api, web = current.get("api_digest") or "", current.get("web_digest") or ""
if not api or not web:
    sys.exit(1)
print(api, web)
'
}

# Every release this environment has recorded as successful: current, previous,
# and any superseded entry in history. A release that passed staging and was
# then superseded must stay promotable, or only the newest SHA could ever reach
# production and a rollback promotion would be impossible.
_pi_ledger_successful_program='
import json, os, sys
path, wanted = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path))
except Exception:
    sys.exit(1)

records = []
for key in ("current", "previous"):
    entry = data.get(key)
    if entry:
        records.append(entry)

history = os.path.join(os.path.dirname(path), "history",
                       os.path.basename(path).replace(".json", ".jsonl"))
if os.path.exists(history):
    for line in open(history):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("result") == "success":
            records.append(entry)

for entry in records:
    if entry.get("sha") == wanted and entry.get("api_digest") and entry.get("web_digest"):
        print(entry["api_digest"], entry["web_digest"],
              entry.get("migrations_backward_compatible", ""))
        sys.exit(0)
sys.exit(1)
'

pi_ledger_has_successful_release() {
    local path
    path="$(pi_ledger_path "${1-}")" || return 1
    _pi_ledger_query "$path" "$_pi_ledger_successful_program" "${2-}" >/dev/null
}

pi_ledger_digests_for_sha() {
    local path output
    path="$(pi_ledger_path "${1-}")" || return 1
    output="$(_pi_ledger_query "$path" "$_pi_ledger_successful_program" "${2-}")" || return 1
    printf '%s %s\n' "$(awk '{print $1}' <<< "$output")" "$(awk '{print $2}' <<< "$output")"
}

pi_ledger_compatibility_for_sha() {
    local path output
    path="$(pi_ledger_path "${1-}")" || return 1
    output="$(_pi_ledger_query "$path" "$_pi_ledger_successful_program" "${2-}")" || return 1
    awk '{print $3}' <<< "$output"
}

# --- Writes ------------------------------------------------------------------

_pi_ledger_write() {
    local path="$1" program="$2"
    shift 2
    mkdir -p "$(dirname "$path")"
    local tmp="${path}.tmp.$$"
    if ! python3 -c "$program" "$path" "$tmp" "$@"; then
        rm -f "$tmp"
        return 1
    fi
    chmod 0644 "$tmp"
    mv -f "$tmp" "$path"
}

pi_ledger_set_candidate() {
    local environment="${1-}" sha="${2-}" api="${3-}" web="${4-}" compat="${5-}"
    local path
    path="$(pi_ledger_path "$environment")" || return 1
    _pi_ledger_write "$path" '
import json, os, sys
from datetime import datetime, timezone
path, tmp, environment, sha, api, web, compat = sys.argv[1:8]
data = {"environment": environment, "current": None, "previous": None, "candidate": None}
if os.path.exists(path):
    try:
        data = json.load(open(path))
    except Exception:
        pass
data["environment"] = environment
data["candidate"] = {
    "sha": sha,
    "api_digest": api,
    "web_digest": web,
    "migrations_backward_compatible": compat,
    "started_at": datetime.now(timezone.utc).isoformat(),
}
json.dump(data, open(tmp, "w"), indent=2, sort_keys=True)
' "$environment" "$sha" "$api" "$web" "$compat"
}

# The only writer of a successful release. Runs after the health gate returns
# clean, and never before it.
pi_ledger_promote_candidate() {
    local environment="${1-}"
    local path
    path="$(pi_ledger_path "$environment")" || return 1
    _pi_ledger_write "$path" '
import json, sys
from datetime import datetime, timezone
path, tmp = sys.argv[1:3]
data = json.load(open(path))
candidate = data.get("candidate")
if not candidate:
    sys.exit(1)
candidate["deployed_at"] = datetime.now(timezone.utc).isoformat()
candidate.pop("started_at", None)
data["previous"] = data.get("current")
data["current"] = candidate
data["candidate"] = None
json.dump(data, open(tmp, "w"), indent=2, sort_keys=True)
'
}

# A failed attempt. current and previous are left exactly as they were.
pi_ledger_clear_candidate() {
    local environment="${1-}"
    local path
    path="$(pi_ledger_path "$environment")" || return 1
    _pi_ledger_write "$path" '
import json, sys
path, tmp = sys.argv[1:3]
data = json.load(open(path))
data["candidate"] = None
json.dump(data, open(tmp, "w"), indent=2, sort_keys=True)
'
}

# Append-only, one JSON object per line, so a partial write can never corrupt an
# earlier record.
pi_ledger_append_history() {
    local environment="${1-}" sha="${2-}" result="${3-}" detail="${4-}"
    local path history
    path="$(pi_ledger_path "$environment")" || return 1
    history="$(pi_ledger_history_path "$environment")" || return 1
    mkdir -p "$(dirname "$history")"
    python3 -c '
import json, os, sys
from datetime import datetime, timezone
ledger_path, history_path, environment, sha, result, detail = sys.argv[1:7]
record = {
    "environment": environment,
    "sha": sha,
    "result": result,
    "detail": detail,
    "deployed_at": datetime.now(timezone.utc).isoformat(),
}
# Carry the artefact identity from the candidate, so a successful history row
# is self-sufficient evidence for a later promotion.
try:
    data = json.load(open(ledger_path))
    source = data.get("candidate") or data.get("current") or {}
    if source.get("sha") == sha:
        for key in ("api_digest", "web_digest", "migrations_backward_compatible"):
            if source.get(key):
                record[key] = source[key]
except Exception:
    pass
with open(history_path, "a") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\n")
' "$path" "$history" "$environment" "$sha" "$result" "$detail"
}
