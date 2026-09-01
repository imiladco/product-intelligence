# V1 Technical Design

Status: proposed, awaiting approval
Last updated: 2026-09-01

Stack is locked: Next.js + React + TypeScript + shadcn/ui + Tailwind (frontend),
Python + Django + DRF (backend), PostgreSQL, monorepo, Docker Compose on a
self-hosted Linux VPS behind a reverse proxy with HTTPS.

External API facts in this document were verified against current Google
documentation on 2026-09-01; sources are listed in §14.

---

## 1. Repository layout

```
/
  apps/
    web/                      # Next.js (App Router, TypeScript)
      app/
      components/
      lib/
      public/
      Dockerfile
    api/                      # Django project
      config/                 # settings, urls, wsgi/asgi
      accounts/               # custom User, auth endpoints
      workspaces/             # Workspace, Membership, tenancy helpers
      projects/               # Project
      integrations/           # connections, credentials, OAuth, health
        providers/
          base.py
          google_ga4.py
          google_search_console.py
        google/               # shared Google OAuth client + token service
      audit/                  # AuditEvent
      common/                 # shared mixins, permissions, errors
      manage.py
      pyproject.toml
      Dockerfile
  docker/
    caddy/Caddyfile
    postgres/                 # backup script only
  docs/
  compose.yaml                # production
  compose.dev.yaml            # local development
  .env.example
  CLAUDE.md
  README.md
```

No shared `packages/` directory. The only cross-boundary contract is the REST
API; a TypeScript types file in `apps/web/lib/api/types.ts` is hand-maintained
and small. Adding a codegen pipeline is not justified at V1 size.

## 2. Django apps and their responsibilities

| App | Owns |
| --- | --- |
| `accounts` | Custom `User` (email as login), signup/login/logout/session endpoints. |
| `workspaces` | `Workspace`, `Membership`, the tenancy query mixin and DRF permission. |
| `projects` | `Project` model + CRUD API, scoped to workspace. |
| `integrations` | `IntegrationConnection`, `IntegrationCredential`, `OAuthAuthorizationRequest`, provider registry, OAuth views, discovery + health services. |
| `audit` | `AuditEvent` and a single `record_event()` helper. |
| `common` | Encrypted field, error taxonomy, base viewset, pagination, throttles. |

`projects` must not import Google-specific code. All provider knowledge lives
behind `integrations/providers/base.py`.

## 3. Authentication

**Choice: Django session authentication over same-origin cookies, with DRF's
`SessionAuthentication` and Django's CSRF protection. No JWT.**

Rationale:

- Production is same-origin (`https://DOMAIN/` → Next.js, `https://DOMAIN/api/`
  → Django), so a cookie set on the apex domain is sent to both. There is no
  cross-origin problem for JWT to solve.
- The session cookie is `HttpOnly`, so browser JavaScript cannot read it —
  strictly better than storing a JWT in `localStorage`.
- Sessions are server-side and revocable: sign-out, forced logout, and account
  disable are immediate. JWT revocation would require a denylist, i.e. a session
  store, i.e. sessions.
- Django admin at `/admin/` uses the same session; no second auth system.
- No refresh-token rotation code to write and get wrong.

Mechanics:

- Custom user model `accounts.User` (`AbstractBaseUser` + `PermissionsMixin`),
  `email` unique and used as `USERNAME_FIELD`. Chosen at project creation
  because swapping the user model later is painful.
- Password hashing: Django default (`PBKDF2` first in the hasher list; Argon2
  added via `argon2-cffi` and placed first — one dependency, meaningfully
  better).
- Endpoints: `POST /api/auth/signup/`, `POST /api/auth/login/`,
  `POST /api/auth/logout/`, `GET /api/auth/me/`.
- Sessions in the database (`django.contrib.sessions.backends.db`). No Redis in
  V1 — there is no demonstrated requirement.
- Cookies in production: `SESSION_COOKIE_SECURE=True`,
  `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`,
  `CSRF_COOKIE_SECURE=True`, `CSRF_COOKIE_SAMESITE="Lax"`,
  `SECURE_SSL_REDIRECT` handled at the proxy, `SECURE_PROXY_SSL_HEADER` set for
  `X-Forwarded-Proto`, HSTS enabled.
- `SameSite=Lax` is required (not `Strict`) because the Google OAuth callback is
  a top-level cross-site GET back into our origin and must carry the session.
- CSRF: Django sets a readable `csrftoken` cookie; the web app sends it as the
  `X-CSRFToken` header on every unsafe request. A small `apiFetch()` wrapper in
  `apps/web/lib/api/client.ts` does this in one place.
- Throttling on login/signup via DRF `ScopedRateThrottle` (anon, per-IP).

Frontend consequence: authenticated data fetching happens in Next.js **server
components / route handlers forwarding the incoming cookie**, or client-side
with `credentials: "include"`. Both work same-origin. Next.js holds no tokens.

## 4. Multi-tenancy and authorization

- `Workspace` is the tenant boundary. Every tenant-owned row reaches a workspace
  in at most two hops: `Project.workspace`, `IntegrationConnection.project`.
- **Every** queryset for tenant data is built from the request user, never from
  a client-supplied workspace id:

  ```python
  Project.objects.filter(workspace__memberships__user=request.user)
  ```

- Implemented once in `common/viewsets.py::WorkspaceScopedModelViewSet`, which
  requires subclasses to define `tenant_filter()`. A viewset that does not is a
  hard error at import time, so the safe path is the default path.
- Cross-tenant access returns **404**, not 403 — no existence disclosure.
- Nested routes resolve the parent by the same scoped queryset:
  `/api/projects/{project_id}/integrations/...` looks up the project through the
  membership filter before anything else runs.
- The OAuth callback re-derives the project from the stored authorization
  request **and** re-checks the current user's membership; a stale or replayed
  state cannot connect an integration into another tenant.
- Tests (§11) assert isolation on every read, write, OAuth, and health endpoint.

Explicitly not built: row-level security policies, schema-per-tenant, tenant
middleware with thread-locals, org hierarchies.

## 5. Domain model

```
User(id, email unique, password, name, is_active, is_staff, date_joined)

Workspace(id, name, slug, created_at)

Membership(id, workspace FK, user FK, role: owner|member, created_at)
  unique(workspace, user)

Project(id, workspace FK, name, website_url, created_at, updated_at, created_by FK)
  unique(workspace, name)
  # website_url stored normalized (scheme + host, lowercased, no trailing path)

IntegrationConnection(
  id, project FK, provider: ga4|search_console,
  status: <see §6>,
  external_resource_id      # immutable Google identifier
  external_resource_label   # display name at selection time
  external_resource_meta    # small JSON: non-sensitive display fields only
  google_account_email      # which account authorized (display + support)
  granted_scopes            # text[]
  last_health_check_at, last_successful_check_at,
  last_error_code, last_error_message,
  connected_by FK(User, null), created_at, updated_at
)
  unique(project, provider)

IntegrationCredential(
  id, connection OneToOne,
  refresh_token_encrypted,      # EncryptedTextField
  access_token_encrypted,       # EncryptedTextField, cached
  access_token_expires_at,
  token_version, created_at, updated_at
)
  # separate table, never serialized, excluded from admin display

OAuthAuthorizationRequest(
  id, state_hash unique,        # sha256 of the state nonce; nonce never stored
  project FK, provider, user FK,
  pkce_verifier_encrypted,
  created_at, expires_at, consumed_at (null)
)

AuditEvent(
  id, workspace FK, project FK(null), actor FK(User, null),
  action, provider(null), target_id(null),
  metadata JSON (non-sensitive), ip, user_agent, created_at
)
```

`IntegrationCredential` is a distinct table rather than columns on the
connection so that "never expose credentials" is enforced structurally: the
connection serializer physically cannot reach token fields, and any query that
returns connections does not load them.

## 6. Connection state machine

States on `IntegrationConnection.status`:

| State | Meaning |
| --- | --- |
| `pending_authorization` | Row created, Google consent in progress. |
| `awaiting_resource_selection` | Tokens held, no property/site chosen yet. |
| `connected` | Resource selected and the most recent health check succeeded. |
| `error` | Resource selected, but the last health check failed for a reason other than authorization. |
| `reauth_required` | Credentials are invalid/revoked; user action required. |
| `disconnected` | User disconnected. Credentials destroyed, selection cleared. Row retained for audit and easy reconnect. |

`NOT_CONNECTED` is not a stored state — it is the absence of a row, rendered by
the frontend from the provider catalog. This keeps the DB honest and avoids
creating rows for things the user never touched.

Transitions:

```
(no row) --connect--> pending_authorization
pending_authorization --callback ok--> awaiting_resource_selection
pending_authorization --denied/invalid state--> (row deleted)
awaiting_resource_selection --select resource + health ok--> connected
awaiting_resource_selection --select resource + health fail--> error
connected --health ok--> connected
connected --health fail (non-auth)--> error
connected|error --auth failure (invalid_grant/401/revoked)--> reauth_required
error --health ok--> connected
reauth_required --reconnect ok--> awaiting_resource_selection | connected
any --disconnect--> disconnected  (credentials deleted, token revoked at Google)
disconnected --connect--> pending_authorization
```

Invariants:

- **Holding a token is never sufficient for `connected`.** `connected` requires
  a successful call against the *selected* resource, recorded in
  `last_successful_check_at`.
- `last_successful_check_at` is never cleared by a failure; the UI shows "last
  worked at" alongside a current error.
- On reconnect, if the previously selected resource is still present in the new
  account's accessible list, the selection is preserved and re-verified;
  otherwise the state drops to `awaiting_resource_selection`.

## 7. Google OAuth architecture

Verified endpoints and parameters (§14):

- Authorization: `https://accounts.google.com/o/oauth2/v2/auth`
- Token: `POST https://oauth2.googleapis.com/token`
- Revocation: `POST https://oauth2.googleapis.com/revoke` with `token=...`

**Scopes — minimum, read-only, one flow per provider:**

| Provider | Scopes |
| --- | --- |
| GA4 | `https://www.googleapis.com/auth/analytics.readonly` |
| Search Console | `https://www.googleapis.com/auth/webmasters.readonly` |

Both flows additionally request `openid email` solely to record which Google
account authorized the connection (shown in the UI, needed for support). We do
**not** use `include_granted_scopes=true`: separate credentials per provider
means disconnecting Search Console cannot weaken GA4, and each consent screen
asks for exactly one product's read access.

Authorization request parameters: `response_type=code`, `client_id`,
`redirect_uri`, `scope`, `state`, `access_type=offline`, `prompt=consent`,
plus PKCE (`code_challenge`, `code_challenge_method=S256`).

`prompt=consent` is deliberate: Google issues a refresh token only on the first
authorization for a client/account/scope combination unless re-consent is
forced. Without it, a user who previously authorized would return with no
refresh token and the integration could never refresh. PKCE is not required for
confidential web clients but costs nothing and hardens the callback.

**State / CSRF:** `state` is 32 bytes from `secrets.token_urlsafe`. Only its
SHA-256 hash is stored, in `OAuthAuthorizationRequest`, with a 10-minute TTL,
bound to project + provider + user. The callback: hashes the returned state,
looks it up, rejects if missing, expired, or already consumed, marks it consumed
atomically (`SELECT ... FOR UPDATE`), and verifies the current session user
matches. Single-use, so a replayed callback fails.

**Redirect URI:** exactly one per environment, built from
`GOOGLE_OAUTH_REDIRECT_URI` (env, never hard-coded), e.g.
`https://DOMAIN/api/integrations/oauth/google/callback/`. One URI for both
providers; the provider is recovered from the stored authorization request, not
from a query parameter, so it cannot be tampered with.

**Callback handling** (`GET /api/integrations/oauth/google/callback/`):

1. `error=access_denied` → delete the pending row, redirect to the Integrations
   page with `?oauth=cancelled`.
2. Any other `error` → record it, redirect with `?oauth=error`.
3. Validate state as above; on failure redirect with `?oauth=invalid_state`.
4. Exchange the code at the token endpoint (server-to-server, client secret from
   env, PKCE verifier from the stored row).
5. Verify the granted `scope` in the response contains the required scope;
   if the user unticked it, set `error` with a clear message.
6. Encrypt and store refresh + access tokens, record the Google account email,
   set `awaiting_resource_selection`, write an `AuditEvent`.
7. Redirect (302) to `/projects/{id}/integrations?connected={provider}`.

The callback is a browser redirect, so it returns redirects rather than JSON.

**Token service** (`integrations/google/tokens.py`) is the only code that reads
credentials. `get_access_token(connection)`:

- returns the cached access token if it expires more than 60s from now;
- otherwise refreshes at the token endpoint using the decrypted refresh token,
  under `select_for_update` on the credential row to avoid concurrent refreshes;
- if Google returns `invalid_grant` → delete the credential, set
  `reauth_required`, raise `ReauthRequired`;
- rotates the stored refresh token if the response contains a new one;
- never logs token values (§9).

**Known refresh-token expiry conditions**, handled as `reauth_required` and
documented in the UI copy: an OAuth consent screen still in "Testing" publishing
status issues refresh tokens that expire in **7 days**; refresh tokens are
revoked by user action, by six months of non-use, and there is a limit of **100
refresh tokens per Google account per client id** (creating the 101st silently
invalidates the oldest). Operational consequence: the production consent screen
must be moved out of Testing before launch. This is a launch checklist item, not
code.

**Disconnect:** revoke the refresh token at Google's revocation endpoint
(best-effort; a failure there does not block), delete the
`IntegrationCredential` row, clear the selected resource, set `disconnected`,
write an `AuditEvent`.

## 8. Provider boundary

The smallest interface that supports two Google providers and does not pretend
to be a connector framework:

```python
class IntegrationProvider(Protocol):
    key: str                 # "ga4" | "search_console"
    display_name: str
    oauth_scopes: list[str]

    def list_resources(self, access_token: str) -> list[RemoteResource]: ...
    def check_resource(self, access_token: str, resource_id: str) -> HealthResult: ...
```

`RemoteResource = (id, label, meta: dict)`. `HealthResult = (ok, error_code,
error_message, checked_at)`. A registry dict maps key → instance. Two
implementations, ~120 lines each. No plugin loader, no entry points, no
abstract base hierarchy, no per-provider database tables.

Google HTTP calls use `google-auth` + `requests` directly against the REST
endpoints rather than the heavyweight discovery-based `google-api-python-client`
— two endpoints per provider, easier to test with `responses`, fewer deps.

### 8.1 GA4 discovery and health

- **Discovery:** `GET https://analyticsadmin.googleapis.com/v1beta/accountSummaries`
  (paginate via `pageToken`). Flatten `accountSummaries[].propertySummaries[]`
  into resources: `id = "properties/{id}"` (stored immutably),
  `label = property displayName`, `meta = {account_display_name, property_type}`.
  Scope `analytics.readonly` is sufficient.
- **Health:** `GET https://analyticsadmin.googleapis.com/v1beta/properties/{id}`.
  Metadata only — no report is run, no analytics data is fetched or stored.
  200 → healthy; 401 → refresh, then `reauth_required`; 403/404 →
  `error` with "no longer accessible to the authorized Google account".

### 8.2 Search Console discovery and health

- **Discovery:** `GET https://www.googleapis.com/webmasters/v3/sites`.
  Resources: `id = siteUrl` (either `https://example.com/` for a URL-prefix
  property or `sc-domain:example.com` for a domain property),
  `label = siteUrl`, `meta = {permission_level}`. Entries with
  `permissionLevel == "siteUnverifiedUser"` are filtered out — they cannot be
  queried later.
- **Health:** `GET https://www.googleapis.com/webmasters/v3/sites/{siteUrl}`
  with `siteUrl` percent-encoded as a single path segment (the `sc-domain:`
  colon and the `https://` slashes must be encoded). No Search Analytics query
  is issued — that would be data ingestion.

Both providers' health checks are the minimum call that proves current access to
the *selected* resource, per the V1 constraint.

## 9. Credential encryption and secret handling

- Library: **`cryptography`** (`Fernet` / `MultiFernet`). Fernet is
  AES-128-CBC with HMAC-SHA256 and a timestamp, from a widely audited, actively
  maintained library. No custom cryptography.
- `django-fernet-fields` and similar are unmaintained; we implement one ~40-line
  `common/fields.py::EncryptedTextField` (a `TextField` subclass overriding
  `get_prep_value` / `from_db_value`) instead of adding an abandoned dependency.
- Keys come from env `CREDENTIAL_ENCRYPTION_KEYS`: a comma-separated list of
  urlsafe-base64 Fernet keys. The **first** key encrypts; all keys decrypt
  (`MultiFernet`), which gives key rotation without downtime plus a management
  command `rotate_credential_keys` to re-encrypt in place. Startup fails loudly
  if the variable is missing or malformed.
- The key is distinct from `DJANGO_SECRET_KEY`; rotating one must not require
  rotating the other.
- **No token value is ever logged.** A logging filter
  (`common/logging.py::RedactSecretsFilter`) redacts `access_token`,
  `refresh_token`, `code`, `client_secret`, `state`, and `Authorization` from
  log records as defence in depth; the primary control is that the token service
  never formats tokens into messages. Google HTTP errors are mapped to our own
  error codes before logging, and response bodies from the token endpoint are
  never logged raw.
- No credential field appears in any serializer, in DRF's browsable API, or in
  Django admin (`IntegrationCredential` is registered read-only with token
  fields excluded, or not registered at all).
- `.env` is git-ignored; `.env.example` carries names and placeholder values
  only. No real secret is ever generated into the repo.
- Django `DEBUG=False` in production, `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`
  from env, so a stack trace can never leak settings to a browser.

## 10. REST API surface (V1, complete)

```
POST   /api/auth/signup/
POST   /api/auth/login/
POST   /api/auth/logout/
GET    /api/auth/me/                          -> user + workspaces

GET    /api/workspaces/
POST   /api/workspaces/

GET    /api/projects/                         ?workspace=<id>
POST   /api/projects/
GET    /api/projects/{id}/
PATCH  /api/projects/{id}/
DELETE /api/projects/{id}/

GET    /api/projects/{id}/integrations/       -> one entry per known provider,
                                                 connected or not
GET    /api/projects/{id}/integrations/{provider}/
POST   /api/projects/{id}/integrations/{provider}/authorize/
                                              -> { authorization_url }
GET    /api/integrations/oauth/google/callback/   (browser redirect target)
GET    /api/projects/{id}/integrations/{provider}/resources/
                                              -> discovered properties/sites
POST   /api/projects/{id}/integrations/{provider}/resource/
                                              -> { resource_id } select + verify
POST   /api/projects/{id}/integrations/{provider}/health-check/
POST   /api/projects/{id}/integrations/{provider}/disconnect/
```

Health checks are user-triggered and run on selection; there is **no background
scheduler, worker, or queue in V1** — no demonstrated requirement, and Celery
would add a broker. Discovery results are fetched live and not persisted (they
are Google's data, and caching them creates a staleness problem for no benefit
at this size).

Errors use a single envelope: `{"error": {"code": "...", "message": "...",
"detail": {...}}}` with a fixed code vocabulary
(`reauth_required`, `resource_inaccessible`, `provider_unavailable`,
`invalid_state`, `scope_not_granted`, `not_found`, `permission_denied`).

## 11. Testing

Framework: `pytest` + `pytest-django` + `responses` (HTTP stubbing). **No live
Google calls in tests.**

Required security tests (these gate V1):

1. **Tenant isolation** — for every project and integration endpoint, a user in
   workspace B receives 404 for workspace A's objects, including nested OAuth,
   resource, and health-check routes.
2. **OAuth state** — missing, unknown, expired, tampered, and replayed `state`
   are all rejected and connect nothing; state is single-use.
3. **Callback binding** — a callback whose authorization request belongs to
   another user/project cannot attach credentials.
4. **Credential non-exposure** — a snapshot test asserts that no API response
   for any integration endpoint contains `refresh_token`, `access_token`, or the
   raw ciphertext; a serializer-field test asserts credential fields are absent.
5. **Encryption at rest** — the raw DB column value differs from the plaintext
   and decrypts back to it; a second key in `MultiFernet` still decrypts.
6. **Log redaction** — a token passed through the logging filter is redacted.
7. **State machine** — each transition in §6, including `invalid_grant` →
   `reauth_required` and disconnect destroying credentials.
8. **Scope enforcement** — a callback whose granted scopes omit the required
   scope does not reach `connected`.
9. **Disconnect/reconnect** — end-to-end with stubbed Google, asserting the
   credential row is gone and revocation was attempted.

Frontend: Vitest + Testing Library for the integration-card state rendering.
No E2E browser suite in V1 (Playwright is explicitly out of scope).

## 12. Deployment architecture

### 12.1 Topology

```
                Internet :80/:443
                       |
                    [ caddy ]           reverse proxy, ACME HTTPS
                    /      \
        /  /_next/*        /api/*  /admin/*  /static/*
           |                        |
        [ web ]                  [ api ]
     Next.js standalone      gunicorn + Django
                                     |
                               [ postgres ]  (named volume)
```

Four services in `compose.yaml`: `caddy`, `web`, `api`, `postgres`. Nothing
else — no Redis, no worker, no broker.

### 12.2 Reverse proxy choice: **Caddy**

Chosen over Nginx because it obtains and renews Let's Encrypt certificates
automatically with no certbot sidecar, no renewal cron, and no separate
challenge volume — on a single self-hosted VPS that removes an entire class of
operational failure. The whole config is roughly:

```
{$APP_DOMAIN} {
    encode zstd gzip
    handle_path /static/* { root * /srv/static; file_server }
    handle /api/*   { reverse_proxy api:8000 }
    handle /admin/* { reverse_proxy api:8000 }
    handle          { reverse_proxy web:3000 }
}
```

Caddy redirects HTTP→HTTPS by default and sets `X-Forwarded-Proto`/`-For`,
which Django consumes via `SECURE_PROXY_SSL_HEADER` and
`USE_X_FORWARDED_HOST`. Nginx remains a reasonable alternative if the target
server already runs it — that is a deployment-time inspection question (§16).

### 12.3 Same-origin routing

One domain, from `APP_DOMAIN`:

- `https://DOMAIN/` → Next.js
- `https://DOMAIN/api/` → Django REST
- `https://DOMAIN/admin/` → Django admin
- `https://DOMAIN/static/` → Django static (admin + DRF assets) via WhiteNoise
  behind Caddy

No `app.` / `api.` split: same-origin means no CORS configuration, no
cross-site cookie problems, and no `SameSite=None`. The domain is never
hard-coded — `APP_DOMAIN` drives the Caddyfile, `ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, and the OAuth redirect URI.

### 12.4 Containers

- `apps/api/Dockerfile`: multi-stage, `python:3.12-slim`, non-root user,
  dependencies installed from a lockfile, `gunicorn` with sync workers
  (`--workers $(2*cpu+1)`), `--access-logfile -`. `collectstatic` runs at build
  time into `/srv/static`, shared with Caddy via a volume.
- `apps/web/Dockerfile`: multi-stage, `node:22-alpine`, `next build` with
  `output: "standalone"`, runtime image copies `.next/standalone` +
  `.next/static` + `public`, runs as non-root on port 3000.
- Healthchecks: `api` → `GET /api/health/` (checks DB connectivity), `web` →
  `GET /` , `postgres` → `pg_isready`. `restart: unless-stopped` on all four.
- `depends_on` with `condition: service_healthy` so the API waits for Postgres.

### 12.5 Migrations and startup

The API container entrypoint runs `python manage.py migrate --noinput` then
starts gunicorn. Single API replica in V1, so there is no concurrent-migration
hazard. Destructive migrations are reviewed by hand; the deploy script takes a
database dump before running migrations (§12.7).

### 12.6 Environment variables

`.env.example` (names final at implementation, values placeholders only):

```
APP_DOMAIN=                       # e.g. app.example.com
APP_URL=                          # https://${APP_DOMAIN}
ACME_EMAIL=

DJANGO_SECRET_KEY=
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=             # ${APP_DOMAIN}
CSRF_TRUSTED_ORIGINS=             # https://${APP_DOMAIN}
DJANGO_LOG_LEVEL=INFO

POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
DATABASE_URL=                     # postgres://user:pass@postgres:5432/db

CREDENTIAL_ENCRYPTION_KEYS=       # comma-separated Fernet keys, newest first

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_OAUTH_REDIRECT_URI=        # https://${APP_DOMAIN}/api/integrations/oauth/google/callback/

NEXT_PUBLIC_APP_URL=              # https://${APP_DOMAIN}
INTERNAL_API_BASE_URL=            # http://api:8000  (server-side fetches only)
```

Secrets live in `.env` on the server, owned by the deploy user, mode `600`, and
are never committed. `NEXT_PUBLIC_*` holds nothing sensitive by construction.

### 12.7 Deploy / update / rollback

Deploy is a short `docs/DEPLOY.md` runbook plus `scripts/deploy.sh`:

1. `git pull` on the server (a tagged commit).
2. `pg_dump` to a timestamped file in the backup volume.
3. `docker compose build` (or pull, if we later add a registry).
4. `docker compose up -d` — Caddy keeps serving during the swap; a brief API
   blip on a single replica is acceptable at this stage.
5. `docker compose exec api python manage.py migrate` runs via entrypoint.
6. Smoke check: `/api/health/`, sign-in, and the Integrations page.

Rollback: check out the previous tag, `docker compose up -d --build`. If a
migration must be undone, restore the pre-deploy dump — forward-only migrations
plus a fresh dump is the honest V1 answer; there is no blue/green here and
pretending otherwise would be over-engineering.

No CI/CD pipeline in V1 beyond, optionally, a GitHub Action running lint +
tests on push.

### 12.8 Backups

- Nightly `pg_dump -Fc` via a host cron calling
  `docker compose exec -T postgres pg_dump`, into `/srv/backups`, 14 daily +
  4 weekly retained by the script.
- **The backup is useless without the encryption key**: `CREDENTIAL_ENCRYPTION_KEYS`
  must be backed up separately, out of band, or every stored credential is
  unrecoverable. This is stated in `docs/DEPLOY.md` in bold.
- Restore procedure documented and required to be tested once before launch.
- Named volumes: `pgdata`, `caddy_data` (certificates), `caddy_config`,
  `static`, `backups`.

### 12.9 Development

`compose.dev.yaml` runs only `postgres` (plus optionally `mailpit` later);
`apps/api` runs `manage.py runserver` and `apps/web` runs `next dev` on the
host, for fast reloads. Local OAuth uses a separate Google OAuth client with
redirect `http://localhost:3000/api/integrations/oauth/google/callback/`, with
Next.js `rewrites()` proxying `/api/*` to `http://localhost:8000` so development
is same-origin too — the same cookie and CSRF path as production, so nothing is
development-only.

## 13. Frontend structure

App Router, TypeScript, Tailwind, shadcn/ui (`button`, `card`, `badge`,
`dialog`, `alert-dialog`, `form`, `input`, `table`, `dropdown-menu`, `skeleton`,
`sonner`, `command`). Route map:

```
app/
  (auth)/login/page.tsx
  (auth)/signup/page.tsx
  (app)/layout.tsx                      # session guard + shell
  (app)/projects/page.tsx
  (app)/projects/[projectId]/page.tsx
  (app)/projects/[projectId]/integrations/page.tsx
lib/api/client.ts                       # fetch wrapper, CSRF header, error envelope
lib/api/types.ts
components/integrations/IntegrationCard.tsx
components/integrations/ResourcePickerDialog.tsx
components/integrations/StatusBadge.tsx
```

Server components fetch via `INTERNAL_API_BASE_URL` forwarding the request
cookie; mutations run in client components against `/api/*` with
`credentials: "include"`. No client-side state library — React state plus
`router.refresh()` is enough for four screens.

## 14. Sources verified 2026-09-01

- GA4 Admin API `accountSummaries.list` — endpoint and `analytics.readonly` scope:
  https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list
- GA4 Admin API `properties.get` — endpoint, `properties/{id}` name format, scopes:
  https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/properties/get
- Search Console `sites.list` — endpoint and `webmasters.readonly` scope:
  https://developers.google.com/webmaster-tools/v1/sites/list
- Search Console `sites.get` — `siteUrl` formats (`https://example.com/`, `sc-domain:example.com`):
  https://developers.google.com/webmaster-tools/v1/sites/get
- OAuth 2.0 web server flow — auth/token/revoke endpoints, `access_type=offline`,
  `prompt`, `state`, refresh-token issuance:
  https://developers.google.com/identity/protocols/oauth2/web-server
- OAuth 2.0 — refresh-token expiry: 7 days while consent screen is in "Testing",
  100 refresh tokens per account per client id, `invalid_grant`:
  https://developers.google.com/identity/protocols/oauth2

These must be re-checked at implementation time for each milestone that touches
them; nothing here is to be taken from memory.
