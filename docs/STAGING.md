# Staging deployment (Milestone 3)

A temporary but real environment whose only purpose is completing the Google
OAuth round-trip on a public HTTPS hostname. It is **not** the production
deployment: Milestone 7 owns that, along with backups, monitoring and CI.

Deliberately minimal — the pieces here exist because the OAuth flow cannot be
verified without them, and nothing else was pulled forward.

## What runs

```
Browser → staging.arkav.lol → reverse proxy → /static/* → shared volume
                                            → /api/*    → api:8000
                                            → /admin/*  → api:8000
                                            → /         → web:3000
                                  private network: api · web · postgres
```

Postgres publishes no port. `api` and `web` publish on **loopback only**
(`127.0.0.1:8001` and `127.0.0.1:3001`), so a proxy on the host can reach them
and the internet cannot.

## Reverse proxy: two supported shapes

**A clean server, nothing on :80/:443** — use the bundled Caddy, which obtains
its own certificate:

```bash
docker compose -f compose.staging.yaml --profile caddy up -d --build
```

**A server that already terminates TLS** — leave the profile off and add a
vhost to the existing proxy pointing at the loopback ports. Do not run a second
proxy on the same ports.

```nginx
# nginx sketch — the X-Forwarded-Proto header is not optional
location /api/    { proxy_pass http://127.0.0.1:8001; include proxy_params; }
location /admin/  { proxy_pass http://127.0.0.1:8001; include proxy_params; }
location /static/ { alias /var/lib/docker/volumes/product-intelligence-staging_static/_data/; }
location /        { proxy_pass http://127.0.0.1:3001; include proxy_params; }
# proxy_set_header X-Forwarded-Proto $scheme;
```

With `DJANGO_DEBUG=false` Django only sets session and CSRF cookies when it
sees `X-Forwarded-Proto: https`. Get that header wrong and **sign-in fails
silently** — the browser is redirected back to the login page with no cookie.
It is the most likely cause of a broken staging deployment.

## First deploy

```bash
sudo mkdir -p /opt/product-intelligence-staging
sudo git clone -b claude/m3-google-oauth \
    https://github.com/imiladco/product-intelligence.git /opt/product-intelligence-staging
cd /opt/product-intelligence-staging

cp .env.staging.example .env
sudo nano .env          # fill in; never paste secrets into a shell command
sudo chmod 600 .env

docker compose -f compose.staging.yaml --env-file .env up -d --build
```

`collectstatic` and `migrate` run from the API entrypoint on every start, so
there is no separate migration step. Staging runs a single API replica, so
there is no concurrent-migration hazard; with more than one, migrations must
move to a one-shot job.

## Health checks, in order

```bash
docker compose -f compose.staging.yaml ps            # all healthy
curl -fsS https://staging.arkav.lol/api/health       # {"status":"ok",...}
curl -sI  https://staging.arkav.lol/                 # 200, valid certificate
curl -sI  http://staging.arkav.lol/                  # redirects to https
```

Then in a browser: sign up, create a project, open Integrations, and confirm
GA4 and Search Console both render as **Not connected** with no certificate
warning. Do not start the OAuth test until all of that passes.

## Updating

```bash
cd /opt/product-intelligence-staging
git fetch origin && git checkout <approved-commit>
docker compose -f compose.staging.yaml --env-file .env up -d --build
docker compose -f compose.staging.yaml ps
```

Pin a reviewed commit rather than tracking a branch, so an update is a
deliberate act.

## Rollback

```bash
git checkout <previous-commit>
docker compose -f compose.staging.yaml --env-file .env up -d --build
```

To remove staging entirely:

```bash
docker compose -f compose.staging.yaml --profile caddy down
docker volume rm product-intelligence-staging_pgdata_staging   # discards data
```

Nothing outside `/opt/product-intelligence-staging` and these named volumes is
touched.

## Backups

None, deliberately. Staging holds throwaway data and a Google OAuth grant that
can be re-granted in a minute. Real backups are Milestone 7.

**One thing does matter:** keep `CREDENTIAL_ENCRYPTION_KEYS` somewhere outside
the server. Without it, stored credentials cannot be decrypted — though for
staging the recovery is simply to reconnect.

## Security notes

- `.env` is `chmod 600`, root-owned, outside Git.
- No secret is in an image, a build argument, or a `NEXT_PUBLIC_*` variable.
  The frontend never sees Google credentials; Django owns the whole exchange.
- Postgres is unreachable from the internet.
- Both containers run as non-root.
- Use a **staging-specific** `DJANGO_SECRET_KEY` and `CREDENTIAL_ENCRYPTION_KEYS`.
