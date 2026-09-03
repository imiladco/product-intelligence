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

sudo cp .env.staging.example .env
sudo chmod 600 .env
```

Generate the three local secrets straight into the file. Nothing is echoed, so
no value reaches the terminal, the scrollback, or shell history — and none is
passed as a command argument, where `ps` would expose it to other users:

```bash
sudo python3 - <<'GENEOF'
import base64, os, pathlib, re, secrets, urllib.parse

env = pathlib.Path("/opt/product-intelligence-staging/.env")
text = env.read_text()

def put(key, value):
    global text
    pattern = rf"(?m)^{re.escape(key)}=.*$"
    if re.search(pattern, text):
        # A lambda, not a replacement string: a generated value could
        # otherwise be read as a backreference.
        text = re.sub(pattern, lambda _m: f"{key}={value}", text)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"

db_password = secrets.token_urlsafe(36)

put("DJANGO_SECRET_KEY", secrets.token_urlsafe(64))
# A Fernet key is urlsafe-base64 of 32 random bytes, so the standard library
# is enough — nothing needs installing on the host.
put("CREDENTIAL_ENCRYPTION_KEYS", base64.urlsafe_b64encode(os.urandom(32)).decode())
put("POSTGRES_PASSWORD", db_password)
put("GUNICORN_WORKERS", "1")

# DATABASE_URL carries the same password, percent-encoded so a special
# character cannot break the URL.
user = re.search(r"(?m)^POSTGRES_USER=(.*)$", text).group(1).strip()
name = re.search(r"(?m)^POSTGRES_DB=(.*)$", text).group(1).strip()
put("DATABASE_URL",
    f"postgres://{user}:{urllib.parse.quote(db_password, safe='')}@postgres:5432/{name}")

env.write_text(text)
print("Wrote 5 generated values. None printed.")
GENEOF
```

Then add by hand the three values that cannot be generated locally:

```bash
sudo nano .env      # ACME_EMAIL, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
```

Verify without revealing anything — names and booleans only:

```bash
sudo python3 - <<'CHKEOF'
import pathlib, re
text = pathlib.Path("/opt/product-intelligence-staging/.env").read_text()
values = dict(re.findall(r"(?m)^([A-Z_]+)=(.*)$", text))
required = ["APP_DOMAIN", "APP_URL", "ACME_EMAIL", "DJANGO_DEBUG",
            "DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS",
            "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL",
            "CREDENTIAL_ENCRYPTION_KEYS", "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET", "GOOGLE_OAUTH_REDIRECT_URI",
            "GUNICORN_WORKERS"]
missing = [k for k in required if not values.get(k, "").strip()]
print("all required values set:", not missing, missing or "")
print("CHANGEME remaining:", text.count("CHANGEME"))
print("GUNICORN_WORKERS:", values.get("GUNICORN_WORKERS"))
print("DJANGO_DEBUG:", values.get("DJANGO_DEBUG"))
print("ALLOWED_HOSTS:", values.get("DJANGO_ALLOWED_HOSTS"))
print("redirect URI:", values.get("GOOGLE_OAUTH_REDIRECT_URI"))
CHKEOF

ls -l .env                       # -rw------- root root
git check-ignore -v .env         # confirms it is ignored
git status --porcelain | grep -c '[.]env$'   # must print 0
```

Start the stack only once that passes:

```bash
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
- Secrets are generated straight into `.env` and never echoed, so they do not
  reach terminal scrollback, shell history, or `ps` output.
- `GUNICORN_WORKERS=1` on this host. The image still defaults to 3, so a larger
  production host is not constrained by a staging choice.
