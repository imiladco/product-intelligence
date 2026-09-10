# Milestone 7 Production Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish V1 with immutable CI-built artifacts, automatic staging
deployment, manual promotion of the exact staging-tested artifacts to an
isolated production environment, and a safe one-time staging/Caddy migration.

**Architecture:** Follow the approved M7 design exactly. Application releases
contain release data only; privileged deployment infrastructure is an
admin-installed control plane. Both environments deploy immutable GHCR images
by digest behind one shared Caddy, with separate Docker networks, databases,
volumes and secrets.

**Tech Stack:** GitHub Actions, Docker Buildx, GHCR, Docker Compose, Bash,
flock, Caddy, Django/DRF, Next.js, PostgreSQL, Ubuntu VPS.

**Spec:**
docs/superpowers/specs/2026-09-06-m7-production-deployment-design.md

---

## 0. Ground rules for the whole milestone

1. **The spec is the authority.** Where this plan and the spec disagree, stop
   and report; do not choose. Section references like §12.6 are to the spec.
2. **Part 1 is repository work only.** No task in Part 1 touches the VPS, DNS,
   GitHub secrets, or any deployed service. Part 2 does not begin until Part 1
   is reviewed and merged (Task 19 is the gate).
3. **No task in this plan may modify `n8n`, `cloudflared`, Portainer,
   `x-ui`/Xray, or any unrelated database or container.** Every Docker command
   in Part 2 and Part 3 names a Compose project or explicit service list.
   `docker system prune`, bare `docker stop`, `docker volume prune`,
   `docker network prune` and `docker compose down` outside the two application
   projects are **forbidden everywhere in this plan**.
4. **Never print a secret.** Not a token, not a key, not a password, not a
   `.env` value. Verification prints names, booleans, counts, and lengths only.
5. **Expected failure first.** Where a task lists an expected failure, run it
   and confirm the failure *before* writing the implementation. An unexpected
   failure mode (an import error where an assertion was expected) means the test
   is wrong — fix the test first.
6. **Commit boundaries are exact.** One commit per task unless the task says
   otherwise. Do not batch.
7. **Full local gates before every Part 1 commit:**
   ```bash
   cd apps/api && ../../.venv/bin/python -m pytest -q
   cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run
   cd apps/web && npm run test && npx tsc --noEmit && npm run lint
   ```
   Tasks that touch `apps/web` also run
   `cd apps/web && NODE_OPTIONS=--max-old-space-size=640 npm run build`.
8. **Every STOP condition in Part 2 and Part 3 is a hard stop.** Report and
   wait for the user. Do not "work around" it.

### Constants used throughout (never re-derive these)

| Name | Value |
|---|---|
| GHCR API repository | `ghcr.io/imiladco/product-intelligence/api` |
| GHCR web repository | `ghcr.io/imiladco/product-intelligence/web` |
| Staging Compose project | `product-intelligence-staging` |
| Production Compose project | `product-intelligence-production` |
| Shared Caddy Compose project | `product-intelligence-shared` |
| Staging DB volume (legacy name, kept) | `product-intelligence-staging_pgdata_staging` |
| Staging static volume | `product-intelligence-staging_static` |
| Caddy data volume (legacy name, kept) | `product-intelligence-staging_caddy_data` |
| Caddy config volume (legacy name, kept) | `product-intelligence-staging_caddy_config` |
| Production DB volume | `product-intelligence-production_pgdata` |
| Production static volume | `product-intelligence-production_static` |
| Networks | `product-intelligence-{staging,production}-{edge,internal}` |
| Staging host | `staging.arkav.lol` |
| Production host | `app.arkav.lol` |
| Compatibility label | `org.arkav.pi.migrations-backward-compatible` |
| Revision label | `org.opencontainers.image.revision` |

---

## 1. Task inventory

**Part 1 — repository implementation (20 tasks, merged before any server work)**

| # | Task | Commit subject |
|---|---|---|
| 01 | Health payload release field | `feat(api): report the deployed release from the health endpoint` |
| 02 | Entrypoint argument passthrough; migrations leave startup | `feat(api): run migrations as an explicit release step, not on startup` |
| 03 | Release metadata and image labels | `feat(deploy): declare migration compatibility on the API image` |
| 04 | Staging deployment manifest | `feat(deploy): staging manifest on external volumes and networks` |
| 05 | Production deployment manifest | `feat(deploy): production manifest` |
| 06 | Shared Caddy manifest and Caddyfile | `feat(deploy): shared Caddy serving both environments` |
| 07 | Deploy library — input validation and parser | `feat(deploy): strict deploy command grammar` |
| 08 | Deploy library — release ledger | `feat(deploy): root-owned release ledger` |
| 09 | Deploy library — preflight | `feat(deploy): preflight that aborts before mutating anything` |
| 10 | Deploy library — health gate | `feat(deploy): bounded health gate with cross-environment check` |
| 11 | Deploy library — rollback | `feat(deploy): image-only rollback` |
| 12 | Staging deploy entry point | `feat(deploy): staging deploy by digest` |
| 13 | Production deploy entry point | `feat(deploy): production promotion of staging-proven digests` |
| 14 | SSH wrapper and control-plane installer | `feat(deploy): environment-bound wrapper and control-plane installer` |
| 15 | CI — quality gates and image builds | `ci: quality gates and image builds` |
| 16 | CI — publish and auto-deploy staging | `ci: publish images and deploy staging by digest` |
| 16B | **Artifact provenance chain (SHA → label → digest)** | `ci: verify artifact provenance for both images` |
| 17 | CI — production promotion workflow | `ci: manual production promotion` |
| 18 | Examples and documentation | `docs: deployment runbooks and production env example` |
| 19 | Merged-tree verification and PR | `chore: M7 repository verification` |

**Part 2 — live infrastructure (approval-gated, 11 tasks)**

| # | Task | Type |
|---|---|---|
| L01 | VPS inventory and unrelated-service baseline | inspect |
| L02 | Staging project, volume, data and credential baseline | inspect |
| L03 | Host resource baseline (pre-relocation) | inspect |
| L04 | Install `deploy` user, forced keys, sudoers, control plane | mutate (additive) |
| L05 | Negative proofs: key isolation and no Docker access | inspect |
| L06 | GHCR read-only credential and pull demonstration | mutate (additive) |
| L07 | Networks and storage skeleton | mutate (additive) |
| L08 | Pre-pull candidate image digests | mutate (additive) |
| L09 | **Relocation and Caddy cutover (outage)** | **STOP/GO** |
| L10 | Post-relocation verification | inspect |
| L11 | Staging auto-deploy proof | mutate (staging only) |

**Part 3 — capacity, production, acceptance (4 tasks)**

| # | Task | Type |
|---|---|---|
| L12 | **Capacity acceptance gate** | **GO/STOP** |
| L13 | Production bootstrap | **STOP/GO**, mutate |
| L14 | First production promotion | mutate |
| L15 | Final V1 acceptance checklist | inspect |

Total: **34 tasks.**

---

# PART 1 — REPOSITORY IMPLEMENTATION

No task in this part touches a server.

---

## Task 01 — Health payload release field

Implements spec §10.4. Produces the interface every health gate depends on.

**Files**
- Modified: `apps/api/common/views.py`
- Modified: `apps/api/tests/test_settings_security.py` — no; **created**:
  `apps/api/tests/test_health.py`

**Interfaces produced**
```
GET /api/health → 200 {"status": "ok", "database": "ok", "release": "<sha>|unknown"}
                → 503 {"status": "error", "database": "unavailable", "release": "<sha>|unknown"}
```
`release` is read from the `RELEASE_SHA` environment variable at request time.

**Behavioural requirements**
1. `release` is present on **both** the 200 and the 503 response.
2. Value is `os.environ.get("RELEASE_SHA", "")` or, when empty/unset, the exact
   string `"unknown"`.
3. Read per request (not at import), so a container restart with a new value is
   reflected without a code change.
4. No other key is added; `status` and `database` keep their current values and
   status codes.
5. The value is a commit SHA and nothing else — no settings, no environment
   dump, no version of any dependency.

**Failing tests first** — create `apps/api/tests/test_health.py`:
- `test_health_reports_the_release_from_the_environment` — `monkeypatch.setenv("RELEASE_SHA", "a"*40)`, GET `/api/health`, assert `response.data["release"] == "a"*40`.
- `test_health_reports_unknown_when_unset` — `monkeypatch.delenv("RELEASE_SHA", raising=False)`, assert `response.data["release"] == "unknown"`.
- `test_health_reports_unknown_when_empty` — set `RELEASE_SHA=""`, assert `"unknown"`.
- `test_release_is_read_per_request` — set `"a"*40`, GET, then set `"b"*40`, GET again, assert the second response reports `"b"*40`.
- `test_health_carries_no_other_configuration` — assert `set(response.data) == {"status", "database", "release"}`.
- `test_health_needs_no_authentication` — unauthenticated `APIClient` gets 200 (pins the existing contract the container healthcheck relies on).

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_health.py -q
```
**Expected failure:** `KeyError: 'release'` / `assert set(...) == {...}` mismatch on the first four tests; the last two pass already.

**Then implement** in `common/views.py`, and re-run: 6 passed.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run
```
Expected: all tests pass (existing count + 6), `No changes detected`.

**Commit:** `feat(api): report the deployed release from the health endpoint`

---

## Task 02 — Entrypoint argument passthrough; migrations leave startup

Implements spec §1.4, §9.1, §9.3, §9.4. **This is the task the whole release
model depends on** — without it, `docker compose run api python manage.py
migrate` silently runs gunicorn instead.

**Files**
- Modified: `docker/api/entrypoint.sh`
- Modified: `apps/api/Dockerfile`
- Modified: `compose.dev.yaml` — **no**. Not touched; it runs no API container.
- Created: `apps/api/tests/test_entrypoint_contract.py`

**Hard constraint discovered by inspection — do not break it.**
`apps/api/tests/test_access_log.py` parses the entrypoint with the anchored
regex `^ACCESS_LOG_FORMAT='(.*)'$` and asserts the literal substring
`--access-logformat "$ACCESS_LOG_FORMAT"` is present. Both **must survive**
verbatim. Task 02 is not permitted to reformat, rename or re-indent the
gunicorn invocation or that assignment.

**Behavioural requirements**
1. With **no arguments**, or with the single argument `serve`, the entrypoint
   starts gunicorn with the existing flags and access-log format, unchanged.
2. With any other arguments, it `exec "$@"` and **never** starts gunicorn.
3. `collectstatic` and `migrate` are **removed** from the startup path entirely.
   They are not run for `serve`, and not run for any other command.
4. `apps/api/Dockerfile` gains `CMD ["serve"]` so the default container command
   is explicit rather than implied by an empty argument list.
5. `set -euo pipefail` is retained.

**Failing tests first** — create `apps/api/tests/test_entrypoint_contract.py`,
following the source-reading + subprocess pattern already used by
`test_access_log.py` (`REPO_ROOT = Path(__file__).resolve().parents[3]`):

Static assertions:
- `test_startup_path_does_not_migrate` — the entrypoint source contains no
  `manage.py migrate`.
- `test_startup_path_does_not_collectstatic` — no `manage.py collectstatic`.
- `test_entrypoint_passes_through_arguments` — source contains `exec "$@"`.
- `test_dockerfile_declares_the_serve_command` — `apps/api/Dockerfile` contains
  `CMD ["serve"]`.
- `test_access_log_contract_is_preserved` — the anchored
  `^ACCESS_LOG_FORMAT='(.*)'$` regex still matches and
  `--access-logformat "$ACCESS_LOG_FORMAT"` is still present. (Belt and braces
  next to `test_access_log.py`, so a future edit fails *here* with an
  explanatory name.)

Behavioural assertions, executing the real script with `bash`:
- `test_an_explicit_command_runs_instead_of_the_server` —
  `subprocess.run(["bash", str(ENTRYPOINT), "python", "-c", "print('RAN_COMMAND')"], cwd=API_DIR, capture_output=True, text=True, timeout=60)`;
  assert `"RAN_COMMAND" in result.stdout` and `"gunicorn" not in result.stdout.lower()`
  and `result.returncode == 0`.
- `test_a_management_command_runs_instead_of_the_server` — same shape with
  `["python", "manage.py", "check", "--deploy"]` under the production-like env
  used by `tests/test_settings_security.py::PROD_ENV` plus
  `DATABASE_URL` pointing at the test database; assert the process exits without
  starting gunicorn (`"Starting gunicorn" not in output`). Use
  `env={**os.environ, **PROD_ENV_COPY}`; do not mutate the ambient environment.
- `test_serve_would_start_gunicorn` — run with `serve` under
  `GUNICORN_CMD_PREFIX`-free conditions but with a **deliberately invalid**
  bind (`GUNICORN_WORKERS=0`) so gunicorn exits immediately, and assert the
  output mentions gunicorn rather than a management command. (Rationale: proves
  the `serve` branch reaches gunicorn without leaving a server running in the
  test suite.)

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_entrypoint_contract.py -q
```
**Expected failure:** every static assertion fails (`manage.py migrate` is
present, `exec "$@"` is absent, `CMD ["serve"]` is absent), and
`test_an_explicit_command_runs_instead_of_the_server` fails because the script
ignores its arguments and tries to start gunicorn.

**Then implement.** Target shape of `docker/api/entrypoint.sh` — the comment
block above the gunicorn invocation and the `ACCESS_LOG_FORMAT` line are kept
byte-for-byte from the current file:

```bash
#!/usr/bin/env bash
# Serve the application, or run exactly the command given.
#
# Migrations and collectstatic are deliberately NOT here. They are an explicit
# one-shot release step (design §9.2): a container restarted by the Docker
# daemon must never change database schema.
set -euo pipefail

if [ "$#" -gt 0 ] && [ "$1" != "serve" ]; then
    exec "$@"
fi

# … existing comment block, verbatim …
ACCESS_LOG_FORMAT='%(h)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s "%(f)s" "%(a)s"'

exec gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --timeout "${GUNICORN_TIMEOUT:-60}" \
    --access-logfile - \
    --access-logformat "$ACCESS_LOG_FORMAT" \
    --error-logfile - \
    --capture-output
```

In `apps/api/Dockerfile`, add `CMD ["serve"]` immediately after the
`ENTRYPOINT` line. Reduce the API healthcheck `start_period` in Task 04, not
here.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
```
Expected: all pass, **including the pre-existing `tests/test_access_log.py`**
unchanged. If `test_access_log.py` fails, the gunicorn block was edited — revert
that part.

**Commit:** `feat(api): run migrations as an explicit release step, not on startup`

---

## Task 03 — Release metadata and image labels

Implements spec §11.2 and the digest-provenance requirement.

**Files**
- Created: `deploy/release-metadata.json`
- Modified: `apps/api/Dockerfile`, `apps/web/Dockerfile`
- Created: `deploy/tests/test_release_metadata.py`
- Created: `deploy/tests/conftest.py` (empty file; makes the directory a clean
  pytest root that does not require Django)

**Interfaces produced**

`deploy/release-metadata.json`, exactly:
```json
{
  "migrations_backward_compatible": true
}
```

Both Dockerfiles accept two build arguments and stamp labels:

| Image | Label | Source |
|---|---|---|
| api, web | `org.opencontainers.image.revision` | `--build-arg GIT_SHA` |
| api | `org.arkav.pi.migrations-backward-compatible` | `--build-arg MIGRATIONS_BACKWARD_COMPATIBLE` |

**Provenance requirement (added at review).** The labels are one link in a
three-link chain that must hold end to end: **Git SHA → OCI label → image
digest**. This task creates the labels on **both** images; Task 09 verifies them
on a pulled image; Tasks 12 and 13 verify them for the API **and** the web image
before any release step; Task 16B proves the whole chain against really-built,
really-pushed images. No link may be assumed.

**Behavioural requirements**
1. `ARG GIT_SHA=unknown` and `LABEL org.opencontainers.image.revision=$GIT_SHA`
   in both Dockerfiles, in the final stage.
2. `ARG MIGRATIONS_BACKWARD_COMPATIBLE=false` in the API Dockerfile, with
   `LABEL org.arkav.pi.migrations-backward-compatible=$MIGRATIONS_BACKWARD_COMPATIBLE`.
   **The default is `false`** — a build that forgets to pass the argument
   produces an image production refuses, which is the fail-closed direction.
3. The label declares the value; nothing at runtime reads it. It is read by the
   deploy scripts via `docker image inspect` (Task 09).
4. Adding the `ARG`/`LABEL` lines must not invalidate the dependency layers:
   place them **after** the last `COPY`/`RUN` in the final stage.

**Failing tests first** — `deploy/tests/test_release_metadata.py`:
- `test_metadata_file_exists_and_is_valid_json`
- `test_metadata_declares_only_the_compatibility_field` — the parsed object's
  keys are exactly `{"migrations_backward_compatible"}`.
- `test_compatibility_value_is_a_json_boolean` — `isinstance(value, bool)`; a
  string `"true"` fails. (Rationale: a string would be truthy in Python and
  ambiguous in shell.)
- `test_api_dockerfile_declares_both_labels` — reads `apps/api/Dockerfile`,
  asserts both `LABEL` lines and both `ARG` lines are present.
- `test_api_compatibility_arg_defaults_to_false` — asserts
  `ARG MIGRATIONS_BACKWARD_COMPATIBLE=false` appears verbatim.
- `test_web_dockerfile_declares_the_revision_label` — reads
  `apps/web/Dockerfile`, asserts `ARG GIT_SHA` and the revision `LABEL`.

```bash
cd /home/user/product-intelligence && .venv/bin/python -m pytest deploy/tests -q -p no:cacheprovider
```
**Expected failure:** `FileNotFoundError` for `deploy/release-metadata.json`
and assertion failures for the missing Dockerfile lines.

**Then implement**, and re-run: 6 passed.

**Verification**
```bash
cd /home/user/product-intelligence && .venv/bin/python -m pytest deploy/tests -q -p no:cacheprovider
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `feat(deploy): declare migration compatibility on the API image`

---

## Task 04 — Staging deployment manifest

Implements spec §5, §6.2, §6.3, §16, D11.

**Files**
- Modified: `compose.staging.yaml`
- Created: `deploy/tests/test_compose_manifests.py`

**Behavioural requirements**
1. `name: product-intelligence-staging` retained **verbatim**.
2. No `build:` section anywhere. `api` and `web` use
   `image: ${API_IMAGE}` / `image: ${WEB_IMAGE}`, supplied by the generated
   `.release/images.env` (Task 12).
3. No `ports:` on any service. The loopback publishes are removed.
4. The `caddy` service is **removed** from this file entirely (it moves to
   Task 06).
5. Volumes declared external by exact name:
   ```yaml
   volumes:
     pgdata_staging:
       external: true
       name: product-intelligence-staging_pgdata_staging   # legacy name, kept on purpose (§6.3)
     static:
       external: true
       name: product-intelligence-staging_static
   ```
6. Networks declared external:
   ```yaml
   networks:
     internal:
       external: true
       name: product-intelligence-staging-internal
     edge:
       external: true
       name: product-intelligence-staging-edge
   ```
7. Service network membership and aliases:
   - `postgres`: `[internal]` only — **never** on edge.
   - `api`: `internal`, plus `edge` with `aliases: [staging-api]`.
   - `web`: `internal`, plus `edge` with `aliases: [staging-web]`.
8. `web` keeps `INTERNAL_API_BASE_URL: http://api:8000` — the internal service
   name, **not** the edge alias (§5.3). Do not "simplify" this.
9. `api` gains `environment: RELEASE_SHA: ${RELEASE_SHA}` alongside the existing
   `DJANGO_STATIC_ROOT`.
10. `api` healthcheck `start_period` reduced from `120s` to `45s` (§9.4); every
    other healthcheck field unchanged.
11. `GUNICORN_WORKERS` continues to come from `.env` (§16).
12. `mem_limit` is **not** set in this task — it is set in L12 from measured
    values, and guessing here would be inventing headroom.

**Failing tests first** — `deploy/tests/test_compose_manifests.py`. Parse with
`yaml.safe_load` (PyYAML is already a transitive dependency of the dev lock;
if the import fails, add `pyyaml` to `apps/api/requirements-dev.txt`, run
`./scripts/lock-python-deps.sh`, and commit the regenerated locks with this
task). Tests:
- `test_staging_project_name_is_pinned`
- `test_staging_has_no_build_sections`
- `test_staging_publishes_no_ports`
- `test_staging_has_no_caddy_service`
- `test_staging_volumes_are_external_by_exact_name` — both names asserted as
  string literals.
- `test_staging_networks_are_external_by_exact_name`
- `test_staging_postgres_is_not_on_the_edge_network`
- `test_staging_api_and_web_expose_disambiguated_edge_aliases`
- `test_staging_ssr_targets_the_internal_service_name` — asserts
  `http://api:8000`, and asserts the string `staging-api:8000` does **not**
  appear in the web service environment.
- `test_staging_api_receives_the_release_sha`
- `test_staging_api_start_period_is_reduced`

```bash
cd /home/user/product-intelligence && .venv/bin/python -m pytest deploy/tests -q -p no:cacheprovider
```
**Expected failure:** all eleven fail against the current manifest (it has
`build:`, ports, a caddy service, and non-external volumes).

**Then implement.**

**Verification** — the tests, plus a real Compose render:
```bash
cd /home/user/product-intelligence
API_IMAGE=x WEB_IMAGE=y RELEASE_SHA=z POSTGRES_DB=d POSTGRES_USER=u POSTGRES_PASSWORD=p \
  docker compose -f compose.staging.yaml config >/dev/null && echo "RENDER OK"
API_IMAGE=x WEB_IMAGE=y RELEASE_SHA=z POSTGRES_DB=d POSTGRES_USER=u POSTGRES_PASSWORD=p \
  docker compose -f compose.staging.yaml config --format json \
  | python3 -c "import json,sys; print('project:', json.load(sys.stdin)['name'])"
```
Expected: `RENDER OK` and `project: product-intelligence-staging`.
*(`config` renders locally and needs no Docker daemon; external volumes are not
checked until `up`.)*

**Commit:** `feat(deploy): staging manifest on external volumes and networks`

---

## Task 05 — Production deployment manifest

Implements spec §5, §15, §16.

**Files**
- Created: `compose.production.yaml`
- Modified: `deploy/tests/test_compose_manifests.py`

**Behavioural requirements** — identical in shape to Task 04 with these values:
1. `name: product-intelligence-production`.
2. Volumes external: `pgdata` → `product-intelligence-production_pgdata`,
   `static` → `product-intelligence-production_static`.
3. Networks external: `product-intelligence-production-{internal,edge}`.
4. Edge aliases `production-api`, `production-web`.
5. `postgres` on `internal` only.
6. `INTERNAL_API_BASE_URL: http://api:8000` (internal name, same reasoning).
7. `RELEASE_SHA: ${RELEASE_SHA}` on `api`.
8. No `build:`, no `ports:`, no `caddy` service.
9. Same healthcheck definitions as staging, including `start_period: 45s`.

**Failing tests first** — add to `deploy/tests/test_compose_manifests.py` a
parametrised suite so both manifests are checked by the same assertions where
they are identical in shape, plus production-specific name assertions:
- `test_production_manifest_exists`
- `test_production_project_name_is_pinned`
- `test_production_volumes_are_external_by_exact_name`
- `test_production_networks_are_external_by_exact_name`
- `test_production_edge_aliases_do_not_collide_with_staging` — asserts the two
  manifests' edge alias sets are disjoint. **This is the test that would catch a
  copy-paste of the staging aliases**, which is the single most dangerous
  mistake available in this task.
- `test_neither_manifest_places_postgres_on_an_edge_network`
- `test_neither_manifest_contains_a_build_section`
- `test_neither_manifest_publishes_ports`

**Expected failure:** `FileNotFoundError: compose.production.yaml`.

**Verification**
```bash
cd /home/user/product-intelligence
API_IMAGE=x WEB_IMAGE=y RELEASE_SHA=z POSTGRES_DB=d POSTGRES_USER=u POSTGRES_PASSWORD=p \
  docker compose -f compose.production.yaml config --format json \
  | python3 -c "import json,sys; print('project:', json.load(sys.stdin)['name'])"
```
Expected: `project: product-intelligence-production`.

**Commit:** `feat(deploy): production manifest`

---

## Task 06 — Shared Caddy manifest and Caddyfile

Implements spec §7, §E. **Consumes** the aliases from Tasks 04 and 05.

**Files**
- Created: `deploy/caddy/compose.yaml`
- Modified: `docker/caddy/Caddyfile`
- Created: `deploy/caddy/Caddyfile.staging-only`
- Modified: `deploy/tests/test_compose_manifests.py`; created
  `deploy/tests/test_caddyfile.py`

**Behavioural requirements**

`deploy/caddy/compose.yaml`:
1. `name: product-intelligence-shared`.
2. One service `caddy`, image `caddy:2-alpine`, `restart: unless-stopped`,
   ports `80:80` and `443:443`.
3. Volumes — the two Caddy volumes adopted external **under their legacy
   staging names**, with the comment explaining why (§7, D3):
   ```yaml
   volumes:
     caddy_data:
       external: true
       name: product-intelligence-staging_caddy_data     # legacy name: adopting the live ACME state (§7)
     caddy_config:
       external: true
       name: product-intelligence-staging_caddy_config   # legacy name, same reason
     staging_static:
       external: true
       name: product-intelligence-staging_static
     production_static:
       external: true
       name: product-intelligence-production_static
   ```
4. Mounts: `./Caddyfile:/etc/caddy/Caddyfile:ro`, `caddy_data:/data`,
   `caddy_config:/config`, `staging_static:/srv/static/staging:ro`,
   `production_static:/srv/static/production:ro`.
5. Networks: **both edge networks, external, and neither internal network.**
6. No `depends_on` on application services — Caddy is independent of both
   deployments (§7).

**Two Caddyfiles, and why.** The live handoff (L09) happens before
`app.arkav.lol` DNS exists, and a site block for a hostname that does not
resolve produces repeated ACME failures. So:
- `deploy/caddy/Caddyfile.staging-only` — the staging site block **only**. This
  is what L09 installs.
- `docker/caddy/Caddyfile` — both site blocks. This is installed later by an
  explicit control-plane update in L13, after DNS resolves.

Both files use `handle_path /static/*` with the environment's own root, and
proxy to the environment's own aliases, exactly as spec §7 shows. The
`{$ACME_EMAIL}` global block is retained in both.

**Failing tests first** — `deploy/tests/test_caddyfile.py`:
- `test_full_caddyfile_serves_both_hosts` — both `staging.arkav.lol {` and
  `app.arkav.lol {` present.
- `test_staging_only_caddyfile_omits_production` — `app.arkav.lol` absent,
  `staging.arkav.lol` present.
- `test_each_site_roots_its_own_static_directory` — `/srv/static/staging`
  appears only inside the staging block, `/srv/static/production` only inside
  the production block. (Parse by splitting on the site headers; assert no
  cross-membership.)
- `test_each_site_proxies_its_own_aliases` — staging block references
  `staging-api:8000` and `staging-web:3000` and **neither** production alias;
  production block the mirror image.
- `test_no_site_uses_the_ambiguous_bare_service_name` — the strings
  `reverse_proxy api:` and `reverse_proxy web:` appear in neither file.
- `test_neither_caddyfile_logs_query_strings` — asserts no `log` directive is
  present in either file. Rationale: Caddy's access log includes the URI with
  query string by default, and the OAuth callback carries `code` and `state`
  there; M3 removed that leak from gunicorn and this must not reintroduce it
  through the proxy. If a log directive is ever wanted, it needs its own design.
- `test_shared_caddy_joins_both_edges_and_neither_internal`
- `test_shared_caddy_adopts_the_legacy_certificate_volumes` — asserts the two
  legacy names as string literals.
- `test_shared_caddy_mounts_both_static_volumes_read_only`

**Expected failure:** `FileNotFoundError` on `deploy/caddy/compose.yaml`, and
the current single-site `docker/caddy/Caddyfile` failing the routing tests.

**Verification** — tests plus a real Caddy syntax validation of both files:
```bash
cd /home/user/product-intelligence
docker run --rm -v "$PWD/docker/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" \
  caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker run --rm -v "$PWD/deploy/caddy/Caddyfile.staging-only:/etc/caddy/Caddyfile:ro" \
  caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```
Expected: `Valid configuration` from both.
**If no Docker daemon is available in the implementation environment**, record
this check as deferred to L09 step 3, where it is mandatory before cutover —
do not skip it silently, and do not mark the task complete without noting it.

**Commit:** `feat(deploy): shared Caddy serving both environments`

---

## Task 07 — Deploy library: input validation and parser

Implements spec §12.3. First of five library tasks (§F asks for small focused
files rather than one large script).

**Files**
- Created: `deploy/scripts/lib/validate.sh`
- Created: `deploy/tests/test_deploy_validation.py`

**Interfaces produced** — sourced by later scripts:
```bash
pi_validate_sha "$1"          # exit 0 iff ^[0-9a-f]{40}$
pi_validate_digest "$1"       # exit 0 iff ^sha256:[0-9a-f]{64}$
pi_validate_environment "$1"  # exit 0 iff staging|production
pi_api_image_ref "$digest"    # echoes ghcr.io/imiladco/product-intelligence/api@<digest>
pi_web_image_ref "$digest"    # echoes ghcr.io/imiladco/product-intelligence/web@<digest>
pi_parse_command "$env" "$original_command"   # echoes: "deploy <sha> [<api> <web>]" or "status"; exit 2 on reject
```

**Behavioural requirements**
1. Repository prefixes are **hard-coded constants** in this file. No function
   accepts a registry, repository or path from a caller (§8.2).
2. `pi_parse_command` reads the environment from its **first argument** (the
   forced command supplies it) and the client string from its second. It never
   takes the environment from the client string.
3. Grammar, enforced by word count and per-field regex:
   - `staging`: `deploy <sha> <api-digest> <web-digest>` (4 words) or `status`.
   - `production`: `deploy <sha>` (2 words) or `status`.
   - Anything else → exit 2, message `invalid command`, nothing echoed.
4. Rejection is silent about *why* beyond a fixed string, and never echoes the
   offending input back (it may contain injected control characters).
5. No `eval`, no `bash -c`, no unquoted expansion of client input anywhere.
   Parsing uses `read -r -a` into an array.
6. `set -euo pipefail` at the top; functions return status, they do not `exit`
   the caller except `pi_parse_command`'s documented exit 2.

**Failing tests first** — `deploy/tests/test_deploy_validation.py`. Each test
invokes bash: `subprocess.run(["bash", "-c", f'source {LIB}; {call}'], …)`.
- Accepts: a valid 40-char lowercase SHA; a valid digest.
- Rejects, each its own test: 39 chars; 41 chars; uppercase hex; non-hex
  (`g`×40); empty string; a SHA with a trailing newline; a digest without the
  `sha256:` prefix; a digest with 63 or 65 hex chars; `sha512:` prefix.
- **Injection battery** — every one of these must exit non-zero and produce no
  file: `deploy $(touch /tmp/pi_pwn)`, ``deploy `touch /tmp/pi_pwn` ``,
  `deploy aaa…a; touch /tmp/pi_pwn`, `deploy aaa…a && touch /tmp/pi_pwn`,
  `deploy aaa…a | touch /tmp/pi_pwn`, `deploy aaa…a$(printf '\\n')touch /tmp/pi_pwn`.
  Each test asserts `not Path("/tmp/pi_pwn").exists()` afterwards and removes
  the file in a fixture teardown if present.
- `test_production_grammar_rejects_client_digests` — `pi_parse_command production
  "deploy <sha> <api-digest> <web-digest>"` exits 2. **This is the production
  digest-injection guard.**
- `test_staging_grammar_requires_both_digests` — `deploy <sha>` alone exits 2 on
  staging.
- `test_environment_comes_from_the_first_argument` — `pi_parse_command staging
  "deploy production …"` does not yield a production deploy.
- `test_image_reference_is_built_from_a_constant_prefix` — output equals the
  literal expected string; and a test asserting the source file contains no
  variable registry (`grep -c 'ghcr.io' == 2` occurrences, both literal).
- `test_library_contains_no_eval` — source contains neither `eval ` nor
  `bash -c`.

**Expected failure:** `FileNotFoundError` on the library.

**Verification**
```bash
cd /home/user/product-intelligence && .venv/bin/python -m pytest deploy/tests -q -p no:cacheprovider
bash -n deploy/scripts/lib/validate.sh && echo "SYNTAX OK"
shellcheck deploy/scripts/lib/validate.sh || echo "shellcheck not installed — record as not run"
```

**Commit:** `feat(deploy): strict deploy command grammar`

---

## Task 08 — Deploy library: release ledger

Implements spec §13.4, §12.6 Tier B.

**Files**
- Created: `deploy/scripts/lib/ledger.sh`
- Created: `deploy/tests/test_deploy_ledger.py`

**Interfaces produced**
```bash
pi_ledger_path "$env"                      # /opt/product-intelligence/state/<env>.json
pi_ledger_read_current_sha "$env"          # echoes sha or "" 
pi_ledger_read_current_digests "$env"      # echoes "<api_digest> <web_digest>" or ""
pi_ledger_has_successful_release "$env" "$sha"    # exit 0 iff that SHA is recorded successful
pi_ledger_digests_for_sha "$env" "$sha"    # echoes the digests recorded for that successful SHA
pi_ledger_set_candidate "$env" "$sha" "$api" "$web" "$compat"
pi_ledger_promote_candidate "$env"         # candidate → current, current → previous, candidate=null
pi_ledger_clear_candidate "$env"           # failed attempt; current/previous untouched
pi_ledger_append_history "$env" "$sha" "$result" "$detail"
```

**Behavioural requirements**
1. JSON is produced and parsed by **`python3`**, not `jq` — `python3` is present
   on Ubuntu by default and `jq` may not be. No script in this plan requires
   `jq`.
2. **Atomic writes:** write to `<path>.tmp.$$` in the same directory, `chmod
   0644`, then `mv -f` (rename within a filesystem is atomic). Never edit in
   place.
3. Schema exactly as spec §13.4, with `migrations_backward_compatible` recorded
   on each entry.
4. `pi_ledger_has_successful_release` consults `current`, `previous`, **and**
   `history` entries with `"result": "success"`. Rationale: a SHA that passed
   staging and was then superseded must remain promotable to production.
5. A missing or unparseable ledger file is **not** an error for read functions:
   they echo nothing and return non-zero, so callers fail closed.
6. History is append-only, one JSON object per line (JSONL) at
   `state/history/<env>.jsonl`, so a partial write can never corrupt earlier
   records.
7. Every write function is idempotent with respect to re-running the same
   deploy.

**Failing tests first** — `deploy/tests/test_deploy_ledger.py`, using
`tmp_path` and a `PI_STATE_DIR` environment override the library honours
(default `/opt/product-intelligence/state`) so tests never touch a real path:
- Round-trip: set candidate → read back → promote → `current` is the candidate,
  `previous` is the old current, `candidate` is null.
- `test_failed_attempt_leaves_current_and_previous_untouched`.
- `test_missing_ledger_reads_fail_closed` — every read returns non-zero and
  empty output on a nonexistent file.
- `test_corrupt_ledger_reads_fail_closed` — write `{` and assert the same.
- `test_superseded_successful_sha_is_still_promotable` — deploy A, deploy B,
  assert `pi_ledger_has_successful_release staging <A>` still succeeds.
- `test_failed_sha_is_never_promotable` — record a failure for C, assert
  `has_successful_release` is non-zero for C. **This is the false-proof guard.**
- `test_write_is_atomic` — assert no `.tmp` file remains after each write, and
  that the resulting file parses.
- `test_history_is_append_only_jsonl` — three writes produce three parseable
  lines and the first line is unchanged.
- `test_ledger_file_mode_is_readable_but_not_writable_by_others` — `0644`.
- `test_no_jq_dependency` — source contains no `jq` invocation.

**Expected failure:** `FileNotFoundError` on the library.

**Verification:** the tests, `bash -n`, and shellcheck if available.

**Commit:** `feat(deploy): root-owned release ledger`

---

## Task 09 — Deploy library: preflight

Implements spec §13.2, §11.2 (fail-closed compatibility), §6.2.

**Files**
- Created: `deploy/scripts/lib/preflight.sh`
- Created: `deploy/tests/test_deploy_preflight.py`

**Interfaces produced**
```bash
pi_preflight_env_file "$env"            # exists, 0600, root:root, required keys non-empty
pi_preflight_compose_project "$env"     # rendered project name == expected constant
pi_preflight_docker_available
pi_preflight_disk_free "$min_gib"
pi_preflight_external_resources "$env"  # networks and volumes exist
pi_preflight_pull "$image_ref"
pi_preflight_image_revision "$image_ref" "$sha"    # revision label == requested SHA
pi_preflight_migration_compatibility "$api_image_ref"   # exit 0 iff label is exactly "true"
```

**Behavioural requirements**
1. `pi_preflight_env_file` reports **names only** of missing/empty keys, never
   values. Required keys: `APP_DOMAIN`, `APP_URL`, `DJANGO_DEBUG`,
   `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`,
   `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `DATABASE_URL`,
   `CREDENTIAL_ENCRYPTION_KEYS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
   `GOOGLE_OAUTH_REDIRECT_URI`, `GUNICORN_WORKERS`, `ACME_EMAIL`.
2. `pi_preflight_compose_project` runs `docker compose -f <manifest> config
   --format json` with `COMPOSE_PROJECT_NAME` **unset** (`env -u
   COMPOSE_PROJECT_NAME`) and **never** passes `-p`, then compares `.name` to
   the expected constant. Mismatch → fail. This is the §6.2 guard, verified in
   the design to be necessary because `COMPOSE_PROJECT_NAME` overrides the
   `name:` key.
3. `pi_preflight_migration_compatibility` reads
   `docker image inspect --format '{{ index .Config.Labels "org.arkav.pi.migrations-backward-compatible" }}'`
   and succeeds **only** on the exact string `true`. `false`, empty, absent,
   `True`, `TRUE`, `1`, `yes`, or an inspect failure → non-zero. **Fail closed.**
4. `pi_preflight_image_revision` compares the `org.opencontainers.image.revision`
   label to the requested SHA and fails on mismatch — this is what stops a
   digest from a different commit being promoted under a SHA it does not belong
   to. It is a **per-image** function and callers must invoke it for the API
   **and** the web image; a web image from another commit is exactly as wrong as
   an API image from another commit.
5. `pi_preflight_disk_free` takes a minimum in GiB and reads
   `df --output=avail -BG /var/lib/docker` (falling back to `/` if that path
   does not exist), failing below the floor. Default floor: **3 GiB**.
6. Every function prints a single-line reason on failure, prefixed
   `preflight: `, and returns non-zero. No function mutates anything.

**Failing tests first** — `deploy/tests/test_deploy_preflight.py`. Docker is
stubbed by prepending a fake `docker` executable to `PATH` (a small script
written into `tmp_path` that echoes canned output based on its arguments), so
these tests run with no daemon:
- Compatibility matrix, parametrised over label values
  `["true", "false", "", "True", "TRUE", "1", "yes", "<absent>", "<inspect-fails>"]`
  asserting **only** `"true"` succeeds. Nine cases, one assertion each.
- `test_revision_mismatch_is_rejected`.
- `test_revision_match_is_accepted`.
- `test_env_file_missing_is_rejected`.
- `test_env_file_with_wrong_mode_is_rejected` — `0644` fails.
- `test_env_file_missing_key_is_reported_by_name_only` — assert the key name is
  in the message and the *value* of a populated key is not.
- `test_env_file_empty_value_is_rejected`.
- `test_compose_project_mismatch_is_rejected` — fake `docker` returns a
  different `.name`.
- `test_compose_check_unsets_compose_project_name` — set
  `COMPOSE_PROJECT_NAME=hijack` in the test environment and assert the fake
  `docker` recorded that it was invoked **without** it (the fake writes its
  environment to a file the test reads).
- `test_compose_check_never_passes_dash_p` — the fake records its argv; assert
  `-p` is absent.
- `test_disk_below_floor_is_rejected` / `..._above_floor_is_accepted`.

**Expected failure:** `FileNotFoundError` on the library.

**Verification:** tests, `bash -n`, shellcheck if available.

**Commit:** `feat(deploy): preflight that aborts before mutating anything`

---

## Task 10 — Deploy library: health gate

Implements spec §13.3 and **implementation clarification 2** (cross-environment
N/A rule).

**Files**
- Created: `deploy/scripts/lib/healthgate.sh`
- Created: `deploy/tests/test_deploy_healthgate.py`

**Interfaces produced**
```bash
pi_health_containers "$env"                  # compose ps: all running and healthy
pi_health_internal "$env"                    # /api/health inside the api container
pi_health_public "$host" "$expected_sha"     # https://<host>/api/health 200 AND .release == sha
pi_health_login "$host"                      # https://<host>/login → 200
pi_health_static "$host"                     # https://<host>/static/admin/css/base.css → 200
pi_health_cross_environment "$other_env" "$other_host"   # see rule below
pi_health_gate "$env" "$host" "$sha"         # runs all of the above in order
```

**Behavioural requirements**
1. **Bounded retries.** Each check has a fixed attempt count and interval, and
   an overall deadline; no unbounded loop anywhere. Values:
   containers 30×3s (90s), internal 10×3s (30s), public 20×3s (60s),
   login 10×3s (30s), static 10×3s (30s), cross-environment 10×3s (30s).
2. `pi_health_public` parses the JSON body with `python3` and requires **both**
   HTTP 200 **and** `release == expected_sha`. A 200 reporting a different
   release is a **failure** (§14 row 8) — this is what catches a hostname
   cross-wire.
3. **Cross-environment rule (clarification 2), exactly:**
   - Read the other environment's ledger `current.sha`.
   - If it is a non-empty valid SHA → the other environment's public health
     check is **REQUIRED**; failure fails the gate.
   - If the ledger is missing, unparseable, or has `current: null` → record
     `cross_environment: not_applicable` in the deploy log and the history
     entry, and **do not fail the deploy**.
   - **Existence is never inferred from DNS**, from a certificate, from a
     reachable port, or from a container being present. The ledger is the only
     source of truth for "the other environment exists".
   - Once both environments have a `current` entry, the check is mandatory in
     both directions.
4. All HTTP checks use `curl -fsS --max-time 10` and never log the URL's query
   string (there is none) or any header.
5. The gate stops at the first failing check and returns a distinct exit code
   per check so the caller can record which one failed.

**Failing tests first** — `deploy/tests/test_deploy_healthgate.py`, stubbing
`curl` and `docker` on `PATH` and pointing `PI_STATE_DIR` at `tmp_path`:
- `test_public_health_requires_matching_release` — 200 with a different
  `release` fails; 200 with the matching release passes.
- `test_public_health_requires_http_200` — a 503 body with the right release
  still fails.
- `test_cross_environment_is_required_when_the_other_has_a_current_release` —
  write a ledger with `current.sha`, make the stubbed `curl` fail for the other
  host, assert the gate fails.
- `test_cross_environment_is_not_applicable_when_the_other_ledger_is_missing` —
  no ledger file → gate **passes** and the string `not_applicable` is emitted.
- `test_cross_environment_is_not_applicable_when_current_is_null`.
- `test_cross_environment_is_not_inferred_from_dns` — no ledger, but the stubbed
  `curl` would answer for the other host; assert the result is still
  `not_applicable` and that the gate never called `curl` for the other host
  (the stub records its argv).
- `test_each_check_is_bounded` — a stub that always fails returns within the
  documented attempt count (assert the stub's invocation count ≤ the bound).
- `test_gate_stops_at_the_first_failure` — stub containers-check failure; assert
  no HTTP check ran.

**Expected failure:** `FileNotFoundError` on the library.

**Verification:** tests, `bash -n`, shellcheck if available.

**Commit:** `feat(deploy): bounded health gate with cross-environment check`

---

## Task 11 — Deploy library: rollback

Implements spec §11.3, §14 rows 5–7.

**Files**
- Created: `deploy/scripts/lib/rollback.sh`
- Created: `deploy/tests/test_deploy_rollback.py`

**Interfaces produced**
```bash
pi_rollback_is_permitted "$env" "$compat"   # exit 0 iff compat=="true" AND previous digests exist
pi_rollback_execute "$env" "$host"          # rewrite .release/images.env to previous, up -d, re-gate
pi_collect_diagnostics "$env" "$outdir"     # compose ps + last 200 log lines per service
```

**Behavioural requirements**
1. Rollback restores **images only**. `pi_rollback_execute` must contain no
   `migrate`, no `--fake`, no `psql`, no dump/restore, and no reference to any
   schema operation. A test asserts this by source inspection.
2. `pi_rollback_is_permitted` returns non-zero when compatibility is not exactly
   `true`, or when there is no `previous` entry with both digests.
3. On a permitted rollback: rewrite `<env>/.release/images.env` with the
   previous digests, `docker compose … up -d` (project-scoped), then re-run the
   full health gate for that environment.
4. **If the rollback's own health gate fails:** stop. No retry, no loop. Write
   `result: rollback_failed` to history, collect diagnostics, exit non-zero with
   a message beginning `CRITICAL:`. Never attempt a second rollback.
5. `pi_collect_diagnostics` writes to
   `/opt/product-intelligence/state/history/diagnostics-<env>-<utc-timestamp>/`
   and **must not** include `.env` contents, environment variable dumps, or
   `docker inspect` output of environment sections. It captures
   `docker compose ps` and `docker compose logs --tail 200 --no-color` for the
   project's services only.

**Failing tests first** — `deploy/tests/test_deploy_rollback.py`:
- `test_rollback_is_refused_when_migrations_are_incompatible` (`false`).
- `test_rollback_is_refused_on_unknown_compatibility` (empty string).
- `test_rollback_is_refused_without_a_previous_release`.
- `test_rollback_is_permitted_when_compatible_and_previous_exists`.
- `test_rollback_never_touches_the_database` — source scan asserting none of
  `migrate`, `psql`, `pg_restore`, `pg_dump`, `--fake` appears in the file.
- `test_rollback_failure_does_not_retry` — stub the health gate to always fail;
  assert the `up -d` stub was invoked exactly once and the exit message starts
  with `CRITICAL:`.
- `test_diagnostics_exclude_environment_values` — run against a fake project
  whose stubbed logs contain `SECRET_VALUE=hunter2`; assert the collector
  captured logs (so the mechanism works) and then assert the collector's source
  contains no `docker inspect` of `.Config.Env` and no `cat .env`.
- `test_diagnostics_are_scoped_to_the_project` — the stubbed `docker` records
  argv; assert every invocation carries `-f <manifest>` and none is a bare
  `docker logs` or `docker ps -a` without project scoping.

**Expected failure:** `FileNotFoundError` on the library.

**Commit:** `feat(deploy): image-only rollback`

---

## Task 12 — Staging deploy entry point

Implements spec §G, §13. **Consumes** Tasks 07–11.

**Files**
- Created: `deploy/scripts/pi-deploy-staging`
- Created: `deploy/tests/test_deploy_staging_entrypoint.py`

**Grammar (exact)**
```
deploy <40-char-sha> <api-sha256-digest> <web-sha256-digest>
status
```

**Behavioural requirements — the ordered sequence**
1. `flock -n 9` on `/var/lock/pi-deploy-staging.lock`; on contention print
   `deploy already in progress for staging` and exit **75**.
2. Parse and validate via `pi_parse_command staging "$SSH_ORIGINAL_COMMAND"`.
3. Preflight (Task 09), in order: env file → compose project → docker available
   → disk floor → external networks and volumes → pull **by digest** (image
   references built by `pi_api_image_ref`/`pi_web_image_ref` from the validated
   digests) → `pi_preflight_image_revision` for **both** the API and the web
   image, each against the requested SHA.
4. Read the compatibility label from the pulled API image and **record** it.
   Staging does **not** refuse on `false` (§11.2) — it records the value.
5. `pi_ledger_set_candidate staging <sha> <api> <web> <compat>`.
6. Write `/opt/product-intelligence/staging/.release/images.env`:
   `API_IMAGE=…@sha256:…`, `WEB_IMAGE=…@sha256:…`, `RELEASE_SHA=<sha>`
   (mode 0644, atomic write).
7. One-shot release step, **project-scoped**, in this order:
   ```
   docker compose -f compose.staging.yaml run --rm --no-deps api python manage.py migrate --noinput
   docker compose -f compose.staging.yaml run --rm --no-deps api python manage.py collectstatic --noinput
   ```
   `postgres` must already be running; the entry point starts it first with
   `docker compose … up -d postgres` and waits for its healthcheck.
8. `docker compose -f compose.staging.yaml up -d` (recreates api and web with
   the new images).
9. `pi_health_gate staging staging.arkav.lol <sha>`.
10. On success: `pi_ledger_promote_candidate staging`, append history
    `result: success`, print a single-line summary with the SHA and both
    digests. On failure: `pi_collect_diagnostics`, attempt rollback if permitted
    (Task 11), `pi_ledger_clear_candidate`, append history with the failing
    check, exit non-zero.
11. **A failed deploy must never write a successful ledger entry.** The
    promotion in step 10 is the only place `result: success` is written, and it
    is reached only after the gate returns 0.

**Failing tests first** — `deploy/tests/test_deploy_staging_entrypoint.py`,
with `docker`, `flock` and `curl` stubbed on `PATH` and `PI_STATE_DIR` /
`PI_DEPLOY_ROOT` pointed at `tmp_path`:
- `test_rejects_a_command_without_digests`.
- `test_rejects_an_invalid_digest`.
- `test_pulls_by_digest_not_by_tag` — assert every stubbed `docker pull`
  argument contains `@sha256:` and none contains `:<sha>` as a tag.
- `test_revision_is_verified_for_both_images` — the stubbed `docker` records
  its `image inspect` calls; assert the revision label was read for the API
  **and** the web image, and that a mismatching web revision aborts the deploy
  with no migration call.
- `test_migration_runs_before_the_app_is_recreated` — the stub records call
  order; assert `run --rm --no-deps api python manage.py migrate` precedes
  `up -d` (without a service argument).
- `test_postgres_is_started_before_the_migration` — assert `up -d postgres`
  precedes the migrate call. *(This is also what makes clarification 3 work on
  the very first production deploy.)*
- `test_a_failing_health_gate_writes_no_successful_entry` — stub gate failure;
  assert `pi_ledger_has_successful_release staging <sha>` is non-zero afterwards.
- `test_a_successful_deploy_records_digests_and_compatibility`.
- `test_second_concurrent_deploy_exits_75` — hold the lock in the test and
  assert exit code 75 and that no `docker` call was made.
- `test_incompatible_migrations_do_not_block_staging` — compat `false` still
  deploys and is recorded as `false`.

**Expected failure:** `FileNotFoundError` on the script.

**Verification:** tests, `bash -n`, shellcheck if available.

**Commit:** `feat(deploy): staging deploy by digest`

---

## Task 13 — Production deploy entry point

Implements spec §H, §11.2, §14 row 0.

**Files**
- Created: `deploy/scripts/pi-deploy-production`
- Created: `deploy/tests/test_deploy_production_entrypoint.py`

**Grammar (exact)**
```
deploy <40-char-sha>
status
```
No digest input. No confirmation token. No force flag. No override of any kind.

**Behavioural requirements** — same skeleton as Task 12 with these differences:
1. Lock file `/var/lock/pi-deploy-production.lock`.
2. After parsing, **before pulling anything**:
   - `pi_ledger_has_successful_release staging <sha>` must succeed, else refuse
     with `refused: <sha> has no successful staging release`.
   - `pi_ledger_digests_for_sha staging <sha>` supplies the digests. **The
     client's input is never consulted for an image identity.**
3. Pull by those digests; verify both revision labels equal the requested SHA.
4. **Fail-closed compatibility gate, before the migration step:**
   `pi_preflight_migration_compatibility` on the pulled API image must return 0.
   Anything else → refuse with
   `refused: <sha> does not declare backward-compatible migrations` and exit
   non-zero **without running the migration, without recreating anything, and
   without writing a candidate**. This is §14 row 0.
5. The rest of the sequence is identical to Task 12 with production paths, the
   production manifest, and `app.arkav.lol`.

**Failing tests first** — `deploy/tests/test_deploy_production_entrypoint.py`:
- `test_refuses_a_sha_with_no_staging_release` — empty staging ledger; assert
  refusal, exit non-zero, and **zero** `docker pull` calls.
- `test_refuses_a_sha_whose_staging_release_failed`.
- `test_uses_the_digests_from_the_staging_ledger` — ledger holds digests X/Y;
  assert the pulls used X/Y.
- `test_ignores_client_supplied_digests` — send
  `deploy <sha> <other-api> <other-web>`; assert exit 2 (grammar rejection) and
  no pull.
- **Compatibility matrix**, parametrised over
  `["false", "", "True", "TRUE", "1", "yes", "<absent>", "<inspect-fails>"]`:
  each asserts refusal **and** that no `migrate` call was made **and** that no
  candidate was written. Plus one case for `"true"` proceeding.
- `test_no_override_exists` — source scan: the script contains none of
  `--force`, `FORCE`, `--yes`, `CONFIRM`, `override`, `skip-compat`.
- `test_revision_mismatch_is_refused` — parametrised over the API image and the
  web image, so neither is left unverified.
- `test_second_concurrent_production_deploy_exits_75`.
- `test_a_failing_health_gate_does_not_mark_production_current`.

**Expected failure:** `FileNotFoundError` on the script.

**Commit:** `feat(deploy): production promotion of staging-proven digests`

---

## Task 14 — SSH wrapper and control-plane installer

Implements spec §12.2, §12.4, §12.6.

**Files**
- Created: `deploy/scripts/pi-deploy-wrapper`
- Created: `deploy/scripts/pi-install-control-plane`
- Created: `deploy/sudoers/pi-deploy`
- Created: `deploy/ssh/authorized_keys.template`
- Created: `deploy/tests/test_control_plane.py`

**`pi-deploy-wrapper` requirements**
1. Invoked as `pi-deploy-wrapper <environment>` from the forced command. Its
   **first argument is the only source of the environment**.
2. Reads the client string from `SSH_ORIGINAL_COMMAND` (empty if unset).
3. Validates the environment with `pi_validate_environment`; anything else exits
   non-zero.
4. Dispatches with `sudo -n /usr/local/sbin/pi-deploy-<env> <parsed args…>`,
   arguments as array elements, never an interpolated string.
5. Contains no `eval`, no `bash -c`, and never executes `SSH_ORIGINAL_COMMAND`.

**`deploy/sudoers/pi-deploy`** — exactly:
```
deploy ALL=(root) NOPASSWD: /usr/local/sbin/pi-deploy-staging, /usr/local/sbin/pi-deploy-production
```
No wildcards. No `ALL` command. No `NOPASSWD: ALL`.

**`deploy/ssh/authorized_keys.template`** — two commented lines showing the
required form, with `PUBLIC_KEY_HERE` placeholders (public keys are not secrets,
but no real key is committed):
```
command="/usr/local/bin/pi-deploy-wrapper staging",restrict,no-pty ssh-ed25519 PUBLIC_KEY_HERE deploy-staging
command="/usr/local/bin/pi-deploy-wrapper production",restrict,no-pty ssh-ed25519 PUBLIC_KEY_HERE deploy-production
```

**`pi-install-control-plane` requirements** — the §12.6 infrastructure update
procedure, run **by a human as root on the VPS**, never by CI:
1. Refuses to run unless `EUID` is 0.
2. Takes one argument: the path to a checked-out repository at a reviewed
   commit. Refuses if that path is not a git work tree.
3. Installs, with explicit mode and ownership:
   - `deploy/scripts/pi-deploy-wrapper` → `/usr/local/bin/` `0755 root:root`
   - `deploy/scripts/pi-deploy-{staging,production}` → `/usr/local/sbin/`
     `0755 root:root`
   - `deploy/scripts/lib/*.sh` → `/usr/local/lib/pi-deploy/` `0644 root:root`
   - `deploy/sudoers/pi-deploy` → `/etc/sudoers.d/pi-deploy` `0440 root:root`,
     **after** `visudo -cf` validation of the staged file; abort on failure.
   - `compose.staging.yaml` → `/opt/product-intelligence/staging/`
   - `compose.production.yaml` → `/opt/product-intelligence/production/`
   - `deploy/caddy/compose.yaml` → `/opt/product-intelligence/shared/caddy/`
   - the Caddyfile named by `--caddyfile <path>` → 
     `/opt/product-intelligence/shared/caddy/Caddyfile`
4. Records the installed commit in
   `/opt/product-intelligence/state/control-plane.json`
   (`{"installed_from_sha": …, "installed_at": …, "caddyfile": …}`).
5. **Never** touches `.env` files, the ledger, `authorized_keys`, or anything
   outside the paths listed above.
6. Prints what it installed. Prints no file contents.

**Failing tests first** — `deploy/tests/test_control_plane.py`:
- `test_wrapper_takes_the_environment_from_its_argument_not_the_client` — set
  `SSH_ORIGINAL_COMMAND="deploy <sha>"` and invoke the wrapper with `staging`;
  assert the stubbed `sudo` received `/usr/local/sbin/pi-deploy-staging`. Then
  set `SSH_ORIGINAL_COMMAND="production deploy <sha>"` with the same argument
  and assert it still dispatched to **staging** (or rejected) — never to
  production.
- `test_wrapper_rejects_an_unknown_environment`.
- `test_wrapper_contains_no_eval_and_never_executes_the_client_string`.
- `test_sudoers_grants_exactly_two_commands` — parse the file; assert exactly
  two absolute paths, no `ALL` command, no wildcard character.
- `test_sudoers_file_is_syntactically_valid` — `visudo -cf` if available; if
  not installed, assert the file matches the exact expected single line
  (string equality) and record that `visudo` was not run.
- `test_authorized_keys_template_binds_the_environment_and_restricts` — both
  lines contain `command="`, `restrict`, `no-pty`, and no `PermitOpen`,
  `port-forwarding` or `agent-forwarding` enabling option.
- `test_authorized_keys_template_contains_no_real_key` — asserts the
  placeholder is present and no base64 blob longer than 40 chars exists.
- `test_installer_requires_root`.
- `test_installer_touches_no_env_or_ledger_path` — source scan asserting the
  strings `.env` and `state/staging.json` do not appear as install targets.
- **`test_no_workflow_installs_the_control_plane`** — scans
  `.github/workflows/*.yml` (once Tasks 15–17 exist; until then the directory
  may be absent and the test asserts vacuously true) for
  `pi-install-control-plane`, `authorized_keys`, `sudoers`, `/usr/local/sbin`,
  `/usr/local/lib/pi-deploy`, `compose.staging.yaml` **as a copy target**, and
  `scp`/`rsync`. Any hit fails. This is the §12.6 guard against CI acquiring
  control-plane write access, and it is re-run by Task 19.

**Expected failure:** `FileNotFoundError` on the wrapper.

**Commit:** `feat(deploy): environment-bound wrapper and control-plane installer`

---

## Task 15 — CI: quality gates and image builds

Implements spec §10.1.

**Files**
- Created: `.github/workflows/ci.yml`
- Created: `deploy/tests/test_workflows.py`

**Behavioural requirements**
1. Triggers: `pull_request` (all branches) and `push` to `main`.
2. Job `backend`: PostgreSQL 17 service container; install from
   `apps/api/requirements.lock.txt` and `requirements-dev.lock.txt`; run
   `python -m pytest -q` and `python manage.py makemigrations --check --dry-run`
   in `apps/api`. Also runs the `deploy/tests` suite from the repository root
   (`python -m pytest deploy/tests -q -p no:cacheprovider`) so the deploy-script
   tests gate every change.
3. Job `frontend`: Node 22, `npm ci` in `apps/web`, then `npm run test`,
   `npx tsc --noEmit`, `npm run lint`, and
   `NODE_OPTIONS=--max-old-space-size=640 npm run build`.
4. Job `images`: `docker/setup-buildx-action`, then build **both** images with
   `docker/build-push-action` and `push: false`, `load: false`, passing
   `GIT_SHA` and `MIGRATIONS_BACKWARD_COMPATIBLE` build args; GitHub Actions
   cache (`cache-from: type=gha`, `cache-to: type=gha,mode=max`).
5. `permissions:` block at workflow level granting `contents: read` **only**.
6. No step in this workflow logs into GHCR, pushes an image, or connects by SSH.

**Failing tests first** — `deploy/tests/test_workflows.py` (YAML-parsed):
- `test_ci_runs_every_backend_gate` — asserts the pytest and
  `makemigrations --check --dry-run` commands are present as literal strings.
- `test_ci_runs_the_deploy_script_tests`.
- `test_ci_runs_every_frontend_gate` — four literal commands.
- `test_ci_builds_both_images_without_pushing` — two build steps, both with
  `push: false`.
- `test_ci_has_no_registry_login_and_no_ssh`.
- `test_ci_permissions_are_read_only`.

**Expected failure:** `FileNotFoundError` on the workflow.

**Verification:** the tests, plus `python3 -c "import yaml,sys;
yaml.safe_load(open('.github/workflows/ci.yml'))"` printing nothing.

**Commit:** `ci: quality gates and image builds`

---

## Task 16 — CI: publish and auto-deploy staging

Implements spec §10.2, §8.1, §8.2.

**Files**
- Created: `.github/workflows/deploy-staging.yml`
- Modified: `deploy/tests/test_workflows.py`

**Behavioural requirements**
1. Trigger: `workflow_run` on the CI workflow completing **successfully** on
   `main`, or `push` to `main` with an explicit `needs:` chain on the gate jobs.
   Choose one and state it in a comment; a red gate must publish nothing.
2. `permissions: contents: read, packages: write` — the **only** workflow with
   package write.
3. Logs into GHCR with `GITHUB_TOKEN`.
4. Builds and pushes both images with tag `<full-sha>`, passing build args
   `GIT_SHA=<full-sha>` and `MIGRATIONS_BACKWARD_COMPATIBLE` read from
   `deploy/release-metadata.json` with
   `python3 -c "import json;print(str(json.load(open('deploy/release-metadata.json'))['migrations_backward_compatible']).lower())"`.
5. Captures each build's `digest` output (`steps.<id>.outputs.digest`).
6. Deploys staging over SSH using the **staging** deploy key
   (`secrets.STAGING_DEPLOY_KEY`, `secrets.DEPLOY_HOST`, `secrets.DEPLOY_USER`),
   sending exactly:
   `deploy <full-sha> <api-digest> <web-digest>`
   with `ssh -o StrictHostKeyChecking=yes` and a known-hosts entry from
   `secrets.DEPLOY_KNOWN_HOSTS`.
7. **Never** references the production key or the production script.
8. The job fails if the SSH command exits non-zero, and the workflow surfaces
   the deploy script's last line.

**Failing tests first** — additions to `deploy/tests/test_workflows.py`:
- `test_staging_workflow_pushes_by_full_sha_tag`.
- `test_staging_workflow_passes_both_build_args`.
- `test_staging_workflow_sends_both_digests_to_the_deploy_command` — asserts the
  SSH command string contains both digest output references.
- `test_staging_workflow_never_deploys_by_tag` — the SSH command contains
  `${{ steps.` digest references and no `:${{ github.sha }}` tag reference.
- `test_staging_workflow_uses_only_the_staging_key` — `STAGING_DEPLOY_KEY`
  present, `PRODUCTION_DEPLOY_KEY` absent.
- `test_staging_workflow_verifies_the_host_key` — `StrictHostKeyChecking=yes`
  and a known-hosts step present.
- `test_only_the_staging_workflow_has_packages_write`.
- Re-run `test_no_workflow_installs_the_control_plane` from Task 14 — it now has
  real workflows to scan.

**Commit:** `ci: publish images and deploy staging by digest`

---

## Task 16B — Artifact provenance chain (SHA → label → digest)

**Added at review.** Implements the requirement that both images preserve the
Git SHA through OCI labels, and that the whole chain is proven **before** Task
19 closes Part 1.

**Why this is its own task.** Tasks 03, 09, 12 and 13 each assert one link by
reading source or by stubbing Docker. None of them ever builds a real image, so
none can prove that the label a Dockerfile *declares* is the label a built image
actually *carries* at a given digest. Only a real build can, and only CI has a
Docker daemon.

**Files**
- Modified: `.github/workflows/ci.yml` (pull-request half)
- Modified: `.github/workflows/deploy-staging.yml` (main half)
- Created: `deploy/scripts/verify-image-provenance.sh`
- Modified: `deploy/tests/test_workflows.py`
- Created: `deploy/tests/test_image_provenance_script.py`

**Interfaces produced**
```bash
deploy/scripts/verify-image-provenance.sh <image-ref> <expected-sha> [<expected-compat>]
# exit 0 iff:
#   org.opencontainers.image.revision == <expected-sha>
#   and, when <expected-compat> is given,
#       org.arkav.pi.migrations-backward-compatible == <expected-compat>
```
It reads labels with `docker image inspect` and prints, on failure, which label
mismatched and what was found. It is a **verification** script: it mutates
nothing, pulls nothing, and is safe to run against any local image reference.

**Behavioural requirements**
1. **Pull request (no push, no digest).** The `images` job builds both images
   with `load: true` and runs the script against each **by local tag**, checking
   the revision label equals `github.event.pull_request.head.sha`, and for the
   API also that the compatibility label equals the value in
   `deploy/release-metadata.json`. A PR that would produce a mislabelled image
   fails before merge.
2. **`main` (pushed, digest known).** After the push step, for **each** image:
   - resolve the reference as `<repo>@${{ steps.<id>.outputs.digest }}`;
   - `docker pull` that digest reference (proving the digest is real and
     fetchable, not merely reported);
   - run the script against the **digest reference**, not the tag — this is the
     link the tag-based check cannot make;
   - assert the digest reported by the build step equals the digest recorded in
     `docker image inspect --format '{{index .RepoDigests 0}}'`.
3. The job **fails the workflow** on any mismatch, and the staging deploy step
   `needs:` it — so a mislabelled or misreported artifact is never deployed and
   never reaches the ledger.
4. The script itself is provider-neutral and takes the image reference as an
   argument; it hard-codes no registry.

**Failing tests first**

`deploy/tests/test_image_provenance_script.py`, stubbing `docker` on `PATH`
exactly as Task 09 does:
- `test_matching_revision_passes`.
- `test_mismatching_revision_fails` — and the message names the label.
- `test_missing_revision_label_fails` — empty and absent both fail.
- `test_compatibility_is_checked_when_expected_value_is_given`.
- `test_compatibility_is_not_checked_when_omitted` — the web image has no such
  label and must still pass.
- `test_inspect_failure_fails_closed`.
- `test_script_pulls_nothing_and_mutates_nothing` — the stub records argv;
  assert no `pull`, `run`, `push`, `rm`, `tag` or `build` invocation.

Additions to `deploy/tests/test_workflows.py`:
- `test_pr_job_verifies_provenance_for_both_images` — the script is invoked
  twice in `ci.yml`, once per image.
- `test_main_job_verifies_provenance_by_digest_for_both_images` — in
  `deploy-staging.yml`, both invocations use an `@${{ steps.` digest reference,
  and neither uses a `:` tag reference.
- `test_main_job_pulls_each_digest_before_verifying`.
- `test_staging_deploy_step_needs_the_provenance_job` — the deploy step or job
  declares `needs:` on the provenance job, so provenance gates deployment.
- `test_provenance_script_is_not_part_of_the_control_plane` — it lives under
  `deploy/scripts/` but is **not** installed by `pi-install-control-plane`
  (Task 14 installs an explicit list); assert the installer does not reference
  it. It runs in CI, never on the VPS, so it grants CI no server capability.

```bash
cd /home/user/product-intelligence && .venv/bin/python -m pytest deploy/tests -q -p no:cacheprovider
```
**Expected failure:** `FileNotFoundError` on
`deploy/scripts/verify-image-provenance.sh`, then workflow assertions failing
until both workflows call it.

**Verification**
```bash
bash -n deploy/scripts/verify-image-provenance.sh && echo "SYNTAX OK"
cd /home/user/product-intelligence && .venv/bin/python -m pytest deploy/tests -q -p no:cacheprovider
python3 -c "import yaml;[yaml.safe_load(open(f)) for f in ['.github/workflows/ci.yml','.github/workflows/deploy-staging.yml']];print('WORKFLOW YAML OK')"
```

**Environment note, stated rather than discovered later.** This implementation
environment has no Docker daemon (`docker ps` fails; `docker compose config`
works because it renders locally). The label-on-a-real-image assertions
therefore cannot execute here — they execute in CI, on the pull request that
Task 19 opens, which is **before** Part 1 merges. Task 19 records the CI run
result as the evidence for this task. Do not mark Task 16B verified on the
strength of the stubbed tests alone.

**Commit:** `ci: verify artifact provenance for both images`

---

## Task 17 — CI: production promotion workflow

Implements spec §10.3.

**Files**
- Created: `.github/workflows/deploy-production.yml`
- Modified: `deploy/tests/test_workflows.py`

**Behavioural requirements**
1. Trigger: `workflow_dispatch` **only**. No `push`, no `workflow_run`, no
   `schedule`.
2. Exactly one input: `sha`, `required: true`, described as the full 40-character
   commit SHA. **No** digest inputs, **no** confirmation input, **no** force
   input.
3. Validates the input shape in the job before contacting the server:
   `echo "$SHA" | grep -Eq '^[0-9a-f]{40}$'`.
4. `permissions: contents: read` — no `packages: write`. It never builds and
   never pushes.
5. Uses `secrets.PRODUCTION_DEPLOY_KEY` and sends exactly `deploy <sha>`.
6. Reports the deploy script's exit status and last output line as the job
   result.
7. Optionally uses a GitHub `environment:` for a manual approval gate; if used,
   it is named `production` and documented in `docs/DEPLOY.md`.

**Failing tests first** — additions to `deploy/tests/test_workflows.py`:
- `test_production_workflow_is_dispatch_only` — `on` has exactly the key
  `workflow_dispatch`.
- `test_production_workflow_takes_only_a_sha_input`.
- `test_production_workflow_has_no_confirmation_or_force_input`.
- `test_production_workflow_never_builds_or_pushes` — no `build-push-action`,
  no `docker build`, no `packages: write`.
- `test_production_workflow_sends_no_digest`.
- `test_production_workflow_uses_only_the_production_key`.

**Commit:** `ci: manual production promotion`

---

## Task 18 — Examples and documentation

Implements spec §K, §20.

**Files**
- Created: `.env.production.example`
- Modified: `.gitignore`
- Created: `docs/DEPLOY.md`
- Modified: `docs/STAGING.md`
- Modified: `docs/V1_BUILD_PLAN.md`
- Modified: `deploy/tests/test_release_metadata.py` (env-example assertions)

**`.env.production.example`** — placeholders only, mirroring
`.env.staging.example`'s structure with production values:
`APP_DOMAIN=app.arkav.lol`, `APP_URL=https://app.arkav.lol`,
`DJANGO_ALLOWED_HOSTS=app.arkav.lol,api,localhost,127.0.0.1`,
`CSRF_TRUSTED_ORIGINS=https://app.arkav.lol`,
`POSTGRES_DB=product_intelligence_production`,
`GOOGLE_OAUTH_REDIRECT_URI=https://app.arkav.lol/api/integrations/oauth/google/callback`,
`GUNICORN_WORKERS=1`, and empty `DJANGO_SECRET_KEY`,
`CREDENTIAL_ENCRYPTION_KEYS`, `POSTGRES_PASSWORD`, `DATABASE_URL`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ACME_EMAIL`. Every generated value
is empty with a comment naming the generator command. **No real value.**

**`.gitignore`** — add `!.env.production.example` next to the existing
exceptions.

**`docs/DEPLOY.md`** — the operational document. Sections, all of which must be
written in full (no cross-reference stubs):
1. Architecture summary and the two hostnames.
2. **The legacy volume names and why they are kept** (§7, D3).
3. Control-plane installation and update procedure (§12.6) — the exact
   `pi-install-control-plane` invocation, including `--caddyfile`.
4. Secret handling rules: never printed, never an argument, `.env` `0600
   root:root`, generation via the non-echoing heredoc pattern already in
   `docs/STAGING.md`, and the off-server backup requirement for
   `CREDENTIAL_ENCRYPTION_KEYS`.
5. GHCR credential properties (§8.3): must pull both private images; must not
   have package write or delete; stored `0600 root:root` at
   `/etc/product-intelligence/ghcr.env`; rotation steps.
6. Staging auto-deploy flow and how to read the ledger.
7. Production promotion flow, including the exact refusal messages and what each
   means.
8. Rollback semantics — images only; **an explicit statement that no database
   rollback exists and no backup is taken in M7** (§17.1).
9. The failure matrix (§14) reproduced as an operator table.
10. Runbooks: relocation reverse handoff, Caddy revert, control-plane revert.
11. The forbidden-commands list (§0 rule 3 of this plan).

**`docs/STAGING.md`** — add a header note that operation of staging has moved to
`docs/DEPLOY.md` as of M7, that the host-proxy alternative and the
loopback-port sketch are superseded (D11), and that the entrypoint no longer
migrates on start (§9). Keep the historical M3 record intact below it.

**`docs/V1_BUILD_PLAN.md`** — reconcile the M7 section: no `compose.yaml`, no
`scripts/deploy.sh`, no `scripts/backup.sh`; per-environment manifests, a
restricted deploy path, CI-built artifacts, and **backups explicitly deferred**
with a pointer to §17.1.

**Checks**
- `test_production_env_example_has_no_populated_secret` — every one of
  `DJANGO_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEYS`, `POSTGRES_PASSWORD`,
  `GOOGLE_CLIENT_SECRET` is present and empty.
- `test_production_env_example_is_tracked` —
  `git check-ignore .env.production.example` exits non-zero.
- `test_build_plan_no_longer_promises_a_backup_script` — `scripts/backup.sh`
  absent from `docs/V1_BUILD_PLAN.md`.

```bash
git check-ignore -v .env.production.example; echo "exit=$?"   # expect exit=1 (not ignored)
git status --porcelain | grep -c '\.env$'                      # expect 0
```

**Commit:** `docs: deployment runbooks and production env example`

---

## Task 19 — Merged-tree verification and pull request

**Files:** none created; this task verifies and opens the PR.

**Steps**
- [ ] Full gates:
  ```bash
  cd apps/api && ../../.venv/bin/python -m pytest -q
  cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run
  cd /home/user/product-intelligence && .venv/bin/python -m pytest deploy/tests -q -p no:cacheprovider
  cd apps/web && npm run test && npx tsc --noEmit && npm run lint
  cd apps/web && NODE_OPTIONS=--max-old-space-size=640 npm run build
  ```
- [ ] Deployment-specific checks:
  ```bash
  cd /home/user/product-intelligence
  for f in compose.staging.yaml compose.production.yaml; do
    API_IMAGE=x WEB_IMAGE=y RELEASE_SHA=z POSTGRES_DB=d POSTGRES_USER=u POSTGRES_PASSWORD=p \
      docker compose -f "$f" config --format json \
      | python3 -c "import json,sys;print('$f ->', json.load(sys.stdin)['name'])"
  done
  bash -n deploy/scripts/pi-deploy-wrapper deploy/scripts/pi-deploy-staging \
          deploy/scripts/pi-deploy-production deploy/scripts/pi-install-control-plane \
          deploy/scripts/lib/*.sh && echo "SHELL SYNTAX OK"
  grep -rn "docker system prune\|docker volume prune\|docker network prune" deploy/ .github/ ; echo "exit=$?"
  ```
  Expected: correct project names; `SHELL SYNTAX OK`; the grep exits 1 (no hits).
- [ ] Confirm no workflow can install the control plane (Task 14 test passes
      against the now-real workflows).
- [ ] `git status --short` clean; push the branch.
- [ ] Open a **draft** PR whose body contains: the task list, the gate results,
      the statement that **no server, DNS, secret or deployment change has been
      made**, and the note that Part 2 requires explicit approval.

**Commit:** `chore: M7 repository verification`

**GATE — Part 2 does not begin until this PR is reviewed and merged.**

---

# PART 2 — LIVE INFRASTRUCTURE (APPROVAL-GATED)

**Do not start any task in this part until:**
1. The Part 1 PR is merged to `main`, and
2. The user has explicitly approved beginning live infrastructure work.

Every task states its STOP conditions. All commands are scoped to Product
Intelligence. **No task in this part may touch `n8n`, `cloudflared`, Portainer,
`x-ui`/Xray, or any unrelated container, volume, network or database.**

---

## Task L01 — VPS inventory and unrelated-service baseline

**Type:** inspect only. Nothing is modified.

- [ ] Record, and paste into the tracking issue:
  ```bash
  hostname; uname -a; lsb_release -d
  docker --version; docker compose version
  docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  docker volume ls
  docker network ls
  ss -ltnp | grep -E ':80|:443' || echo "nothing on 80/443"
  systemctl is-active docker
  ```
- [ ] Write down the **uptime and container ID** of every unrelated service —
  `n8n`, `cloudflared`, Portainer, `x-ui`/Xray, any unrelated Postgres. This is
  the baseline the final acceptance (L15) compares against to prove they were
  never restarted.

**STOP if:** anything other than the staging Caddy is bound to :80 or :443
(the cutover assumes staging owns them); or the Docker version does not support
`docker compose` v2 external volume declarations.

---

## Task L02 — Staging project, volume, data and credential baseline

**Type:** inspect only. Implements spec §6.1.

- [ ] Confirm project identity and resources:
  ```bash
  cd /opt/product-intelligence-staging
  echo "COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-<unset>}"
  docker compose -f compose.staging.yaml config --format json \
    | python3 -c "import json,sys;print('project:', json.load(sys.stdin)['name'])"
  docker volume ls --filter label=com.docker.compose.project=product-intelligence-staging
  docker network ls --filter label=com.docker.compose.project=product-intelligence-staging
  docker compose -f compose.staging.yaml ps --format json | python3 -m json.tool | head -40
  ```
  Expected: project `product-intelligence-staging`; the four volumes named in
  §1.2 of the spec.
- [ ] **Row-count snapshot** (the number that proves data survived):
  ```bash
  docker compose -f compose.staging.yaml exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c \
    "select (select count(*) from accounts_user),
            (select count(*) from projects_project),
            (select count(*) from integrations_integrationconnection),
            (select count(*) from integrations_integrationcredential);"
  ```
  Record the four numbers verbatim.
- [ ] **Credential decrypt proof (before):** in the browser, confirm at least one
  integration reads `Connected` on its remembered resource. Record which.

**STOP if:** the project name differs, any of the four volumes is missing, or
`COMPOSE_PROJECT_NAME` is set in the operating shell. Report before proceeding.

---

## Task L03 — Host resource baseline (pre-relocation)

**Type:** inspect only. Feeds the L12 capacity gate.

- [ ] Record:
  ```bash
  free -m
  swapon --show || echo "no swap"
  df -h / /var/lib/docker
  uptime
  docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}'
  ```
- [ ] Note the **total** memory used by all Product Intelligence containers, and
  separately by everything else. These two numbers are the inputs to L12.

**STOP if:** free disk on the Docker filesystem is under 5 GiB — the relocation
pre-pulls images and needs headroom.

---

## Task L04 — Install `deploy` user, forced keys, sudoers, control plane

**Type:** mutate (additive only — nothing existing is changed or stopped).

- [ ] Generate two SSH key pairs **on a trusted workstation, not on the VPS**,
  and add the **public** keys to the server. Private keys go into GitHub secrets
  by the user; this plan never creates or reads GitHub secrets.
- [ ] On the VPS, as root:
  ```bash
  # /bin/bash, NOT /usr/sbin/nologin. sshd runs a forced command through the
  # account's login shell as `shell -c "<command>"`, so nologin would print
  # "This account is currently not available." and exit before
  # pi-deploy-wrapper ever ran -- every deploy would fail. The security
  # boundary is the forced command plus the key restrictions below, not the
  # shell: `restrict,no-pty` denies PTY, agent, port and X11 forwarding, and
  # the client's string is never executed.
  useradd --system --create-home --shell /bin/bash deploy
  install -d -m 0700 -o deploy -g deploy /home/deploy/.ssh
  # authorized_keys built from deploy/ssh/authorized_keys.template with the two
  # real public keys substituted; installed root-owned so `deploy` cannot edit it:
  install -m 0644 -o root -g root /tmp/authorized_keys /home/deploy/.ssh/authorized_keys
  ```
  Note: `deploy` must be able to *read* `authorized_keys` for sshd but must not
  own or be able to write it. Confirm sshd accepts this (`sshd -T | grep -i
  strictmodes`); if `StrictModes` requires user ownership, use
  `AuthorizedKeysFile /etc/ssh/authorized_keys.d/%u` with a root-owned file
  instead, and record which arrangement was used.
- [ ] Install the control plane from a checked-out repository at the merged M7
  commit:
  ```bash
  sudo /path/to/repo/deploy/scripts/pi-install-control-plane /path/to/repo \
       --caddyfile /path/to/repo/deploy/caddy/Caddyfile.staging-only
  ```
  Expected: prints each installed path; writes `state/control-plane.json`.
- [ ] Verify:
  ```bash
  ls -l /usr/local/bin/pi-deploy-wrapper /usr/local/sbin/pi-deploy-*
  ls -l /etc/sudoers.d/pi-deploy && visudo -cf /etc/sudoers.d/pi-deploy
  id deploy
  getent passwd deploy
  ```
  Expected: `0755 root:root` scripts, `0440 root:root` sudoers, `parsed OK`,
  `deploy` **not** in the `docker` group, and a login shell of `/bin/bash` --
  a forced command cannot run without one.

**STOP if:** `id deploy` shows the `docker` group, `visudo -cf` fails, or
`getent passwd deploy` shows `nologin` or `/bin/false`.

---

## Task L05 — Negative proofs: key isolation and no Docker access

**Type:** inspect only. These are acceptance evidence, recorded verbatim.

- [ ] **The staging key cannot deploy production.** From the workstation:
  ```bash
  ssh -i staging_deploy_key deploy@<host> "deploy 0000000000000000000000000000000000000000"
  ```
  Expected: rejected by the staging grammar (staging requires digests) — and
  crucially, **no production script is invoked**. Record the output.
  ```bash
  ssh -i staging_deploy_key deploy@<host> "production deploy <valid-sha>"
  ```
  Expected: still dispatches to staging or is rejected; **never** production.
- [ ] **The deploy user has no free Docker access:**
  ```bash
  ssh -i staging_deploy_key deploy@<host> "docker ps"
  ```
  Expected: rejected by the forced command (the client string is not executed).
  Then, as an admin, `sudo -u deploy docker ps` → expected: permission denied on
  the Docker socket.
- [ ] **The deploy user cannot read secrets:**
  `sudo -u deploy cat /opt/product-intelligence/staging/.env` → permission
  denied. `sudo -u deploy cat /etc/product-intelligence/ghcr.env` → permission
  denied (after L06).

**STOP if:** any of these succeeds where it should fail.

---

## Task L06 — GHCR read-only credential and pull demonstration

**Type:** mutate (additive).

- [ ] The user creates a credential meeting the §8.3 properties. **This plan
  does not read, print or store the value in any transcript.**
- [ ] Install it as `0600 root:root` at `/etc/product-intelligence/ghcr.env`
  using a non-echoing heredoc (the pattern in `docs/STAGING.md`).
- [ ] **Demonstrate pull capability** (property 1) and **absence of write**
  (property 2):
  ```bash
  # as root, using the stored credential:
  docker login ghcr.io --username <user> --password-stdin < /dev/null   # see DEPLOY.md for the exact non-echoing form
  docker pull ghcr.io/imiladco/product-intelligence/api:<some-pushed-sha>
  docker push ghcr.io/imiladco/product-intelligence/api:probe-should-fail   # EXPECTED TO FAIL
  ```
  Expected: pull succeeds; push is **denied**. Record both outcomes.

**STOP if:** the push succeeds — the credential is over-privileged and must be
replaced before continuing.

---

## Task L07 — Networks and storage skeleton

**Type:** mutate (additive). Implements spec §6.4 Phase 0 step 4.

- [ ] Create, checking first so the command is safe to re-run:
  ```bash
  for n in product-intelligence-staging-edge product-intelligence-staging-internal \
           product-intelligence-production-edge product-intelligence-production-internal; do
    docker network inspect "$n" >/dev/null 2>&1 || docker network create "$n"
  done
  docker volume inspect product-intelligence-production_static >/dev/null 2>&1 \
    || docker volume create product-intelligence-production_static
  ```
- [ ] **Do not create** `product-intelligence-production_pgdata` here — it is
  created in L13, so "production starts empty" is one checkable step.
- [ ] Verify the four networks and the production static volume exist, and that
  the four staging volumes from L02 are **untouched**.

**STOP if:** any pre-existing volume's name or driver changed.

---

## Task L08 — Pre-pull candidate image digests

**Type:** mutate (additive). Implements spec §6.4 Phase 0 step 5.

- [ ] Identify the merged M7 `main` commit SHA whose images CI published.
- [ ] Pull both images **by digest** as root, so the cutover window contains no
  network transfer:
  ```bash
  docker pull ghcr.io/imiladco/product-intelligence/api@sha256:<api-digest>
  docker pull ghcr.io/imiladco/product-intelligence/web@sha256:<web-digest>
  docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
    ghcr.io/imiladco/product-intelligence/api@sha256:<api-digest>
  ```
  Expected: the revision label equals the SHA.

**STOP if:** the revision label does not match, or the pull fails.

---

## Task L09 — Relocation and Caddy cutover — **STOP/GO, PRODUCES AN OUTAGE**

**Type:** mutate. This is the only outage-producing task in the milestone.
Implements spec §6.4 Phases 1–2 with **implementation clarification 1**.

**GO/NO-GO checklist — every line must be YES before starting:**
- [ ] L01–L08 complete, with all recorded values.
- [ ] Row-count snapshot from L02 written down.
- [ ] Images pre-pulled (L08) and revision-verified.
- [ ] Control plane installed with the **staging-only** Caddyfile (L04).
- [ ] `.env` copied and verified byte-identical:
  ```bash
  install -m 600 -o root -g root /opt/product-intelligence-staging/.env \
      /opt/product-intelligence/staging/.env
  cmp /opt/product-intelligence-staging/.env /opt/product-intelligence/staging/.env && echo IDENTICAL
  ```
- [ ] Caddyfile validated offline:
  ```bash
  docker run --rm -v /opt/product-intelligence/shared/caddy/Caddyfile:/etc/caddy/Caddyfile:ro \
    caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  ```
  Expected: `Valid configuration`.
- [ ] Relocated manifest renders with the right project name and external
  volumes resolve:
  ```bash
  cd /opt/product-intelligence/staging
  env -u COMPOSE_PROJECT_NAME docker compose -f compose.staging.yaml config --format json \
    | python3 -c "import json,sys;print('project:', json.load(sys.stdin)['name'])"
  ```
  Expected: `project: product-intelligence-staging`.
- [ ] `.release/images.env` written with the pre-pulled digests and the SHA.
- [ ] **User has given explicit GO for the outage.**

**Cutover — clarification 1: two explicit stop commands, in this order.**
Do not rely on Compose honouring the order of a multi-service argument list.

- [ ] **T0 — stop the public proxy first:**
  ```bash
  cd /opt/product-intelligence-staging
  docker compose -f compose.staging.yaml stop caddy
  ```
  Expected: only `caddy` stops. Verify with `docker compose ps`.
- [ ] **Then stop the application services:**
  ```bash
  docker compose -f compose.staging.yaml stop api web postgres
  ```
  `stop`, never `down`. No `-v`. No volume or network removal.
- [ ] **Start the relocated stack:**
  ```bash
  cd /opt/product-intelligence/staging
  env -u COMPOSE_PROJECT_NAME docker compose -f compose.staging.yaml up -d
  ```
  Wait for `api` and `web` healthy.
- [ ] **Start shared Caddy:**
  ```bash
  cd /opt/product-intelligence/shared/caddy
  docker compose up -d
  ```
- [ ] **T1 — downtime ends:** `curl -fsS https://staging.arkav.lol/api/health`
  returns 200. Record T0 and T1 and the elapsed seconds.

**Reverse handoff — equally explicit, if any step above fails:**
```bash
cd /opt/product-intelligence/shared/caddy && docker compose stop
cd /opt/product-intelligence/staging && docker compose -f compose.staging.yaml stop
cd /opt/product-intelligence-staging && \
  docker compose -f compose.staging.yaml --profile caddy --env-file .env up -d
```
Before relying on it, confirm the previously built local images still exist
(`docker image ls | grep product-intelligence-staging`); the old manifest
contains `build:` sections and would otherwise attempt a host build — which this
milestone forbids. If they are gone, **stop and report** rather than building.

**STOP immediately if:** two Caddy processes ever contend for :80/:443; any
unrelated container changes state; or the relocated stack fails to start.

---

## Task L10 — Post-relocation verification

**Type:** inspect only. Implements spec §6.4 Phase 2.

- [ ] **Row counts must match L02 exactly:**
  ```bash
  cd /opt/product-intelligence/staging
  docker compose -f compose.staging.yaml exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c \
    "select (select count(*) from accounts_user),
            (select count(*) from projects_project),
            (select count(*) from integrations_integrationconnection),
            (select count(*) from integrations_integrationcredential);"
  ```
  **STOP if any number differs — especially a zero.** Do not "re-seed", do not
  run migrations to "fix" it: the wrong volume was mounted. Reattach the correct
  volume.
- [ ] **Credential decrypt proof (after):** the same integration identified in
  L02 still reads `Connected` on the same remembered resource in the browser.
  This proves `CREDENTIAL_ENCRYPTION_KEYS` still decrypts stored credentials.
- [ ] Public checks: `/api/health` 200 with the expected `release`; `/login`
  200; `/static/admin/css/base.css` 200; `http://` redirects to `https://`;
  certificate valid and **not newly issued** (check `notBefore` — it should
  predate the cutover, proving the adopted volumes were used).
- [ ] Unrelated services unchanged: compare container IDs and uptimes to L01.

**STOP if:** the certificate was re-issued (the volumes were not adopted), or any
unrelated service restarted.

---

## Task L11 — Staging auto-deploy proof

**Type:** mutate (staging only).

- [ ] Merge a trivial, safe change to `main` (for example a comment-only edit to
  a documentation file) and let CI run.
- [ ] Observe: gates pass → images built and pushed → staging deploy invoked
  with `deploy <sha> <api-digest> <web-digest>`.
- [ ] Verify on the server:
  ```bash
  cat /opt/product-intelligence/state/staging.json | python3 -m json.tool
  curl -fsS https://staging.arkav.lol/api/health
  ```
  Expected: `current.sha` equals the new commit; both digests recorded; the
  health endpoint reports that SHA.
- [ ] **Confirm the cross-environment check recorded `not_applicable`** — this
  is clarification 2's first real exercise, because production does not exist
  yet. Check the history entry:
  ```bash
  tail -1 /opt/product-intelligence/state/history/staging.jsonl | python3 -m json.tool
  ```
  Expected: a field recording `cross_environment: not_applicable`, and
  `result: success`.

**STOP if:** the deploy fails because the cross-environment check was treated as
required. That is a clarification-2 defect: fix it in the repository, re-merge,
and re-run — do not patch the server.

---

# PART 3 — CAPACITY, PRODUCTION, ACCEPTANCE

---

## Task L12 — Capacity acceptance gate — **GO/STOP**

**Type:** inspect and decide. Implements spec §16. **No production service is
created or started before this task returns GO.**

- [ ] Measure the host with staging running and every unrelated service running:
  ```bash
  free -m
  swapon --show || echo "no swap"
  df -h / /var/lib/docker
  uptime
  docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'
  ```
- [ ] Record, in a table: total RAM; RAM used; RAM available (the `available`
  column, not `free`); swap total and used; per-container memory for **every**
  container; 1/5/15-minute load; free disk on the Docker filesystem.
- [ ] **Estimate the production increment from measured staging values**, not
  from guesses: production's `postgres`, `api` and `web` are the same images
  with the same worker count, so the expected increment is the **measured**
  staging total for those three services.

**Decision procedure — conservative, and stated before the numbers are known:**

| Condition | Decision |
|---|---|
| `available` RAM after subtracting the measured staging three-service total leaves **≥ 300 MiB** headroom, and swap use is 0, and free Docker disk ≥ 5 GiB | **GO** |
| Headroom between 0 and 300 MiB, or swap already in use, or free disk < 5 GiB | **STOP** — report the numbers and the options |
| Projected headroom negative | **STOP** |

- [ ] If **GO**: set `mem_limit` for each service in both manifests from the
  measured values plus 50% headroom, as a repository change through the normal
  PR + control-plane update path — **not** by editing files on the server.
- [ ] If **STOP**: report the measurements and the honest options (a larger VPS,
  or running staging only when needed). **Do not add swap. Do not stop or
  degrade any unrelated service. Do not proceed.**

---

## Task L13 — Production bootstrap — **STOP/GO**

**Type:** mutate. Implements spec §15 and **implementation clarification 3**.
Runs only after L12 returns GO.

- [ ] **DNS (user action):** `app.arkav.lol` A record → VPS IP, Cloudflare
  **DNS-only** (grey cloud). Verify: `dig +short app.arkav.lol` returns the VPS
  IP and `dig +short app.arkav.lol @1.1.1.1` agrees. **STOP if** the record is
  proxied — Caddy's HTTP-01 challenge would fail.
- [ ] Create the production directory and install its control plane:
  ```bash
  install -d -m 0755 -o root -g root /opt/product-intelligence/production
  ```
  then re-run `pi-install-control-plane` from the merged commit (it installs
  `compose.production.yaml`).
- [ ] **Secrets:** generate unique `DJANGO_SECRET_KEY`,
  `CREDENTIAL_ENCRYPTION_KEYS` and `POSTGRES_PASSWORD` straight into
  `/opt/product-intelligence/production/.env` with the non-echoing heredoc from
  `docs/DEPLOY.md`. `chmod 600`. **Nothing is printed.** Verify with the
  names-and-booleans checker from `docs/STAGING.md` (adapted in `docs/DEPLOY.md`).
- [ ] **Back up `CREDENTIAL_ENCRYPTION_KEYS` off the server** before any user
  data exists. Confirm the user has done so. **STOP if not** — unlike staging,
  production users cannot simply reconnect.
- [ ] **Google OAuth (user action):** add
  `https://app.arkav.lol/api/integrations/oauth/google/callback` to the **same**
  client's Authorized redirect URIs. **Added, never replacing staging's.**
  Verify staging's URI is still listed afterwards.
- [ ] **Create the empty database volume:**
  ```bash
  docker volume create product-intelligence-production_pgdata
  ```
- [ ] **Clarification 3 — start Postgres only, and prove it is empty:**
  ```bash
  cd /opt/product-intelligence/production
  env -u COMPOSE_PROJECT_NAME docker compose -f compose.production.yaml up -d postgres
  # wait for healthy:
  until [ "$(docker inspect -f '{{.State.Health.Status}}' product-intelligence-production-postgres-1)" = healthy ]; do sleep 3; done
  docker compose -f compose.production.yaml exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c \
    "select count(*) from information_schema.tables where table_schema='public';"
  ```
  Expected: `0` — an empty database, no Django tables yet. **STOP if non-zero:**
  the wrong volume was attached.
  The application is **not** started here; the ordinary release path (L14)
  starts it. There is no bespoke bootstrap start.
- [ ] **Caddy production site:** update the control plane to the full Caddyfile
  and reload — a reload, not a recreate, so staging is not disturbed:
  ```bash
  sudo /path/to/repo/deploy/scripts/pi-install-control-plane /path/to/repo \
       --caddyfile /path/to/repo/docker/caddy/Caddyfile
  docker compose -f /opt/product-intelligence/shared/caddy/compose.yaml exec caddy \
       caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
  ```
- [ ] Verify the certificate is obtained for `app.arkav.lol` and that
  `https://staging.arkav.lol/api/health` is **still** 200 throughout.

**STOP if:** staging health breaks at any point, or the certificate cannot be
obtained.

---

## Task L14 — First production promotion

**Type:** mutate. Uses the ordinary path — no bespoke artifact, no rebuild.

- [ ] Choose a SHA that has a **successful staging** ledger entry (L11's, or a
  later one).
- [ ] Run the production workflow: `workflow_dispatch` with that SHA only.
- [ ] Expected server-side sequence: staging-proof lookup → digests from the
  staging ledger → pull by digest → revision labels match → compatibility label
  is exactly `true` → `up -d postgres` (already running) → `migrate` one-shot →
  `collectstatic` one-shot → `up -d` → health gate → ledger promotion.
- [ ] Verify:
  ```bash
  curl -fsS https://app.arkav.lol/api/health          # 200, release == that SHA
  curl -fsS https://staging.arkav.lol/api/health      # 200, release == staging's SHA
  cat /opt/product-intelligence/state/production.json | python3 -m json.tool
  ```
  The two `release` values must **differ** unless the same SHA is deployed to
  both — this is the routing proof.
- [ ] Confirm the production history entry now records a **required** (not
  `not_applicable`) cross-environment check, because staging has a `current`
  entry. And confirm the next staging deploy also treats production as required.

**STOP if:** production reports staging's release or vice versa — that is a
hostname cross-wire (§14 row 8) and must be fixed before use.

---

## Task L15 — Final V1 acceptance checklist

**Type:** inspect. Implements spec §18 exactly. Record each item PASS / FAIL /
NOT EXECUTED with evidence.

**A — Platform**
- [ ] 1. `https://app.arkav.lol/api/health` → 200, `release` equals the promoted SHA.
- [ ] 2. Valid certificate; `http://` redirects to `https://`.
- [ ] 3. `https://staging.arkav.lol/api/health` → 200 with **staging's** release.
- [ ] 4. Django admin login page renders **with CSS** on both hosts
  (`/static/admin/css/base.css` → 200).

**B — Application (production)**
- [ ] 5. Sign up, sign out, sign in.
- [ ] 6. Session and CSRF cookies are `Secure`; the session cookie is `HttpOnly`.
- [ ] 7. Create a project.
- [ ] 8. GA4: OAuth connect → resource selection → Test connection → Change
  property → Disconnect → reconnect.
- [ ] 9. Search Console: the same sequence.

**C — Isolation and hygiene**
- [ ] 10. A fresh production user sees no staging project, and vice versa.
- [ ] 11. Different Postgres volumes, databases and credentials — verified by
  **name**, never by printing values.
- [ ] 12. Access-log scan for `ya29.`, `1//`, `client_secret`, `"access_token"`,
  `"refresh_token"`, `code=`, `state=` — **counts only**, expected zero:
  ```bash
  for p in 'ya29\.' '1//' 'client_secret' '"access_token"' '"refresh_token"' 'code=' 'state='; do
    printf '%s: ' "$p"
    docker compose -f /opt/product-intelligence/production/compose.production.yaml \
      logs --no-color api | grep -cE "$p" || true
  done
  ```
- [ ] 13. Stored credentials are Fernet-encrypted at rest (inspect that the
  column does not contain a readable token; never print the ciphertext in full).

**D — Deployment mechanics**
- [ ] 14. Staging auto-deploy proof (L11 evidence).
- [ ] 15. Production promotion used the **same digests** recorded by staging —
  compare the two ledger entries.
- [ ] 16. Production refuses a SHA that never passed staging (expected failure,
  recorded).
- [ ] 17. The staging deploy key cannot deploy production (L05 evidence).
- [ ] 18. Rollback proof — **only if a safe application-level failure is
  naturally available.** Otherwise record **NOT EXECUTED** and say why. Never
  ship a deliberately broken migration, never disable a guard.
- [ ] 19. Unrelated services untouched: `n8n`, `cloudflared`, Portainer and
  `x-ui` show container IDs and uptimes matching the L01 baseline.

No Google grant revocation is performed.

**Final report to the user:** every item with its result, the capacity numbers
from L12, the measured cutover downtime from L09, and any NOT EXECUTED item with
its reason.

---

## 2. Spec coverage map

Every section of the approved design maps to at least one task.

| Spec section | Tasks |
|---|---|
| §0 Scope / non-goals | Plan §0 ground rules; every task's constraints |
| §1 Current-state findings | 02 (§1.4 entrypoint), 04 (§1.1–1.2 project/volumes, §1.7 sizing), 06 (§1.3 Caddy), 15–17 (§1.6 no CI), 18 (§1.7 gitignore) |
| §2 Decisions D1–D14 | D1/D13 → 12, 13, 16; D2/D3 → 04, 05, 06; D4 → 06, L09; D5 → 02; D6 → 14, L04, L05; D7 → 08; D8/D9/D14 → 03, 09, 11, 13; D10 → 04, 05, L12; D11 → 04, 05; D12 → 14, L04 |
| §3 Target architecture | 04, 05, 06 |
| §4 Filesystem layout | 14 (installer paths), L04, L07 |
| §5 Docker and network topology | 04, 05, 06, L07 |
| §6 Staging relocation | L02, L07, L08, L09, L10 |
| §7 Caddy migration | 06, L09, L10 |
| §8 Artifacts, GHCR, digests | 03, 12, 13, 16, **16B**, L06, L08 |
| §9 Migration as a release step | 02, 12, 13 |
| §10 CI design | 15, 16, 17; §10.4 → 01 |
| §11 Migration policy | 03, 09, 13 |
| §12 Deploy security model | 07, 14, L04, L05; §12.6 → 14, L04 |
| §13 Execution, locking, health, ledger | 08, 09, 10, 12, 13 |
| §14 Failure matrix | 09, 10, 11, 12, 13; operator table in 18 |
| §15 Production bootstrap | L13 |
| §16 Resource limits | 04, 05, L03, L12 |
| §17 Deferred risks | 18 (documented in `docs/DEPLOY.md` §8) |
| §18 Acceptance checklist | L15 |
| §19 Rollback and recovery | 11, 18, L09 reverse handoff |
| §20 Files expected to change | Tasks 01–18 collectively |
| §21 Review answers | Covered by the sections they cite |
| §22 Self-review | Plan self-review below |

## 3. The three implementation clarifications

| Clarification | Where it is encoded |
|---|---|
| **1 — Caddy stop order.** Two explicit commands; never rely on argument order | Task **L09** cutover ("T0 — stop the public proxy first" as its own command, then `stop api web postgres`), and the reverse handoff, also as separate explicit commands |
| **2 — Cross-environment health gate is N/A until the other environment has a `current` ledger entry; existence is never inferred from DNS** | Task **10** behavioural requirement 3 and its five dedicated tests, including `test_cross_environment_is_not_inferred_from_dns`; exercised live in **L11** and required from **L14** onward |
| **3 — First production database start** | Task **L13**: create the volume, `up -d postgres` **only**, wait healthy, prove `0` public tables; the application is started by the ordinary release path in **L14**. Task **12** requirement 7 and its test `test_postgres_is_started_before_the_migration` make `--no-deps` safe by starting Postgres first in every deploy |

## 4. Plan self-review

Run against the 15 required checks.

| # | Check | Result |
|---|---|---|
| 1 | Every spec section maps to a task | Yes — §2 above, all 22 sections |
| 2 | Three clarifications represented | Yes — §3 above, each with a named task and test |
| 3 | No placeholders | Searched for `TODO`, `TBD`, `similar to`, `as appropriate`, `add tests`, `implement appropriately`: **zero occurrences**. Every task names exact paths and exact commands |
| 4 | Task interfaces consistent | Library functions declared in 07–11 are consumed by name in 12–13; `.release/images.env` produced in 12/13 is consumed by the manifests in 04/05; digests produced in 16 are consumed by 12's grammar |
| 5 | No task lets CI modify the control plane | Task 14's `test_no_workflow_installs_the_control_plane` scans the workflows for installer paths, `scp`/`rsync` and privileged targets; re-run in Task 19. Control-plane installation is L04/L13 by a root human only |
| 6 | Staging never deploys by mutable tag | Task 12 requirement 3 and `test_pulls_by_digest_not_by_tag`; Task 16 `test_staging_workflow_never_deploys_by_tag` |
| 7 | Production never accepts a client digest | Task 07 `test_production_grammar_rejects_client_digests`; Task 13 `test_ignores_client_supplied_digests`; Task 17 `test_production_workflow_sends_no_digest` |
| 8 | Unknown/incompatible metadata cannot enter production | Task 09 nine-case parametrised matrix (only `"true"` passes); Task 13 eight-case refusal matrix asserting no pull, no migrate, no candidate; Task 03 defaults the build arg to `false` |
| 9 | Ordinary API startup cannot migrate | Task 02 static and behavioural tests; the migration lives only in Tasks 12/13 step 7 |
| 10 | First production Postgres starts before the `--no-deps` migration | Task L13 (`up -d postgres`, wait healthy, prove empty) and Task 12 requirement 7 / `test_postgres_is_started_before_the_migration` |
| 11 | Staging auto-deploy works before production exists | Task 10 N/A rule; Task L11 explicitly verifies `cross_environment: not_applicable` and instructs that a failure there is a repository defect to fix, not a server patch |
| 12 | No live work before repository merge | Part 2 preamble plus the Task 19 GATE |
| 13 | No task touches unrelated services | Plan §0 rule 3; L01 and L10/L15 baseline-compare their uptimes; forbidden-command grep in Task 19 |
| 14 | Capacity is a GO/STOP gate, not a claim | Task L12 states the decision table **before** the numbers, forbids adding swap or degrading staging, and blocks all production tasks |
| 16 | Provenance chain SHA → label → digest proven before Task 19 | Task **16B**: real builds in CI verify both images' revision labels, the API compatibility label, and that the reported digest is the fetchable one; the staging deploy `needs:` that job. Static links are held by 03, 09, 12, 13 |
| 15 | Rollback never claims to restore the database | Task 11 requirement 1 and `test_rollback_never_touches_the_database`; `docs/DEPLOY.md` §8 states plainly that no database rollback and no backup exist in M7 |

**Issues found and fixed inline while writing this plan**

1. *Task 02 would have broken an existing test.* `tests/test_access_log.py`
   parses the entrypoint with an anchored regex and asserts a literal gunicorn
   flag substring. The task now forbids reformatting that block and adds
   `test_access_log_contract_is_preserved` so a future edit fails with an
   explanatory name.
2. *`jq` may not exist on the VPS.* The spec's inspection commands used it. All
   scripted JSON in this plan uses `python3`, and Task 08 has
   `test_no_jq_dependency`.
3. *The deploy-script tests needed a home.* `apps/api/pyproject.toml` sets
   `testpaths = ["tests"]`, so shell tests placed there would be Django-coupled.
   They live in `deploy/tests/` and run from the repository root with an
   explicit path, requiring no Django settings.
4. *Caddy would have failed at the handoff.* A single Caddyfile with an
   `app.arkav.lol` block would produce ACME failures before DNS exists. Task 06
   produces **two** Caddyfiles; L09 installs the staging-only one and L13
   swaps in the full one after DNS.
5. *`authorized_keys` ownership versus sshd `StrictModes`.* A root-owned
   `authorized_keys` in `~deploy` may be rejected depending on configuration.
   L04 checks `sshd -T` and names the `AuthorizedKeysFile` alternative rather
   than discovering it during a cutover.
6. *The reverse handoff could trigger a host build.* The old manifest still has
   `build:` sections. L09's reverse procedure checks the local images exist
   first and stops rather than building on the host.

**Blockers / decisions for the user before Part 2**

None block Part 1. Three need the user before Part 2 begins, and each is called
out in its task: creating and installing the two SSH key pairs and the four
GitHub secrets (L04, 16, 17); creating the GHCR read-only credential (L06); and
the DNS record plus the Google redirect-URI addition (L13). This plan does not
create, read, or print any of them.
