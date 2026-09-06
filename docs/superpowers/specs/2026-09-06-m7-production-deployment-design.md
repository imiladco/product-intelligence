# Milestone 7 — Production deployment and launch hardening

**Status:** design only. No implementation, no server change, no DNS change.
**Base commit:** `d77c57f1a7274fe262d72c5b26d6f67005610f01` (M6 merge).
**Design branch:** `claude/m7-production-design`.

M7 finishes V1 by giving the project a deployment architecture it does not yet
have: immutable artifacts built once in CI, automatic staging deploys, manual
production promotion of the *same* artifacts, and two isolated environments on
one small VPS behind one shared reverse proxy.

It is not a product milestone. Nothing in it changes what the application does
for a user, with one small exception argued for in §10.4.

---

## 0. Scope

### In scope

- GitHub Actions CI that runs the existing quality gates and builds two images.
- Private GHCR as the artifact store, addressed by **digest**.
- Automatic staging deployment on green `main`.
- Manual production promotion by `workflow_dispatch`, of an artifact that
  already passed staging.
- A second isolated environment (`production`) on the same host: its own
  database, volumes, networks, secrets, and empty initial state.
- One shared Caddy, independent of both application deployments.
- Relocation of the current staging deployment into a new directory layout,
  **without losing its database, its OAuth credentials, or its certificate**.
- A restricted deploy path: dedicated Unix user, forced SSH commands, root-owned
  scripts, no Docker group membership, no arbitrary shell.
- A minimal root-owned release ledger recording candidate / current / previous
  per environment.
- Explicit migration and `collectstatic` as a one-shot release step, out of the
  API container's normal startup.
- Bounded health gating and automatic **image** rollback.
- A production bootstrap runbook and a V1 acceptance checklist.

### Out of scope — and not to be added by the back door

Ingestion, analytics, Clarity, Google Ads, AI, findings, dashboards. Sentry,
Grafana, Prometheus, Loki, any observability platform. Kubernetes, Nomad,
Swarm, ArgoCD, service mesh. A backup platform or automated pre-deploy database
backup (§17.1 — a consciously deferred risk). A secret-management platform.
Blue/green with two live production stacks. Any server-side application build.
Any change to `n8n`, `cloudflared`, Portainer, `x-ui`/Xray, or unrelated
databases. Signup policy, which is unchanged and not an M7 concern.

The user is building a separate monitoring product. This project gets only the
health and deploy checks needed to operate itself.

---

## 1. Current-state findings

Everything below was read from the repository at `d77c57f`, not assumed. Five
of these findings materially change the design; they are marked **[M]**.

### 1.1 The staging Compose project is already explicitly named — **[M]**

`compose.staging.yaml` begins:

```yaml
name: product-intelligence-staging
```

Compose resolves the project name in this order: `-p` flag →
`COMPOSE_PROJECT_NAME` → the `name:` key in the file → the directory name. The
`name:` key is set, so **the directory name is not what identifies the
project today**. Moving `/opt/product-intelligence-staging` to
`/opt/product-intelligence/staging` therefore does not by itself rename the
project or its volumes.

Verified against this repository rather than recalled (Compose v5.1.1):

```
$ docker compose -f compose.staging.yaml config → name: product-intelligence-staging
$ COMPOSE_PROJECT_NAME=override-attempt docker compose … config → name: override-attempt
$ docker compose -p flag-attempt … config → name: flag-attempt
```

The second line is the reason §6.2 is a hard guard and not a formality: an
inherited `COMPOSE_PROJECT_NAME` in the deploying shell **does** override the
`name:` key, and would send every volume lookup to a different, non-existent
project — which, without §6.3, is precisely how an empty database appears.

### 1.2 The real volume names, and the trap inside them — **[M]**

The staging project declares four volumes:

```yaml
volumes:
  pgdata_staging:
  caddy_data:
  caddy_config:
  static:
```

Compose prefixes each with the project name, so on disk they are:

| Compose key | Actual Docker volume | Holds |
|---|---|---|
| `pgdata_staging` | `product-intelligence-staging_pgdata_staging` | the staging database |
| `caddy_data` | `product-intelligence-staging_caddy_data` | **ACME account + certificates** |
| `caddy_config` | `product-intelligence-staging_caddy_config` | Caddy autosave config |
| `static` | `product-intelligence-staging_static` | staging `collectstatic` output |

`docs/STAGING.md` independently confirms the database volume name in its
teardown instructions (`docker volume rm
product-intelligence-staging_pgdata_staging`), and rendering the manifest
confirms all four:

```
$ docker compose --profile caddy -f compose.staging.yaml config
  volumes: caddy_config, caddy_data, pgdata_staging, static
  → product-intelligence-staging_{caddy_config,caddy_data,pgdata_staging,static}
```

**The trap:** the key is `pgdata_staging`, not `pgdata`. A tidy-looking new
`compose.staging.yaml` that renames the key to `pgdata` keeps the same project
name and still produces a **different volume** —
`product-intelligence-staging_pgdata` — which Docker creates empty, on the spot,
with no warning. Postgres then initialises a fresh cluster and staging comes up
looking healthy with zero users, zero projects, and zero integrations. The old
volume still exists, so the data is recoverable, but the failure is silent and
looks like success. §6.3 makes this outcome structurally impossible rather than
merely discouraged.

### 1.3 Caddy currently lives inside the staging Compose project — **[M]**

Caddy is a service of `compose.staging.yaml` under `profiles: [caddy]`, binding
host `80:80` and `443:443`, mounting `./docker/caddy/Caddyfile` read-only plus
the three volumes above. Its certificate state is therefore owned by the staging
project. Any naive "move Caddy to its own project" creates new empty
`caddy_data` / `caddy_config` volumes, discards a valid Let's Encrypt account
and certificate, and re-issues against rate limits. §7 handles this as a
deliberate one-time handoff that keeps the existing volumes.

### 1.4 The API entrypoint ignores its arguments — **[M]**

`apps/api/Dockerfile` ends with `ENTRYPOINT ["/app/entrypoint.sh"]` and declares
**no `CMD`**. `docker/api/entrypoint.sh` runs `collectstatic --clear`, then
`migrate`, then `exec gunicorn …`, and **never references `"$@"`**.

Two consequences, both central to M7:

1. Every ordinary container start mutates the database schema. That was a
   defensible staging choice — the entrypoint comment and `docs/STAGING.md` both
   say so, and a single replica has no concurrent-migration hazard — but it is
   not a release-control model. A container restarted by `restart:
   unless-stopped` at 3am applies migrations.
2. **`docker compose run api python manage.py migrate` does not work today.**
   The arguments are discarded and the container runs the full
   collectstatic → migrate → gunicorn sequence anyway, then keeps a gunicorn
   process alive. Any one-shot release step written against the current image
   would appear to succeed while doing something else entirely.

§9 specifies the minimal entrypoint and Dockerfile change that fixes both.

### 1.5 The web image is genuinely environment-agnostic — **[M]**

Build-once only works if the frontend image contains no environment-specific
values. It does not:

- `grep -rn "NEXT_PUBLIC"` over `apps/web` (excluding `node_modules`) returns
  **nothing**. No public env var is inlined at build time.
- `INTERNAL_API_BASE_URL` is read at **runtime** in `apps/web/lib/api/server.ts`
  (`process.env.INTERNAL_API_BASE_URL ?? "http://127.0.0.1:8000"`).
- The `next.config.ts` rewrite that also reads it is explicitly
  development-only (`if (process.env.NODE_ENV === "production") return []`).

So one web image serves staging and production, differing only by runtime
environment. No design workaround is needed. The same is true of the API image,
which reads everything from the environment.

### 1.6 There is no CI at all

There is no `.github` directory in the repository. CI is greenfield: nothing to
preserve, nothing to migrate, no existing workflow semantics to respect.

### 1.7 Runtime and host facts that constrain the design

- Staging publishes `127.0.0.1:8001` (api) and `127.0.0.1:3001` (web) so a host
  proxy could reach them. With a containerised shared Caddy on both edge
  networks, these publishes are unnecessary — and a second environment would
  need a second pair of ports. §5.4 removes them.
- `GUNICORN_WORKERS=1` in `.env.staging.example`, with the image defaulting to
  `3`. The comment says the default "stays right for a production host" — on a
  *different* host. Production here is the same single-core box. §16 chooses 1
  for both, deliberately.
- The API healthcheck has `start_period: 120s`, sized for entrypoint migrations.
  Once migrations leave startup, that can come down (§9.4).
- `DJANGO_ALLOWED_HOSTS` must list `api` because Next.js SSR calls
  `http://api:8000` (Host: `api:8000`). This is why §5.3 keeps web→api traffic
  on the internal network and puts the disambiguated aliases only on the edge.
- Django sets secure cookies only when it sees `X-Forwarded-Proto: https`
  (`SECURE_PROXY_SSL_HEADER`, `SESSION_COOKIE_SECURE = not DEBUG`). Caddy sets
  that header. Getting it wrong fails sign-in silently — the single most likely
  broken-deployment symptom, per `docs/STAGING.md`.
- `SECURE_HSTS_SECONDS` is one year with `includeSubDomains` and `preload`.
  `staging.arkav.lol` and `app.arkav.lol` are both subdomains of `arkav.lol`;
  §17.4 records the implication.
- `.gitignore` ignores `.env.*` with explicit `!` exceptions for
  `.env.example` and `.env.staging.example`. A new `.env.production.example`
  needs its own exception or it will be silently untracked (§20).
- The API image installs `curl`; the web image (node:alpine) has none and uses
  busybox `wget`. Health probes must keep using the right tool per image.

---

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Images are addressed by **digest** everywhere after the first pull | A tag can be moved; a digest cannot. Production must run the bytes that passed staging |
| D2 | Both environment manifests declare **every** persistent volume `external: true` with its exact name | An external volume that does not exist is a hard error. Compose can then never silently create an empty database |
| D3 | The existing staging volumes keep their legacy names, including `product-intelligence-staging_pgdata_staging` | Renaming a volume means copying data; keeping the name means not touching it. Legacy names are documented, not tidied |
| D4 | Shared Caddy **adopts** the existing `caddy_data` / `caddy_config` volumes as external | Preserves the ACME account and the live staging certificate. Avoids re-issuance and rate limits |
| D5 | The API entrypoint gains argument passthrough; migrations and `collectstatic` leave normal startup | Ordinary container restarts must never mutate schema |
| D6 | Environment is bound to the **SSH key**, not to a client-supplied argument | A compromised staging deploy key cannot address production, whatever it sends |
| D7 | Release state is a root-owned JSON ledger under `/opt/product-intelligence/state/` | Answers "did this SHA pass staging?" without a database or a service |
| D8 | Automatic rollback restores **images only**, never schema | Image rollback and database rollback are different things. Pretending otherwise is how data is lost |
| D9 | A release declares whether its migrations are backward compatible; incompatible releases forbid automatic rollback | The honest form of D8 |
| D10 | `GUNICORN_WORKERS=1` in both environments | One core, two stacks, plus unrelated workloads |
| D11 | No application ports are published to the host in either environment | Shared Caddy reaches both over Docker networks. Removes a whole class of port collisions and a public-exposure risk |
| D12 | The compose files, Caddyfile and deploy scripts are installed as a versioned **config bundle**, not by a Git checkout on the server | Ordinary deploys need no Git on the box; config changes are still explicit and auditable |

---

## 3. Target architecture

```
                     GitHub (main, green)
                            │
                    ┌───────┴────────┐
                    │ CI: test+build │  pytest · makemigrations --check
                    │                │  vitest · tsc · eslint · next build
                    └───────┬────────┘  docker build (api, web)
                            │ push by tag = full SHA, capture digests
                            ▼
                   ghcr.io (PRIVATE)
                    api@sha256:… · web@sha256:…
                            │
        auto (staging)      │      manual workflow_dispatch (production)
        ────────────────────┼──────────────────────────────────────────
                            ▼
   SSH forced command, per-environment key, to user `deploy`
                            │
                sudo → root-owned /usr/local/sbin/pi-deploy-<env>
                            │
   flock → preflight → pull by digest → one-shot migrate+collectstatic
        → recreate app → health gate → ledger update (or rollback)
                            │
╔═══════════════════════════▼════════════════════════════════════════════╗
║ VPS (Ubuntu · 1 vCPU · ~2 GiB) — unrelated services untouched          ║
║                                                                        ║
║   n8n · cloudflared · Portainer · x-ui/Xray   ← never addressed        ║
║                                                                        ║
║   ┌────────────── shared Caddy (owns :80/:443) ──────────────┐         ║
║   │  staging.arkav.lol → staging-api / staging-web           │         ║
║   │  app.arkav.lol     → production-api / production-web     │         ║
║   │  /srv/static/staging   (ro)   /srv/static/production (ro)│         ║
║   └──────┬───────────────────────────────────┬───────────────┘         ║
║          │ staging-edge                      │ production-edge         ║
║   ┌──────▼──────────────────┐         ┌──────▼──────────────────┐      ║
║   │ staging-web  staging-api│         │ prod-web      prod-api  │      ║
║   │        staging-internal │         │      production-internal│      ║
║   │            postgres     │         │            postgres     │      ║
║   └─────────────────────────┘         └─────────────────────────┘      ║
╚════════════════════════════════════════════════════════════════════════╝
```

Caddy joins both edge networks and **neither** internal network. Neither
Postgres is reachable from an edge network, from the host, or from the internet.

---

## 4. Filesystem layout

```
/opt/product-intelligence/
├── staging/
│   ├── compose.staging.yaml        # installed from the config bundle
│   ├── .env                        # root:root 0600, never in Git
│   └── .release/                   # rendered per-deploy image digests
├── production/
│   ├── compose.production.yaml
│   ├── .env                        # root:root 0600, distinct secrets
│   └── .release/
├── shared/
│   └── caddy/
│       ├── compose.yaml
│       └── Caddyfile               # both site blocks
├── state/                          # root-owned release ledger
│   ├── staging.json
│   ├── production.json
│   └── history/                    # append-only deploy records
└── config-bundles/                 # unpacked, SHA-named config snapshots
    └── <sha>/
```

Ownership: everything `root:root`. `deploy` needs **no** write access anywhere
under `/opt/product-intelligence` (§12). `.env` files are `0600 root:root` and
readable only by root and therefore only by the root-owned deploy script.

`/opt/product-intelligence-staging` is **not deleted** by this design. After the
move it is left in place, stopped, as a rollback affordance, and removed only by
an explicit later decision once staging has been verified healthy in its new
home (§6.6).

---

## 5. Docker and network topology

### 5.1 Networks

Four application networks, all created **externally** and declared `external:
true` in the manifests, so no deployment can invent one by accident:

| Network | Members | Purpose |
|---|---|---|
| `product-intelligence-staging-edge` | caddy, staging api, staging web | proxy → app |
| `product-intelligence-staging-internal` | staging api, web, postgres | app → database |
| `product-intelligence-production-edge` | caddy, production api, production web | proxy → app |
| `product-intelligence-production-internal` | production api, web, postgres | app → database |

There is no shared application edge network. Nothing on the staging edge can
address anything on the production edge; the only common member is Caddy, which
has no route between them.

### 5.2 Aliases — how Caddy tells the two apart

Both projects contain services literally named `api` and `web`. A container
joined to two networks that both expose the name `api` is exactly the ambiguity
to avoid. Each service therefore carries an explicit alias on its **edge**
network only:

```yaml
# compose.staging.yaml (shape, not final text)
services:
  api:
    networks:
      internal:
      edge:
        aliases: [staging-api]
  web:
    networks:
      internal:
      edge:
        aliases: [staging-web]
  postgres:
    networks: [internal]          # never on edge
```

Production is identical with `production-api` / `production-web`. The Caddyfile
addresses only the aliases. `staging-api` resolves on the staging edge and
nowhere else; `production-api` likewise. Even if a future edit put Caddy on one
network too many, the names cannot collide.

### 5.3 Web → API stays internal

Next.js SSR calls the API server-side. That traffic stays on the internal
network using the plain service name, so `INTERNAL_API_BASE_URL` remains
`http://api:8000` in both environments and `DJANGO_ALLOWED_HOSTS` keeps its
existing `api` entry unchanged.

This is deliberate. Routing SSR over the edge alias would require
`DJANGO_ALLOWED_HOSTS` to gain `staging-api` / `production-api` in lockstep, and
forgetting it returns `400 DisallowedHost` — the same class of failure
`.env.staging.example` already warns about at length. Caddy preserves the
original `Host` header when proxying, so requests arriving through Caddy carry
the public hostname, which is already in `ALLOWED_HOSTS`.

**Implementation must not "simplify" this** by pointing SSR at the edge alias.

### 5.4 No published application ports

Neither environment publishes `api` or `web` to the host (D11). Shared Caddy
reaches both over the edge networks. This removes the 8001/3001-versus-8002/3002
collision question entirely and shrinks the host's listening surface to Caddy's
`:80`/`:443` plus whatever the unrelated services already own.

Consequence for `docs/STAGING.md`: its "existing proxy on the host" alternative
(the nginx sketch) no longer applies to the relocated staging. That document is
superseded for staging operation and updated in implementation (§20).

---

## 6. Staging relocation — preserving data and credentials

This is the highest-risk part of M7 and the one most likely to look like it
worked. The order below is deliberate.

### 6.1 Inspect before touching anything

Record, and paste into the implementation PR:

```bash
cd /opt/product-intelligence-staging
docker compose -f compose.staging.yaml config --format json | jq -r .name
docker compose -f compose.staging.yaml ps --format json
docker volume ls --filter label=com.docker.compose.project=product-intelligence-staging
docker network ls --filter label=com.docker.compose.project=product-intelligence-staging
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}'   # note unrelated services
echo "${COMPOSE_PROJECT_NAME:-<unset>}"
```

Expected: project `product-intelligence-staging`; the four volumes named in
§1.2; containers `product-intelligence-staging-{postgres,api,web,caddy}-1`. If
any of that differs, **stop** — the rest of this section assumes it and the
divergence must be understood first.

Also capture the row counts that prove the data survived:

```bash
docker compose -f compose.staging.yaml exec -T postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c \
  "select (select count(*) from accounts_user),
          (select count(*) from projects_project),
          (select count(*) from integrations_integrationconnection),
          (select count(*) from integrations_integrationcredential);"
```

### 6.2 Protect against a project rename

The move is safe *because* of the `name:` key (§1.1). Two guards make that
non-negotiable rather than incidental:

- The relocated `compose.staging.yaml` keeps `name: product-intelligence-staging`
  verbatim.
- The deploy script **never** passes `-p` and explicitly unsets
  `COMPOSE_PROJECT_NAME`, then asserts the resolved name matches the expected
  constant before running any mutating command.

### 6.3 Make an empty database structurally impossible

Every persistent volume in the relocated manifest is declared external by exact
name:

```yaml
volumes:
  pgdata_staging:
    external: true
    name: product-intelligence-staging_pgdata_staging   # legacy name, kept on purpose
  static:
    external: true
    name: product-intelligence-staging_static
```

If the volume does not exist, Compose fails with `external volume … not found`
and starts nothing. There is no path where Postgres initialises a new cluster
because a name drifted. This is the answer to "design the move so accidental
creation of a fresh empty staging database is impossible": **not care, but a
failure mode that cannot produce an empty database.**

The `pgdata_staging` key and its doubled-up name are kept exactly as-is (D3).
Renaming would mean copying a live database for cosmetics.

### 6.4 Sequence

1. Snapshot the counts and volume list (§6.1). Copy `.env` to the new path with
   `install -m 600 -o root -g root`, and verify it is byte-identical.
2. `docker compose -f compose.staging.yaml stop` **in the old directory**
   (`stop`, not `down`: no network or volume removal). Caddy is handled in §7 and
   may keep running through this step.
3. Install the config bundle into `/opt/product-intelligence/staging/`.
4. Create the four external networks and confirm the external volumes resolve:
   `docker compose -f compose.staging.yaml config` must succeed and
   `docker compose … config --format json | jq -r .name` must still print
   `product-intelligence-staging`.
5. `docker compose -f compose.staging.yaml up -d` from the **new** directory.
6. Re-run the count query. The numbers must match §6.1 **exactly**. A zero where
   there was a non-zero means the wrong volume was mounted: stop, do not
   "re-seed", and reattach the correct volume.
7. Sign in through the browser and confirm one existing integration still reads
   `Connected` on its remembered resource — proof that
   `CREDENTIAL_ENCRYPTION_KEYS` still decrypts what is stored, which is the real
   test of credential preservation.

### 6.5 What is preserved

| Volume | Action | Result |
|---|---|---|
| `product-intelligence-staging_pgdata_staging` | reattached, external | users, projects, integrations, encrypted credentials |
| `product-intelligence-staging_static` | reattached, external | staging static assets |
| `product-intelligence-staging_caddy_data` | adopted by shared Caddy (§7) | ACME account + live certificate |
| `product-intelligence-staging_caddy_config` | adopted by shared Caddy (§7) | Caddy autosave |

No volume is created, renamed, copied, or removed during the relocation.

### 6.6 Old directory

Left in place and stopped. It is the fastest rollback for the relocation itself.
Removal is a separate, later, explicit decision — never part of a deploy.

---

## 7. Caddy migration — a controlled one-time handoff

Only one process can bind `:80`/`:443`, so this is a brief, deliberate cutover,
not a rolling change.

**Adoption, not recreation.** `/opt/product-intelligence/shared/caddy/compose.yaml`
uses project name `product-intelligence-shared` and declares:

```yaml
volumes:
  caddy_data:
    external: true
    name: product-intelligence-staging_caddy_data     # legacy name, intentional
  caddy_config:
    external: true
    name: product-intelligence-staging_caddy_config   # legacy name, intentional
```

The names keep saying `staging` and that is correct: they hold the ACME account
and certificates for *both* hostnames from now on. Reusing them preserves a
valid certificate and a known-good ACME account; renaming would mean copying
certificate state for the sake of a nicer string, and copying certificate state
is how you end up re-issuing against Let's Encrypt rate limits. The legacy name
is documented here, in the compose file comment, and in `docs/DEPLOY.md`.

**Static volumes.** Shared Caddy mounts both, read-only, at distinct paths:

```yaml
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
      - staging_static:/srv/static/staging:ro
      - production_static:/srv/static/production:ro
```

Two mount points, two volumes, no union, no collision. Each site block roots its
own `handle_path /static/*`:

```caddyfile
staging.arkav.lol {
	handle_path /static/* { root * /srv/static/staging; file_server }
	handle /api/*   { reverse_proxy staging-api:8000 }
	handle /admin/* { reverse_proxy staging-api:8000 }
	handle          { reverse_proxy staging-web:3000 }
}

app.arkav.lol {
	handle_path /static/* { root * /srv/static/production; file_server }
	handle /api/*   { reverse_proxy production-api:8000 }
	handle /admin/* { reverse_proxy production-api:8000 }
	handle          { reverse_proxy production-web:3000 }
}
```

Django admin and DRF assets keep working in both environments — the current
architecture is preserved, not dropped, and each environment serves its own
build of them.

**Cutover sequence** (staging downtime measured in seconds):

1. Install the shared Caddy directory and Caddyfile. Do **not** start it.
2. Validate the config without binding ports:
   `docker run --rm -v …/Caddyfile:/etc/caddy/Caddyfile:ro caddy:2-alpine caddy validate --config /etc/caddy/Caddyfile`.
3. Ensure the relocated staging stack is up on the new networks (§6.4) so the
   aliases resolve.
4. `docker compose -f compose.staging.yaml stop caddy` in the **old** project —
   scoped to one service of one project; ports free.
5. `docker compose -f shared/caddy/compose.yaml up -d` — same volumes, same
   certificate, now serving `staging.arkav.lol` from the shared project.
6. Verify staging over public HTTPS before doing anything else.
7. `app.arkav.lol` is added to the Caddyfile **only after its DNS A record
   resolves to the VPS** (§15). Caddy obtains that certificate on first request;
   until DNS is valid, adding the site block produces repeated ACME failures.

The old Caddy service is stopped, never `down`-ed with volume removal, and the
old project's other services are already stopped from §6.4. `cloudflared`, n8n,
Portainer and x-ui are never addressed by any command here — every command names
a project or a single service.

**Independence.** From this point Caddy is its own Compose project. Deploying
staging or production touches only that environment's project, so Caddy is never
recreated by an application deploy. Caddy changes are their own explicit
operation.

---

## 8. Artifacts, GHCR and release identity

### 8.1 Build once

CI builds two images per green `main` commit and pushes them to **private**
GHCR:

```
ghcr.io/imiladco/product-intelligence/api:<full-40-char-sha>
ghcr.io/imiladco/product-intelligence/web:<full-40-char-sha>
```

The tag is the full commit SHA — never `latest`, never a branch name, never a
short SHA. The VPS never builds application source after M7; neither
`compose.staging.yaml` nor `compose.production.yaml` contains a `build:` section
in the final architecture.

### 8.2 Digests are the real identity

A tag is a mutable pointer. The design therefore treats the **digest** as
identity:

- CI records the digests emitted by `docker buildx build --push`
  (`docker/build-push-action` exposes them) into a release manifest attached to
  the workflow run.
- The staging deploy pulls by tag once, immediately resolves what it pulled
  (`docker image inspect --format '{{index .RepoDigests 0}}'`), and writes those
  digests into the staging ledger entry.
- **Production pulls by digest**, taken from the staging ledger entry for that
  SHA — not by tag, and never rebuilt. If someone force-pushes a tag between
  staging and production, production still runs the exact bytes staging
  validated, and the pull simply fails if those bytes are gone.

Each environment's `.release/` directory holds a small generated env file
(`API_IMAGE=…@sha256:…`, `WEB_IMAGE=…@sha256:…`) that its compose file
interpolates, so the manifest itself stays free of hard-coded digests.

### 8.3 GHCR credential on the VPS

A GitHub personal access token with **`read:packages` only** — no repo scope, no
`write:packages`, no `delete:packages`. Stored at
`/etc/product-intelligence/ghcr.env`, `0600 root:root`, used by the root-owned
deploy script to `docker login ghcr.io` on the root Docker context. The `deploy`
user cannot read it (§12). It is never committed, never passed as a command
argument, and never echoed. Rotation is a documented manual step.

---

## 9. Migration and `collectstatic` as an explicit release step

### 9.1 The minimal image change

Given §1.4, the smallest change that makes one-shot commands possible:

```bash
#!/usr/bin/env bash
set -euo pipefail

# Anything after the entrypoint runs as given: `… run --rm api python
# manage.py migrate` must run *that*, not the server. Without this the
# arguments are silently discarded and gunicorn starts instead.
if [ "$#" -gt 0 ] && [ "$1" != "serve" ]; then
    exec "$@"
fi

# `serve` (the image default) starts the app server and NOTHING else:
# no collectstatic, no migrate. A container restarted by the Docker daemon
# must never change database schema.
exec gunicorn config.wsgi:application …   # flags and access-log format unchanged
```

with `CMD ["serve"]` added to the Dockerfile. The gunicorn invocation, including
the access-log format that deliberately omits the query string, is carried over
byte-for-byte — that redaction is a security control from M3 and is not
re-litigated here.

### 9.2 The one-shot release step

```bash
docker compose -f compose.<env>.yaml run --rm --no-deps \
    api python manage.py migrate --noinput
docker compose -f compose.<env>.yaml run --rm --no-deps \
    api python manage.py collectstatic --noinput
```

Run with the **candidate** image, before the long-running app containers are
recreated. `--rm` leaves nothing behind; `--no-deps` avoids restarting Postgres.

`--clear` is dropped from `collectstatic`. With a shared, per-environment static
volume and a live Caddy serving from it, `--clear` empties the directory before
repopulating it, creating a window where the running site has no admin CSS. The
default overwrite behaviour is correct here. (Stale files accumulate only across
asset renames; that is a cosmetic, bounded cost.)

### 9.3 Ordering, and why migrations run first

`pull → migrate → collectstatic → recreate app → health gate`.

Migrating before the new code starts means the **previous** release is briefly
running against the **new** schema. That is safe only for backward-compatible
migrations, which is precisely why §11 requires expand/contract and why §11.2
exists for the cases that are not.

### 9.4 Healthcheck timing

With migrations out of startup, the API `start_period` drops from 120s to 45s,
and the web service no longer needs `depends_on: api: service_healthy` to be
generous. Retries and timeouts stay bounded (§13).

---

## 10. CI design (to be implemented later)

### 10.1 Pull requests and `main` — quality gates

Unchanged from what the project already enforces locally:

| Gate | Command |
|---|---|
| Backend tests | `pytest -q` (Postgres service container) |
| Migration drift | `python manage.py makemigrations --check --dry-run` |
| Frontend tests | `npm run test` |
| Types | `npx tsc --noEmit` |
| Lint | `npm run lint` |
| Frontend build | `npm run build` |
| Image builds | `docker buildx build` for api and web (build only, no push) |

On a pull request the images are built and **not** pushed — proof the Dockerfiles
still work without publishing artifacts for unmerged code.

### 10.2 `main` only — publish and deploy staging

After every gate passes: build both images with buildx and GitHub Actions cache,
push by full-SHA tag, capture digests, then invoke the staging deploy over SSH.
A red gate publishes nothing and deploys nothing.

### 10.3 Production — `workflow_dispatch` only

Inputs: the full 40-character SHA, and a typed confirmation string. The workflow
verifies the SHA passed staging by reading the ledger through the deploy path
(the server is the authority, not the workflow), then invokes the production
deploy. It never builds. There is no `push` trigger on the production workflow —
production cannot be reached by merging anything.

### 10.4 The one application change M7 requires

§23 of the brief requires proving that each hostname routes to the correct
environment. Container health cannot prove that; a public request that reveals
which release answered can. The design adds a `release` field to the existing
`/api/health` payload, sourced from a `RELEASE_SHA` environment variable set by
the deploy script, defaulting to `"unknown"`:

```json
{"status": "ok", "database": "ok", "release": "d77c57f…"}
```

A commit SHA of a private repository is not a secret, and it is the only value
that makes the routing assertion real: the deploy gate requires
`https://app.arkav.lol/api/health` to report the SHA it just deployed, and
`https://staging.arkav.lol/api/health` to report staging's. This is the sole
application-code change in M7 and it is additive.

---

## 11. Migration policy and rollback semantics

### 11.1 Expand/contract is the default contract

Every release is expected to be backward compatible with the previous release's
code: add columns nullable or defaulted, do not drop or rename in the same
release that stops using them, split destructive changes across two releases.
Automatic image rollback is only meaningful under that discipline.

### 11.2 Declaring an incompatible release

A repository file — `deploy/release-metadata.json` — carries one field:

```json
{ "migrations_backward_compatible": true }
```

CI reads it at build time and copies the value into the release manifest, so the
claim travels with the artifact rather than living in someone's memory. When it
is `false`:

- the staging deploy proceeds normally (staging is where you find out);
- the production workflow requires an extra explicit confirmation input;
- **automatic rollback is disabled for that release**. A failed health gate stops
  and reports `MANUAL RECOVERY REQUIRED`, naming the previous SHA, rather than
  rolling images back onto a schema they cannot read.

This is one boolean and one CI step. It is deliberately not a migration
framework.

### 11.3 No automatic database downgrade, ever

The deploy path never runs `migrate <app> <earlier>`, never restores a dump, and
never claims database rollback exists. §17.1 records the accepted consequence.

---

## 12. Deploy security model

### 12.1 Principals

| Principal | Has | Explicitly does not have |
|---|---|---|
| `deploy` (Unix user) | An SSH key with a forced command; `sudo` for exactly two root scripts | Docker group membership; write access under `/opt/product-intelligence`; read access to any `.env` or the GHCR token; an interactive shell |
| root scripts | Docker socket, `.env` files, the ledger | Nothing from the client except a validated SHA |
| GitHub Actions (staging) | The staging deploy key | Any way to name `production` |
| GitHub Actions (production) | The production deploy key | Any way to build or push a new image at deploy time |

`deploy` is **not** in the `docker` group. Docker group membership is
root-equivalent; granting it would make every other control cosmetic.

### 12.2 Two keys, environment bound to the key

`~deploy/.ssh/authorized_keys`:

```
command="/usr/local/bin/pi-deploy-wrapper staging",restrict,no-pty ssh-ed25519 AAAA…stagingkey
command="/usr/local/bin/pi-deploy-wrapper production",restrict,no-pty ssh-ed25519 AAAA…prodkey
```

The environment is an argument of the **forced command**, written by root in a
file `deploy` cannot modify. The client's command line reaches the wrapper only
as `SSH_ORIGINAL_COMMAND`, and the wrapper never uses it to choose an
environment. A stolen staging key can therefore deploy staging with any SHA it
likes — and cannot address production at all. `restrict` disables port
forwarding, agent forwarding, X11 and PTY allocation.

This answers "what prevents the auto-staging credential from deploying
production" structurally rather than by convention.

### 12.3 Input validation

The wrapper accepts one of two shapes and nothing else:

```
deploy <40-hex-sha>
status
```

Parsing is strict — `[[ "$sha" =~ ^[0-9a-f]{40}$ ]]` — before any other use, and
the SHA is never interpolated into a shell string. No user-supplied text ever
reaches `eval`, a shell `-c`, a compose file, or a docker argument other than as
a validated tag or a ledger key. Anything else exits non-zero with a fixed
message.

### 12.4 sudoers

```
deploy ALL=(root) NOPASSWD: /usr/local/sbin/pi-deploy-staging, /usr/local/sbin/pi-deploy-production
```

Exact paths, no wildcards, no arguments permitted beyond what the scripts
validate themselves. Scripts are `0755 root:root` in a root-owned directory; the
wrapper is likewise root-owned. `deploy` can execute them and cannot edit them.

### 12.5 Secrets

Nothing secret is ever passed as a command argument (visible in `ps`), echoed,
or logged. `.env` files are `0600 root:root`, generated on the server by the
same non-echoing technique `docs/STAGING.md` already establishes. GitHub stores
only the two SSH private keys and the host/user, as repository secrets. The GHCR
read-only token lives only on the server. This design adds **no** secret-manager.

---

## 13. Deploy execution, locking and health gating

### 13.1 Locking

Each environment has its own lock:

```bash
exec 9>/var/lock/pi-deploy-<env>.lock
flock -n 9 || { echo "deploy already in progress for <env>"; exit 75; }
```

`-n` is non-blocking on purpose: a second deploy **fails fast** with a clear
message and a distinct exit code rather than queueing. On a single-core host,
queued deploys pile up behind each other and turn one slow deploy into a stall;
CI can simply be re-run. The locks are independent, so a staging deploy never
blocks a production promotion.

### 13.2 Preflight — before anything mutates

Abort with a clear reason, having changed nothing, if any of these fail:

1. Environment is one of the two known values (from the forced command).
2. SHA matches `^[0-9a-f]{40}$`.
3. `/opt/product-intelligence/<env>/.env` exists, is `0600 root:root`, and
   contains every required key with a non-empty value (names only — never
   values — are reported).
4. Docker daemon responds (`docker info`).
5. `docker compose config` validates and the resolved project name equals the
   expected constant (§6.2).
6. The four external networks and this environment's external volumes exist.
7. Both images exist and pull successfully (staging: by tag; production: by
   digest from the ledger).
8. **Production only:** the ledger records this SHA as a successful staging
   release, and — if `migrations_backward_compatible` is false — the extra
   confirmation was supplied.
9. Free disk on the Docker filesystem is above a floor (a few GiB), since a pull
   that fills the disk is a way to damage unrelated services.
10. **Production only, first deploy:** `app.arkav.lol` resolves to this host and
    HTTPS is being served for it.

### 13.3 Health gate

Bounded, in order, after the candidate is up:

| Check | Bound |
|---|---|
| Containers `running` and `healthy` for this project | ~90s |
| In-container API health (`/api/health` on loopback) | ~30s |
| **Public HTTPS** `https://<host>/api/health` returns 200 **and `release` equals the deployed SHA** | ~60s |
| Login route `https://<host>/login` returns 200 | ~30s |
| A known static asset (`/static/admin/css/base.css`) returns 200 | ~30s |
| The *other* environment's host still answers 200 with *its* own release | ~30s |

Fixed retry counts with a fixed interval and an overall deadline; never an
unbounded loop. The last row is what proves the two hostnames did not cross —
and it is why deploying one environment verifies the other is still standing.

### 13.4 Ledger

`/opt/product-intelligence/state/<env>.json`, `0644 root:root`, written
atomically (temp file + `rename`):

```json
{
  "environment": "staging",
  "current":  { "sha": "…", "api_digest": "sha256:…", "web_digest": "sha256:…",
                "deployed_at": "…", "migrations_backward_compatible": true },
  "previous": { "sha": "…", "api_digest": "sha256:…", "web_digest": "sha256:…" },
  "candidate": null
}
```

`history/` keeps one append-only record per attempt, including failures and
rollbacks. `candidate` is set at the start of a deploy and cleared on success or
rollback, so an interrupted deploy is visible afterwards rather than invisible.

**Proof that a SHA passed staging** is a `staging.json` (or history) entry whose
`sha` matches and whose result is `success`, together with the digests recorded
at that moment. Production reads that entry through the same root-owned tooling;
GitHub never asserts it.

---

## 14. Failure matrix

| # | Failure point | Schema touched? | Automatic action | End state |
|---|---|---|---|---|
| 1 | Preflight (any of §13.2) | No | Abort | Current release untouched and serving |
| 2 | Image pull fails | No | Abort | Current release untouched and serving |
| 3 | Migration one-shot fails | **Partially — possibly** | Abort. Candidate app is **never started** | Previous app still running. Django migrations are per-migration atomic on PostgreSQL, so a failed migration leaves the earlier ones applied; with expand/contract the old code tolerates that. Reported loudly |
| 4 | `collectstatic` fails | No | Abort | Previous app still running; its static files untouched |
| 5 | Container start / health fails, migrations compatible | Yes, applied | Roll images back to `previous` digests, re-run health gate | Previous images on new schema — the expand/contract case |
| 6 | Container start / health fails, migrations **in**compatible | Yes, applied | **No rollback.** Stop, dump `docker compose ps` and last log lines, mark the attempt failed | `MANUAL RECOVERY REQUIRED`, previous SHA named |
| 7 | Rollback itself fails | Yes | Stop. No retry loop | `CRITICAL`: ledger records both failures, diagnostics preserved, exit non-zero. A human decides next |
| 8 | Public health passes but the wrong `release` answers | Yes | Treated as a health failure → row 5 or 6 | Routing error surfaces as a failed deploy, not a silent cross-wire |
| 9 | Lock held | No | Exit 75 immediately | Other deploy continues undisturbed |

Rows 5 and 6 are the whole point of §11.2: **automatic image rollback is safe
only when the candidate's migrations were declared backward compatible.**

---

## 15. Production bootstrap (one-time, manual, approval-gated)

Ordered so that nothing irreversible happens before its prerequisite:

1. **DNS.** `app.arkav.lol` A record → VPS IP, Cloudflare **DNS-only** (grey
   cloud). Verify with `dig +short app.arkav.lol`. No Cloudflare proxy in M7 —
   Caddy terminates TLS directly, and an orange cloud would break HTTP-01.
2. **Directories.** Create `/opt/product-intelligence/production/` and
   `state/`, root-owned.
3. **Networks and volumes.** Create the two production networks and the
   production volumes explicitly by name, so the external declarations resolve:
   `docker volume create product-intelligence-production_pgdata` and
   `…_static`.
4. **Secrets.** Generate a **new** `DJANGO_SECRET_KEY`,
   `CREDENTIAL_ENCRYPTION_KEYS` and `POSTGRES_PASSWORD` straight into
   `/opt/product-intelligence/production/.env` using the non-echoing generator
   pattern from `docs/STAGING.md`. Nothing is printed, pasted, or passed as an
   argument. `chmod 600`. Back up `CREDENTIAL_ENCRYPTION_KEYS` off the server —
   without it every stored credential is unrecoverable, and unlike staging,
   production users cannot simply reconnect on request.
5. **Google OAuth.** Same client ID and secret as staging (a locked decision).
   Add exactly
   `https://app.arkav.lol/api/integrations/oauth/google/callback` as an
   Authorized redirect URI on that client — **added**, not replacing staging's.
   Move the consent screen out of "Testing" if it is still there, or refresh
   tokens expire after 7 days (carried forward from the M7 build-plan note).
6. **Empty database.** First `up` initialises an empty cluster in the new
   volume; the release step runs `migrate` against it. No staging data is copied,
   ever. Verify emptiness: zero users, zero projects, zero connections.
7. **Caddy.** Add the `app.arkav.lol` site block and reload Caddy (not
   recreate). It obtains the certificate on first request now that DNS is valid.
8. **First release.** Promote a SHA that already passed staging, through the
   normal production workflow — the bootstrap does not get a bespoke deploy path.
9. **Smoke test.** §18.

Every step is inspect-then-act, and no step touches n8n, cloudflared, Portainer,
x-ui, or any unrelated database.

---

## 16. Resource limits on a 1 vCPU / ~2 GiB host

Adding production means a second Postgres, a second gunicorn and a second Node
server beside staging and the unrelated workloads. What makes it fit at all is
that **no build ever runs on the server** after M7 — the Next.js build, capped at
640 MB and the most likely thing to be OOM-killed here, moves to CI.

Conservative V1 settings:

| Component | Setting | Reason |
|---|---|---|
| API (both envs) | `GUNICORN_WORKERS=1` | One core. The image default of 3 is wrong for this host in **either** environment; §1.7 shows the current comment assumes production lands elsewhere. Revisit only with evidence |
| API | `GUNICORN_TIMEOUT=60` (unchanged) | Google API calls are the slow path |
| Postgres (both) | modest `shared_buffers` (~128 MB), default `max_connections` | Two clusters on 2 GiB |
| All app services | explicit `mem_limit` | A leak in one environment must not OOM the other, or n8n |
| Web | standalone Node server, no build tooling | Already the case |
| Deploys | serialized per environment by `flock` | Two simultaneous pulls would thrash a small box |

Memory limits are set from measured idle usage during implementation, with
headroom — not guessed here. If the host proves too small, the honest options are
a bigger VPS or scaling staging down between tests; the design does not pretend
otherwise, and does not add swap silently.

---

## 17. Deferred risks

### 17.1 No automated pre-deploy database backup — accepted, documented

The user explicitly decided against it for M7. The consequence, stated plainly:
**if a production migration corrupts or destroys data, there is no restore
point.** Image rollback does not undo schema or data changes (§11.3). Until a
backup exists, production data loss is unrecoverable. This is a conscious
trade, recorded here so it is a decision rather than an oversight, and it is the
first thing to revisit after launch.

### 17.2 Single host

Staging and production share a VPS, a kernel, a Docker daemon and a Caddy. Host
loss takes both. Accepted for V1.

### 17.3 Shared Google OAuth client

A locked decision. Revoking the grant, or the client being suspended, affects
both environments simultaneously — the same combined-authorization blast radius
M6 documented.

### 17.4 HSTS preload spans the parent domain

`SECURE_HSTS_INCLUDE_SUBDOMAINS` + `preload` with a one-year max-age is sent for
`arkav.lol` subdomains. Any future sibling subdomain must be HTTPS-capable
before it is used. Noted, not changed.

### 17.5 Stale static files

Dropping `collectstatic --clear` (§9.2) lets superseded assets accumulate in the
static volume. Bounded and cosmetic; cleaned by an occasional manual step, not
by a window where the live site has no CSS.

---

## 18. V1 production acceptance checklist

Run once, on production, after the first promotion. Phases are ordered so a
failure stops before anything harder to undo.

**A — Platform**

1. `https://app.arkav.lol/api/health` → 200, `release` equals the promoted SHA.
2. Valid certificate; `http://` redirects to `https://`.
3. `https://staging.arkav.lol/api/health` → 200 with **staging's** release —
   staging still works after production launch.
4. Django admin login page renders **with CSS** (`/static/admin/css/base.css` →
   200) on both hosts.

**B — Application**

5. Sign up, sign out, sign in on production.
6. Session and CSRF cookies are `Secure` + `HttpOnly` (session) behind Caddy.
7. Create a project.
8. GA4: OAuth connect → resource selection → **Test connection** → **Change
   property** → **Disconnect** → reconnect.
9. Search Console: the same sequence.

**C — Isolation and hygiene**

10. Production database starts empty: a fresh production user cannot see any
    staging project, and vice versa.
11. Production and staging use different Postgres volumes, different databases
    and different credentials (verified by name, never by printing values).
12. Access-log scan for `ya29.`, `1//`, `client_secret`, `"access_token"`,
    `"refresh_token"`, and OAuth `code=`/`state=` — **counts only**, expected
    zero.
13. Stored credentials are Fernet-encrypted at rest.

**D — Deployment mechanics**

14. Staging auto-deploy proof: a merge to `main` deploys staging with no manual
    step, and the ledger records success.
15. Production promotion proof: `workflow_dispatch` with that SHA deploys the
    **same digests**, verified against the staging ledger entry.
16. Production refuses a SHA that never passed staging (expected failure).
17. The staging deploy key cannot deploy production (expected failure).
18. Rollback proof, if practical safely: promote a candidate whose health gate
    is made to fail by a controlled, reversible means (for example an
    intentionally wrong `RELEASE_SHA` for the gate, or a candidate that fails the
    login-route check) and observe automatic image rollback to the previous
    digests followed by a passing health gate. **Never** by shipping a
    deliberately broken migration.
19. Unrelated services untouched: n8n, cloudflared, Portainer and x-ui show
    uptimes predating the deployment window.

No Google grant revocation is required for M7 acceptance.

---

## 19. Rollback and recovery procedures

| Situation | Procedure |
|---|---|
| Bad production release, migrations compatible | Automatic (§14 row 5). Manual equivalent: promote the previous SHA — its digests are in the ledger |
| Bad production release, migrations incompatible | No automatic rollback. Fix forward with a new release, or restore manually — accepting §17.1 |
| Caddy misconfigured | Revert the Caddyfile from the previous config bundle and reload. Certificates are in the adopted volumes and survive |
| Staging relocation went wrong | Old `/opt/product-intelligence-staging` still exists and is stopped, with the same external volumes; start it there again |
| Config bundle broke a deploy | Install the previous bundle by SHA and redeploy |
| Total loss of `CREDENTIAL_ENCRYPTION_KEYS` | Unrecoverable stored credentials; users must reconnect. This is why the key is backed up off-server (§15.4) |

---

## 20. Files expected to change during implementation

Nothing below is being changed now.

**New**

- `.github/workflows/ci.yml`, `deploy-staging.yml`, `deploy-production.yml`
- `compose.production.yaml`; `deploy/caddy/compose.yaml`
- `deploy/scripts/pi-deploy-wrapper`, `pi-deploy-staging`, `pi-deploy-production`,
  and a shared library implementing preflight, lock, health gate, ledger,
  rollback
- `deploy/release-metadata.json`
- `.env.production.example`
- `docs/DEPLOY.md` — bootstrap, runbooks, the legacy volume names, acceptance

**Modified**

- `compose.staging.yaml` — external volumes and networks by exact name, edge
  aliases, no `build:`, no published ports, Caddy service removed
- `docker/api/entrypoint.sh` — argument passthrough; migrations and
  `collectstatic` out of startup
- `apps/api/Dockerfile` — `CMD ["serve"]`
- `docker/caddy/Caddyfile` — two site blocks, two static roots, alias targets
- `apps/api/common/views.py` — `release` field in the health payload (§10.4)
- `.gitignore` — `!.env.production.example`
- `docs/STAGING.md` — superseded operationally; points at `docs/DEPLOY.md`
- `docs/V1_BUILD_PLAN.md` — M7 section reconciled: no `scripts/backup.sh`
  (§17.1), no `compose.yaml`, CI-built artifacts instead of server builds

---

## 21. The review questions, answered directly

1. **Staging moved without losing Postgres data or OAuth credentials?** The
   Compose project name comes from the `name:` key, not the directory (§1.1), so
   the move does not rename anything; volumes are additionally pinned
   `external: true` by exact name (§6.3), making an empty database a hard error
   rather than a silent success; and the move is bracketed by row-count and
   decrypt-a-credential checks (§6.4).
2. **Which volumes are preserved?** All four:
   `product-intelligence-staging_{pgdata_staging,static,caddy_data,caddy_config}`
   (§6.5). None is created, renamed, copied or removed.
3. **Caddy moved without losing the certificate or a long outage?** The new
   shared project adopts the existing `caddy_data`/`caddy_config` volumes as
   external under their legacy names; cutover is `stop caddy` in the old project
   then `up -d` in the new one, seconds apart, after the config is validated
   offline (§7).
4. **How does shared Caddy reach the right static volume?** Two read-only mounts
   at `/srv/static/staging` and `/srv/static/production`; each site block roots
   its own `handle_path /static/*` (§7).
5. **How does Caddy distinguish the two APIs on two edge networks?** Explicit
   per-network aliases `staging-api` / `production-api` (and `-web`), each
   resolvable on one edge network only; the Caddyfile never says plain `api`
   (§5.2).
6. **Deploy without a Git checkout?** Compose files, Caddyfile and scripts are
   installed as a SHA-named config bundle under `/opt/product-intelligence/`; a
   release is images-by-digest plus that bundle. No `git` on the deploy path
   (D12, §4).
7. **How is config installed and updated then?** The bundle is produced by CI
   from the repository and delivered over the same restricted SSH path, unpacked
   into `config-bundles/<sha>/` and installed by the root script. Previous
   bundles remain for rollback (§19).
8. **How is a build identified and later promoted?** By full commit SHA as tag,
   and by **image digest** as identity. Production pulls the digests recorded in
   the staging ledger for that SHA and never rebuilds (§8.2).
9. **How do we prove a SHA passed staging?** A root-owned ledger entry written
   only by the staging deploy script after its health gate passed, carrying the
   SHA and the digests (§13.4). The server is the authority; the workflow cannot
   assert it.
10. **What stops the staging credential deploying production?** The environment
    is baked into the SSH forced command in a root-owned `authorized_keys`, not
    taken from the client. The staging key has no syntax that names production
    (§12.2).
11. **What can the `deploy` user do?** Execute exactly two root scripts via
    NOPASSWD sudo, through a forced command. No Docker group, no writes under
    `/opt/product-intelligence`, no `.env` or GHCR-token access, no interactive
    shell (§12.1, §12.4).
12. **Where is the GHCR credential?** `/etc/product-intelligence/ghcr.env`,
    `0600 root:root`, `read:packages` only, readable by root — therefore by the
    root deploy scripts and not by `deploy` (§8.3).
13. **What prevents command injection over SSH?** A forced command; a strict
    grammar of `deploy <40-hex>` or `status`; regex validation before use; no
    `eval`, no shell interpolation of client input (§12.3).
14. **How are concurrent deploys prevented?** Per-environment `flock -n`; a
    second deploy exits 75 immediately with a clear message (§13.1).
15. **Image pull fails?** Preflight aborts before anything mutates; the current
    release keeps serving (§14 row 2).
16. **Migration fails?** The candidate app is never started; the previous app
    keeps running; the failure is reported (§14 row 3).
17. **Startup/health fails after migration?** Automatic image rollback to the
    previous digests, then the health gate again — if the release declared
    backward-compatible migrations (§14 row 5).
18. **When is image rollback safe?** Only under expand/contract, asserted per
    release by `migrations_backward_compatible`. When false, rollback is refused
    and manual recovery is demanded (§11.2, §14 row 6).
19. **If rollback fails?** Stop. No retry loop. Preserve `ps` output and recent
    logs, write both failures to the ledger, exit non-zero, report CRITICAL
    (§14 row 7).
20. **How are unrelated services protected?** Every command names a Compose
    project or a single service. No `docker system prune`, no bare `docker
    stop`, no global network or volume cleanup, no `compose down` outside the
    two application projects, no restart of anything not owned by this project
    (§29 of the brief; enforced in §6, §7, §13).
21. **Does production fit the host?** Yes, on the strength of no server-side
    builds, `GUNICORN_WORKERS=1` in both environments, modest Postgres buffers
    and explicit memory limits — with the honest caveat in §16 that this is the
    binding constraint and evidence, not optimism, should settle it.
22. **What is out of M7?** §0: the whole observability/orchestration list,
    automated backups, blue/green, server builds, secret platforms, and any
    product feature.

---

## 22. Self-review

Each risk the brief named, checked against the design as written.

| Risk | Finding |
|---|---|
| Data loss on relocation | **Addressed.** External-by-exact-name volumes turn a name drift into a startup error, not an empty database (§6.3); counts verified before and after |
| Compose project-name change | **Addressed.** `name:` retained verbatim; `-p` never used; `COMPOSE_PROJECT_NAME` unset and the resolved name asserted (§6.2) |
| Docker volume renaming | **Avoided entirely.** Legacy names kept, including the awkward `_pgdata_staging`, and documented as intentional (D3) |
| Caddy certificate loss | **Addressed.** Volumes adopted, not recreated; config validated offline; cutover is stop-then-start on the same volumes (§7) |
| Staging/production crossover | **Addressed.** Separate edge and internal networks, distinct aliases, and a health gate that fails the deploy if a hostname reports the wrong release (§5, §13.3 row 3 and row 6) |
| Static volume collision | **Addressed.** Two volumes, two mount points, two roots (§7) |
| Arbitrary sudo or Docker access | **Addressed.** No Docker group; two exact sudo paths; forced commands; strict input grammar (§12) |
| Staging credential deploying production | **Addressed.** Environment bound to the key, not to client input (§12.2) |
| Mutable tag assumptions | **Addressed.** Digests are identity; production pulls the digests staging validated (§8.2) |
| Automatic DB rollback assumed | **Rejected explicitly.** Images only; incompatible releases refuse rollback (§11, §14) |
| Migrations hidden in normal startup | **Fixed at the root cause.** `serve` does nothing but serve; the entrypoint's silent argument-swallowing (§1.4) is repaired (§9.1) |
| Accidental server builds | **Addressed.** No `build:` in either deployment manifest; CI builds and pushes; PRs build without pushing (§8.1, §10.1) |
| Secret exposure | **Addressed.** No secret in an image, a build arg, a command argument, a log line or the repository; `.env` `0600 root:root`; GHCR token root-only; the only new payload field is a commit SHA (§8.3, §10.4, §12.5) |
| Commands affecting unrelated workloads | **Addressed.** Project- and service-scoped commands only; the prohibited list is explicit (§21 answer 20) |
| Unnecessary infrastructure | **Addressed.** No new service or database: the ledger is JSON files, the lock is `flock`, the migration policy is one boolean |

**Contradictions found and resolved while writing this document**

1. *Aliases versus `DJANGO_ALLOWED_HOSTS`.* An early shape had SSR call
   `staging-api:8000`, which would have required `ALLOWED_HOSTS` to gain the
   alias in both environments — the exact `400 DisallowedHost` failure
   `.env.staging.example` warns about. Resolved by keeping SSR on the internal
   network and putting aliases only on the edge (§5.3), leaving the existing
   environment values untouched.
2. *`collectstatic --clear` in a one-shot.* Carrying `--clear` over would empty
   the live static volume before repopulating it, briefly serving the running
   site without admin CSS. Resolved by dropping `--clear` and accepting bounded
   staleness (§9.2, §17.5).
3. *"Verify the host routes to the right environment" versus "no application
   changes".* Container health cannot prove routing. Resolved by adding one
   non-secret `release` field to the health payload and stating it as the single
   application change M7 makes (§10.4).
4. *Migrate-then-recreate leaves old code on new schema.* Acknowledged rather
   than hidden: it is safe exactly under expand/contract, which is why §11.2
   exists and why row 5 of the failure matrix is conditional.
5. *Published loopback ports.* Keeping them would have forced a second port pair
   for production and kept an unnecessary host surface. Resolved by removing
   them (D11) and recording that `docs/STAGING.md`'s host-proxy alternative is
   superseded (§5.4).
6. *Build-plan drift.* `docs/V1_BUILD_PLAN.md` still promises `compose.yaml`,
   `scripts/deploy.sh` and `scripts/backup.sh`. The first two are superseded by
   the per-environment manifests and the restricted deploy path; the third
   contradicts the deferred-backup decision. Reconciling that text is listed in
   §20 rather than left to be discovered.

**Residual risks, stated rather than solved:** §17.1 (no backup — the largest),
§17.2 (single host), §17.3 (shared OAuth client), and §16 (host capacity, to be
settled by measurement during implementation).

---

## 23. Open questions for the user

None block writing the implementation plan. Two want a decision before
implementation begins:

1. **GHCR token type.** A classic PAT with `read:packages` is simplest and is
   what §8.3 assumes; a fine-grained token scoped to the package is tighter but
   needs the package to exist first, which is a chicken-and-egg on the first
   push. Recommendation: classic `read:packages` for V1, noted for revisit.
2. **Rollback proof in acceptance (item 18).** It needs a *safe* induced
   failure. Recommendation: fail the login-route check on a candidate rather
   than anything involving migrations. Confirm this is acceptable, or mark the
   item NOT EXECUTED.
