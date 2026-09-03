# V1 Build Plan

Status: approved. Milestones 1–2 complete; Milestone 3 in progress.
Last updated: 2026-09-02

Vertical slices. Each milestone ends with something demonstrable and its own
tests. No milestone builds infrastructure for a later milestone's benefit.

---

## Milestone 1 — Foundation: auth, workspace, project — **complete**

**Demo:** sign up on `localhost`, land on `/projects`, create a project with a
domain, sign out, sign back in, project still there.

Files and directories created:

```
apps/api/
  pyproject.toml, README.md, manage.py, Dockerfile, .dockerignore
  config/{__init__,settings.py,urls.py,wsgi.py,asgi.py}
  accounts/{__init__,apps,models,managers,serializers,views,urls,admin,migrations/}
  workspaces/{__init__,apps,models,serializers,views,urls,admin,migrations/}
  projects/{__init__,apps,models,serializers,views,urls,admin,migrations/}
  common/{__init__,viewsets.py,permissions.py,errors.py,logging.py,fields.py}
  tests/{conftest.py,factories.py,test_auth.py,test_tenancy.py,test_projects.py}
apps/web/
  package.json, tsconfig.json, next.config.ts, tailwind.config.ts,
  postcss.config.mjs, components.json, Dockerfile, .dockerignore
  app/{layout.tsx,globals.css}
  app/(auth)/login/page.tsx, app/(auth)/signup/page.tsx
  app/(app)/layout.tsx, app/(app)/projects/page.tsx
  app/(app)/projects/[projectId]/page.tsx
  components/ui/*            # shadcn: button card input form label badge dialog table sonner
  components/AppShell.tsx
  lib/api/{client.ts,types.ts}
compose.dev.yaml
.env.example
.gitignore
README.md
```

Includes: custom `User` (decided now, not later), `Workspace`/`Membership`,
`Project`, `WorkspaceScopedModelViewSet`, session auth + CSRF wrapper,
`/api/health/`, Postgres via `compose.dev.yaml`, Next.js `rewrites()` so
development is same-origin.

Tests: signup/login/logout, `me`, tenant isolation on every project endpoint
(404 for foreign workspace), project validation of `website_url`.

Excluded: Docker production images, Caddy, integrations.

## Milestone 2 — Integrations surface (no Google yet) — **complete**

**Demo:** the Integrations page lists GA4 and Search Console as *Not connected*,
rendered from the backend provider catalog.

Adds: `integrations` app with `IntegrationConnection` + status enum + the
provider registry and `IntegrationProvider` protocol (two stub providers,
no network calls); `GET /api/projects/{id}/integrations/` merging the catalog
with stored rows; `audit` app + `record_event()`;
`components/integrations/{IntegrationCard,StatusBadge}.tsx`.

Tests: catalog shape, tenant isolation on the integrations endpoints, status
badge rendering for every state.

## Milestone 3 — Google OAuth — **in progress**

**Demo:** click **Connect** on GA4, complete a real Google consent screen with a
development client, return to the Integrations page showing *Select a property*;
tokens are in the database, encrypted, and absent from every API response.

Adds: `common/fields.py::EncryptedTextField` + `MultiFernet` key loading,
`IntegrationCredential`, `OAuthAuthorizationRequest`, `integrations/google/`
(OAuth client, token service, error mapping), `authorize/` and the callback
view, `RedactSecretsFilter`, disconnect-time revocation helper.

Tests: the full OAuth security set (state missing/expired/replayed/tampered,
cross-user callback, denial, scope not granted, encryption at rest, key
rotation, credential non-exposure, log redaction).

Re-verify Google OAuth docs before writing this milestone.

## Milestone 4 — GA4 discovery, selection, health

**Demo:** real GA4 properties listed, one selected, status becomes *Connected*
with last-successful-check timestamp; **Test connection** works.

Adds: `providers/google_ga4.py` (`accountSummaries.list`, `properties.get`),
`resources/`, `resource/`, `health-check/` endpoints, state transitions,
`ResourcePickerDialog.tsx`, empty-state copy for zero accessible properties.

Tests: discovery pagination, selection persists the immutable `properties/{id}`,
health 200/403/404/401 mapping, `invalid_grant` → `reauth_required`.

## Milestone 5 — Search Console discovery, selection, health

**Demo:** same as Milestone 4 for Search Console, independently connectable.

Adds: `providers/google_search_console.py` (`sites.list`, `sites.get`),
`siteUrl` path encoding for both `https://…/` and `sc-domain:…` forms,
`siteUnverifiedUser` filtering. Frontend is reused unchanged — if it is not,
the provider boundary is wrong and gets fixed here.

Tests: both siteUrl forms round-trip, unverified sites excluded, health mapping.

## Milestone 6 — Reconnect, disconnect, error handling, audit

**Demo:** revoke access in the Google account settings → **Test connection**
shows *Reauthorization required* → **Reconnect** restores *Connected* with the
prior selection re-verified; **Disconnect** destroys credentials.

Adds: reconnect flow preserving a still-valid selection, disconnect with
revocation + credential deletion, the full error taxonomy surfaced in the UI,
audit events on connect/reconnect/disconnect/resource-change, `AuditEvent`
admin.

Tests: every transition in the state machine, disconnect leaves no credential
row, audit rows written with no sensitive metadata.

## Milestone 7 — Production deployment

**Demo:** the whole V1 flow on the real HTTPS domain with the production Google
OAuth client.

Adds: production `apps/api/Dockerfile` (gunicorn, non-root, `collectstatic`) and
`apps/web/Dockerfile` (standalone build), `compose.yaml` (caddy/web/api/postgres
with healthchecks, restart policies, named volumes), `docker/caddy/Caddyfile`,
`scripts/deploy.sh`, `scripts/backup.sh`, `docs/DEPLOY.md`.

Pre-flight, before any server change and requiring approval: inspect the server
for existing services, ports 80/443 usage, existing proxy and containers;
present the required DNS records, public IP, ports, and the production OAuth
redirect URI; move the Google consent screen out of "Testing" (otherwise refresh
tokens expire in 7 days); test a database restore once.

Tests: not unit tests — a written smoke checklist executed against production.

---

## Sequencing notes

- Milestones 3–5 are where the real risk is; each ends with the security tests
  green, not deferred to Milestone 6.
- Google API documentation is re-read at the start of Milestones 3, 4, and 5.
- If Milestone 5 requires changing the provider protocol, that is a signal to
  fix the abstraction, not to special-case Search Console in shared code.
