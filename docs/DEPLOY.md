# Deployment (Milestone 7)

The operational document for both environments. `docs/STAGING.md` is the
historical M3 record; operating staging is described here.

Everything below assumes the control plane is installed (§3). Nothing below is
run by CI except where it says so.

---

## 1. Architecture

Two applications, one host, one reverse proxy, three Compose projects.

```
Browser ─ staging.arkav.lol ─┐                        ┌─ staging-api:8000
                             ├─ Caddy (shared) ───────┤  staging-web:3000
Browser ─ app.arkav.lol ─────┘   :80 :443             │  production-api:8000
                                                      └─ production-web:3000
```

| Project | Directory | What it owns |
|---|---|---|
| `product-intelligence-staging` | `/opt/product-intelligence/staging` | staging api · web · postgres |
| `product-intelligence-production` | `/opt/product-intelligence/production` | production api · web · postgres |
| `product-intelligence-shared` | `/opt/product-intelligence/shared/caddy` | Caddy, for both hostnames |

Each application project has two networks — `…-edge` and `…-internal`. Caddy
joins **only the two edge networks**, and reaches each environment by a network
alias (`staging-api`, `production-web`, …). It has no route to either database.
No application port is published to the host; the only listeners on the public
interface are Caddy's `:80` and `:443`.

Caddy is a separate project on purpose: deploying either application never
recreates the proxy, and a Caddy change is its own explicit operation.

Hostnames come from `APP_DOMAIN` / `APP_URL` in each environment's `.env`. They
are never hard-coded in application code.

---

## 2. The legacy volume names, and why they are kept

Four volumes still carry `staging` in their names although two of them now
serve both environments. This is deliberate, not an oversight.

| Volume | Holds | Project that reads it |
|---|---|---|
| `product-intelligence-staging_pgdata_staging` | the staging database | staging |
| `product-intelligence-staging_static` | staging `collectstatic` output | staging, and Caddy read-only |
| `product-intelligence-staging_caddy_data` | **ACME account + certificates, both hostnames** | shared Caddy |
| `product-intelligence-staging_caddy_config` | Caddy autosave config | shared Caddy |
| `product-intelligence-production_pgdata` | the production database | production |
| `product-intelligence-production_static` | production `collectstatic` output | production, and Caddy read-only |

Two rules follow, and both are load-bearing:

**The Compose key is `pgdata_staging`, not `pgdata`.** Renaming the key while
keeping the project name produces a *different* volume, which Docker creates
empty on the spot, with no warning. Postgres then initialises a fresh cluster
and staging comes up looking healthy with zero users, zero projects and zero
integrations. Every volume in both manifests is therefore declared
`external: true` with an explicit `name:`, so a missing volume is a hard start
failure rather than a silent empty database.

**The Caddy volumes keep the `staging` prefix.** They hold a valid Let's
Encrypt account and live certificates for both hostnames. Renaming them would
mean copying certificate state for the sake of a nicer string, and copying
certificate state is how you re-issue against Let's Encrypt rate limits.

Because the volumes are external, the production static volume must exist
before shared Caddy can start at all. It is created empty during the storage
skeleton step, well before production exists. An empty volume is harmless:
nothing serves from it until the `app.arkav.lol` site block is added.

---

## 3. Installing and updating the control plane

The privileged deployment code is what enforces production's refusals and the
environment binding on the SSH keys. **No workflow installs it and no SSH
grammar can reach it.** It is installed by a human with root, from a reviewed
commit.

```bash
# On the server, as root, in an admin working copy at the reviewed commit:
sudo /path/to/repo/deploy/scripts/pi-install-control-plane /path/to/repo \
     --caddyfile /path/to/repo/deploy/caddy/Caddyfile.staging-only
```

`--caddyfile` is required and picks which site blocks are live:

| Caddyfile | Use it |
|---|---|
| `deploy/caddy/Caddyfile.staging-only` | during the handoff, **before `app.arkav.lol` resolves to the VPS** |
| `docker/caddy/Caddyfile` | once the production DNS A record resolves |

Adding the production site block before DNS resolves produces repeated ACME
failures against a hostname that does not exist. Adding it later is a
`caddy reload`, not a recreate, so it does not disturb the running staging site.

### The `deploy` account

Created once, by hand, as root:

```bash
useradd --system --create-home --shell /bin/bash deploy
install -d -m 0700 -o deploy -g deploy /home/deploy/.ssh
install -m 0644 -o root -g root ./authorized_keys /home/deploy/.ssh/authorized_keys
```

**The login shell must be a real shell.** sshd executes a forced command
through the account's login shell, as `shell -c "<command>"`. With
`/usr/sbin/nologin` or `/bin/false` the shell prints its refusal and exits
before `pi-deploy-wrapper` is ever reached, so every deploy fails — and it
fails in a way that looks like a key or network problem rather than a shell
problem.

A normal shell does not weaken anything, because the shell was never the
boundary. Three other things are:

* **The forced command.** Every key carries
  `command="/usr/local/bin/pi-deploy-wrapper <environment>"`. sshd runs that and
  only that. The client's own string is passed as `SSH_ORIGINAL_COMMAND`, which
  the wrapper parses and never executes — so `ssh deploy@host "docker ps"` runs
  the wrapper, not `docker ps`.
* **The key restrictions.** `restrict` denies port forwarding, agent
  forwarding, X11 and PTY allocation; `no-pty` is repeated explicitly so a
  future sshd default cannot quietly re-enable it. There is no interactive
  session to be had with either key.
* **sudo, allow-listed to two exact paths.** `deploy` may run
  `/usr/local/sbin/pi-deploy-{staging,production}` as root and nothing else,
  cannot edit them (they are `root:root`), and is **not** in the `docker` group.

`authorized_keys` is installed `root:root` so `deploy` can read it but never
rewrite its own restrictions. Each key is bound to one environment by the
forced command's argument, which is why a stolen staging key cannot address
production however it is used.

The installer places, all `root:root`:

```
/usr/local/bin/pi-deploy-wrapper                       0755
/usr/local/sbin/pi-deploy-{staging,production}         0755
/usr/local/lib/pi-deploy/*.sh                          0644
/etc/sudoers.d/pi-deploy                               0440  (visudo-validated first)
/opt/product-intelligence/staging/compose.staging.yaml       0644
/opt/product-intelligence/production/compose.production.yaml 0644
/opt/product-intelligence/shared/caddy/{compose.yaml,Caddyfile}
```

and records the commit in `/opt/product-intelligence/state/control-plane.json`.

It deliberately does **not** install three things: `authorized_keys` (assembled
by hand with the real public keys), the `.env` files (generated on the server,
never from the repository), and the ledger (written only by deployments).

**Updating** is the same command at a newer reviewed commit. **Rolling back** is
the same command at the previous reviewed commit; `control-plane.json` records
which one is live. A Caddyfile change additionally needs a reload:

```bash
docker compose -p product-intelligence-shared \
  -f /opt/product-intelligence/shared/caddy/compose.yaml \
  exec caddy caddy reload --config /etc/caddy/Caddyfile
```

**A release whose compose manifest must change is not an ordinary release.** It
needs a control-plane update first, then the release. That friction is the price
of the automatic path being unable to alter its own guardrails.

---

## 4. Secrets

Rules, all non-negotiable:

- **Never printed, never logged, never a command argument** (arguments are
  visible in `ps`). Verification prints names, booleans, counts and lengths.
- `.env` files are `0600 root:root`. They are generated on the server and are
  never copied from the repository. `.env.staging.example` and
  `.env.production.example` carry names and placeholders only.
- GitHub stores only the two SSH private keys, the known-hosts entry, and the
  deploy host/user. No application secret is in GitHub.

Generate values without echoing them — the heredoc reads from the terminal and
nothing lands in shell history:

```bash
umask 077
cat > /opt/product-intelligence/production/.env <<'ENV'
# paste the filled-in file here, then Ctrl-D
ENV
chown root:root /opt/product-intelligence/production/.env
chmod 600 /opt/product-intelligence/production/.env
```

To check a value was set without revealing it:

```bash
awk -F= '/^CREDENTIAL_ENCRYPTION_KEYS=/ {print $1, length($2)}' \
  /opt/product-intelligence/production/.env
```

**`CREDENTIAL_ENCRYPTION_KEYS` must be backed up off-server before the first
production deploy.** Losing it makes every stored OAuth credential permanently
unrecoverable, and there is no database backup in M7 (§8). Users would have to
reconnect every integration.

---

## 5. The GHCR credential

The deploy scripts pull two private images, so the server needs a registry
credential with these properties:

1. It can pull `ghcr.io/imiladco/product-intelligence/api` and `…/web`. This is
   **demonstrated** during bootstrap, not assumed.
2. It has **no package write and no package delete** capability, and no
   repository scope. A read credential on a deploy host cannot become a supply
   chain write.
3. It lives at `/etc/product-intelligence/ghcr.env`, `0600 root:root`, and is
   read only by the root-owned deploy scripts. The `deploy` user cannot read
   it. The schema is exactly two keys:

   ```
   GHCR_USERNAME=<github username or bot account>
   GHCR_TOKEN=<read-only packages token>
   ```

   **Exactly two keys, each exactly once, and nothing else.** Blank lines and
   whole-line `#` comments are allowed; an unknown key, a duplicate, an
   indented assignment or a line without `=` is a hard error. A parser that
   hunted for the keys it wanted and ignored the rest would accept a file with
   a typo'd second token and silently use whichever line it reached first.

   `deploy/scripts/lib/registry.sh` parses that file — it never sources it —
   and authenticates with `--password-stdin`, so the token is never a process
   argument. Both `pi-deploy-staging` and `pi-deploy-production` call
   `pi_registry_login` **before** pulling, on every deploy.

   **Two kinds of state, kept apart:**

   | | Where | Lifetime |
   |---|---|---|
   | **Source of truth** | `/etc/product-intelligence/ghcr.env` | permanent, `0600 root:root` |
   | **Authenticated Docker state** | a fresh `DOCKER_CONFIG` directory, mode `0700` | one deploy |
   | **Default root Docker config** | `/root/.docker/config.json` | **never read, never written** |

   `docker login` persists the credential into whatever Docker config it is
   given. Left at the default it would copy the token into
   `/root/.docker/config.json`, where it outlives the deploy and duplicates the
   secret outside the one file meant to hold it. So each authentication gets
   its own throwaway config directory; the pulls run inside it; and it is
   removed after the second pull, with an `EXIT` trap covering every path that
   does not get there. The lifecycle is explicit:

   ```
   pi_registry_login → docker pull <api@digest> → docker pull <web@digest> → pi_registry_logout
   ```

   A host that has never run `docker login` deploys normally, and a host that
   has is neither consulted nor modified.

   That last point is the whole reason the helper exists. A `docker login`
   typed once by an admin persists in `/root/.docker/config.json`, and pulls
   keep working from it — so the credential file is never exercised, and the
   day the remembered token expires deploys fail with `could not pull` and
   nothing on the host points at the cause. Authenticating from the file every
   time means the credential documented here is the credential actually in use.

   It fails closed: a missing file, an owner other than root, a mode other than
   `0600`, a missing or empty key, or a rejected credential each stop the
   deploy before any pull.
4. It is never committed, never passed as an argument, never echoed.

**Rotation** (manual, and the only supported procedure):

1. Create the new credential with the same two properties.
2. Write `/etc/product-intelligence/ghcr.env` using the non-echoing pattern in
   §4; keep the mode `0600 root:root`.
3. Prove the new credential works *before* revoking the old one, through the
   same code path a deploy uses:

   ```bash
   sudo bash -c 'source /usr/local/lib/pi-deploy/registry.sh && pi_registry_login'
   sudo docker pull "ghcr.io/imiladco/product-intelligence/api@$(sudo python3 -c \
     "import json;print(json.load(open('/opt/product-intelligence/state/production.json'))['current']['api_digest'])")"
   ```

   Verifying with a bare `docker login` instead would only prove that *some*
   credential works, which on a host with a cached login can be the old one —
   and it would write that credential into the root Docker config, which the
   deploy path deliberately never touches.
4. Revoke the old credential.
5. Run one staging deploy and confirm it reaches `success` in the ledger.

---

## 6. Staging: automatic deploys, and reading the ledger

### The default branch must be the branch you merge to

`deploy-staging.yml` uses `workflow_run` and `deploy-production.yml` uses
`workflow_dispatch`. GitHub delivers **neither** to a workflow file that is not
on the repository's **default branch** — and it does so silently: no error, no
annotation, no run. The workflow's runs URL simply reports that it does not
exist.

`push` and `pull_request` have no such requirement, because they run the
workflow file from the ref that triggered them. That asymmetry is the trap: CI
passes on every commit while both deployment workflows are inert, and nothing
in the repository looks wrong.

So the repository's default branch must be the branch these workflows are
merged to. CI enforces this on every run —
`deploy/scripts/check_workflow_registration.py` asks GitHub which workflows it
has registered and which exist on the default branch, and fails naming any that
can never be triggered. It is read-only: two GETs and a per-file existence
check, no mutation.

### The readiness gate

`deploy-staging.yml` is two jobs. **`publish`** runs on every green CI on
`main`: it builds both images, pushes them, and verifies the provenance chain.
**`deploy`** runs only when the repository variable `STAGING_DEPLOY_READY` is
exactly `true`.

Until the staging host is bootstrapped and its control plane installed, leave
the variable unset. Publishing still runs, so a broken Dockerfile or a broken
provenance chain is caught on the commit that introduced it rather than during
bootstrap; the deploy job is skipped, and the run's summary says so explicitly
rather than leaving a reader to infer it from an absent job.

The gate is a **variable, not a secret and not a file in this repository**.
A variable is set in repository settings by a human with admin rights, outside
the merge path — the same principle the control plane follows on the server:
what authorises deployment is not modifiable by the automatic path it
authorises. It is fail-closed by absence; unset, empty, `TRUE` and `yes` all
mean not ready, and there is no default that arms it.

**To arm staging**, once the host exists, the control plane is installed, and
`STAGING_DEPLOY_KEY`, `DEPLOY_KNOWN_HOSTS`, `DEPLOY_USER` and `DEPLOY_HOST`
are configured: set `STAGING_DEPLOY_READY` to `true` in
Settings → Secrets and variables → Actions → Variables. To disarm — during a
maintenance window, or an incident — set it to anything else. The deploy job
verifies the four secrets are present before touching SSH, so arming the gate
without them fails naming what is missing rather than inside `ssh`.

### The deploy itself

Staging deploys itself once armed. `.github/workflows/ci.yml` gates every
change; `deploy-staging.yml` runs only after CI succeeds on `main`, builds both
images once, pushes them tagged with the full commit SHA, verifies the
provenance chain, and then sends one SSH command:

```
deploy <sha> <api-digest> <web-digest>
```

That is the entire client request. The forced command on the deploy key supplies
the environment, so the staging key cannot name production however the workflow
is written. The tag exists for human lookup; **the digests are the deployment
identity** and nothing deploys by tag.

On the server the script takes a lock (`flock -n`; a second deploy exits 75
immediately), runs preflight, pulls both digests, runs migrations as a one-shot
container while the previous release is still serving, runs `collectstatic`,
starts the candidate, and then runs the health gate. Every outcome is recorded.

### Reading the ledger

```bash
sudo cat /opt/product-intelligence/state/staging.json | python3 -m json.tool
sudo tail -5 /opt/product-intelligence/state/history/staging.jsonl
```

`current` is what is serving. `previous` is what an automatic rollback would
return to. `candidate` is populated only while a deploy is in flight — if you
find one there with no deploy running, the last attempt died mid-flight and the
history file says at which stage. `history/<environment>.jsonl` is append-only:
every attempt, successful or not.

The ledger is the **only** evidence that a release exists. Production reads it
to decide whether a SHA passed staging, and the cross-environment health check
treats a sibling with no `current` entry as *not applicable* rather than as a
failure. DNS is never used as evidence of existence.

---

## 7. Production: promotion, and every way it refuses

Production is `workflow_dispatch` only. There is no push trigger, no schedule
and no `workflow_run`: **production cannot be reached by merging anything.** The
GitHub `production` environment gate means a human approves the run in the UI.

The workflow takes one input — a full 40-character SHA that passed staging — and
sends one command:

```
deploy <sha>
```

No digests: the server reads those from the staging ledger entry that passed.
No confirmation input, no force flag, no override — there is nothing for one to
unlock, because every refusal below is a fact about the release rather than a
policy a caller may waive.

It never builds. The artefacts were built once on `main` and validated on
staging; rebuilding would produce different bytes and discard that evidence.

| Refusal | What it means | What to do |
|---|---|---|
| `refused: <sha> has no successful staging release` | The ledger has no successful staging entry for that SHA. Either it never deployed to staging, or it failed there | Deploy it to staging and let it pass |
| `refused: no digests recorded for <sha>` | A staging entry exists but carries no digests — a ledger written by an older or interrupted deploy | Re-deploy the SHA to staging |
| `refused: recorded API digest for <sha> is malformed` / `… web digest …` | The recorded digest is not `sha256:` + 64 hex | Re-deploy to staging; if it recurs, the ledger has been tampered with — stop and investigate |
| `refused: could not pull <image>` | GHCR is unreachable, the credential is expired, or the digest no longer exists | Check `/etc/product-intelligence/ghcr.env` (§5) and that the package was not deleted |
| `refused: <sha> does not declare backward-compatible migrations` `refused: a backward-incompatible migration is a separate, manual maintenance procedure` | The API image's `org.arkav.pi.migrations-backward-compatible` label is not exactly `true` — false, missing, malformed or unreadable all land here | Not a deploy problem. The release needs the separate manual maintenance procedure, planned as its own operation |

The compatibility gate runs **before** the migration, because the previous
application is still serving traffic while migrations run. Refusing afterwards
would be refusing too late.

Exit 75 means the lock was held: another deploy is running, and it was left
undisturbed.

---

## 8. Rollback: images only

An automatic rollback re-points the Compose project at the `previous` digests in
the ledger and re-runs the health gate. It attempts this **once** — there is no
retry loop — and it touches images and nothing else.

**There is no database rollback, and M7 takes no backup.** This was decided
deliberately and is recorded as a deferred risk, not an oversight:

- Image rollback does not undo schema or data changes. A migration that ran is
  still applied after the images go back.
- Rolling back is safe *because* every deployable release declares
  backward-compatible migrations (§7): the previous code tolerates the new
  schema. That is the entire mechanism.
- **If a production migration corrupts or destroys data, there is no restore
  point.** Production data loss is unrecoverable until a backup exists. This is
  the first thing to revisit after launch.

No deploy script runs `pg_dump`, `psql` or a reverse migration. Nothing here
will pretend to protect data it cannot protect.

---

## 9. Failure matrix

| # | Failure point | Schema touched? | Automatic action | End state |
|---|---|---|---|---|
| 0 | **Production**: compatibility label is not exactly `true` | No | **Refuse before pulling or migrating.** No override exists | Current release untouched and serving; reported as ineligible with the reason |
| 1 | Any other preflight check | No | Abort | Current release untouched and serving |
| 2 | Image pull fails | No | Abort | Current release untouched and serving |
| 3 | Migration one-shot fails | **Possibly, partially** | Abort. The candidate app is **never started** | Previous app still running. Migrations are per-migration atomic on PostgreSQL, so earlier ones stay applied; expand/contract means the old code tolerates that. Reported loudly |
| 4 | `collectstatic` fails | No schema; the static volume may be partially updated | Abort | Previous app still running. Filenames are stable, so the site keeps serving; a subset of assets may be from the newer build |
| 5 | Start or health fails, migrations compatible | Yes, applied | Roll images back to `previous`, re-run the health gate | Previous images on the new schema — the expand/contract case |
| 6 | Start or health fails on a release with an unreadable label (**staging only**) | Yes, applied | **No rollback.** Stop; dump `docker compose ps` and the last log lines | `MANUAL RECOVERY REQUIRED`, previous SHA named. Unreachable on production: row 0 refused it first |
| 7 | The rollback itself fails | Yes | Stop. No retry loop | `CRITICAL`: both failures in the ledger, diagnostics preserved, non-zero exit. A human decides next |
| 8 | Public health passes but the wrong `release` answers | Yes | Treated as a health failure → row 5 or 6 | A routing error surfaces as a failed deploy, not a silent cross-wire |
| 9 | Lock held | No | Exit 75 immediately | The other deploy continues undisturbed |

Health-gate failures carry distinct exit codes so the ledger records *which*
check failed: `10` containers, `11` internal health, `12` public health,
`13` login route, `14` static asset, `15` cross-environment.

---

## 10. Runbooks

### Reverse the staging relocation

Restarting the old directory alone is **not** enough once Caddy has moved: the
ports and the pgdata volume are still held.

1. Stop shared Caddy — it holds `:80`/`:443`:
   `docker compose -p product-intelligence-shared -f /opt/product-intelligence/shared/caddy/compose.yaml stop`
2. Stop the relocated stack — it holds the pgdata volume:
   `docker compose -p product-intelligence-staging -f /opt/product-intelligence/staging/compose.staging.yaml stop`
3. Start the original project **with its Caddy profile**, from the old
   directory, so it serves and terminates TLS again.

Certificates survive: they live in the adopted volumes, which neither step
removes.

### Revert a Caddy change

Install the previous reviewed Caddyfile through §3 and reload. Never edit the
live Caddyfile in place — `control-plane.json` would then name a commit that is
not what is running.

### Revert a control-plane change

Re-run `pi-install-control-plane` from the previous reviewed commit.
`/opt/product-intelligence/state/control-plane.json` records
`installed_from_sha`, which is the commit to go back to.

---

## 11. Commands that are never run on this host

The VPS also runs `n8n`, `cloudflared`, Portainer and `x-ui`/Xray. Nothing in
this document addresses them, and neither does any deploy script. Every Docker
command here names a Compose project or an explicit service list.

Forbidden, without exception:

```
docker system prune
docker volume prune
docker network prune
docker stop            # bare, with no explicit container list
docker compose down    # outside the two application projects
```

`docker compose down` with volume removal on a project holding a database or
the ACME state destroys data that has no backup (§8). The old Caddy service is
*stopped*, never `down`-ed.
