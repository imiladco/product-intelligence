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

## Milestone 4 — GA4 discovery, selection, verification

**Demo:** real GA4 properties listed, one selected, status becomes *Connected*
with a last-successful-check timestamp from the verifying call itself.

Adds: `google/ga4.py` (`accountSummaries.list`, `properties.get`),
`google/credentials.py` (token refresh), `resources/` and `resource/`
endpoints, state transitions, `ResourcePickerDialog.tsx`, empty-state copy for
zero accessible properties.

Selection verification *is* the connection's initial health check: the 200 that
proves the property is readable stamps the health timestamps, so there is one
verification path rather than two. Changing an existing selection, the
on-demand `health-check/` endpoint and the **Test connection** action moved to
Milestone 6, where a failing check has somewhere to lead. Deliberately no
provider-neutral resource abstraction yet — see Milestone 5.

Tests: discovery pagination, selection persists the immutable `properties/{id}`
and Google's own label rather than the request body's, 200/403/404/401 mapping,
`invalid_grant` → `reauth_required`.

## Milestone 5 — Search Console discovery, selection, health

**Demo:** same as Milestone 4 for Search Console, independently connectable.

Adds: `providers/google_search_console.py` (`sites.list`, `sites.get`),
`siteUrl` path encoding for both `https://…/` and `sc-domain:…` forms,
`siteUnverifiedUser` filtering. Frontend is reused unchanged — if it is not,
the provider boundary is wrong and gets fixed here.

Milestone 4 left the GA4 boundary GA4-specific on purpose, reached through one
explicit provider check. This is where the abstraction was decided, with two
real implementations to generalize from instead of one: a ResourceCatalog with
three methods — normalize, list, verify — one per call site that already
existed. resource_service names no provider.

Two things are genuinely different from GA4 and are handled at the boundary:
the identifier is a URL, so it is percent-encoded whole into one path segment;
and a 200 is not proof of access, because Search Console answers 200 for a site
the account is merely aware of. Permission is checked against an allowlist of
siteOwner, siteFullUser and siteRestrictedUser.

Tests: both siteUrl forms round-trip, unverified sites excluded, health mapping.

## Milestone 6 — Reconnect, disconnect, error handling, audit

**Demo:** revoke access in the Google account settings → **Test connection**
shows *Reauthorization required* → **Reconnect** restores *Connected* with the
prior selection re-verified; **Disconnect** destroys credentials.

Adds: reconnect flow preserving a still-valid selection, disconnect with
credential deletion, the on-demand `health-check` endpoint and **Test
connection** action (moved here from Milestone 4), changing an already selected
resource, the full error taxonomy surfaced in the UI, audit events on
reconnect/disconnect/resource-change.

Tests: every transition in the state machine, disconnect leaves no credential
row, audit rows written with no sensitive metadata.

**Delivered.** Three things differ from the sketch above, each decided in the
M6 design and approved before implementation:

- **Disconnect does not revoke the Google grant.** The sketch said
  "revocation + credential deletion". One consent can cover more than this
  connection, so revoking on the user's behalf could break integrations this
  project never touched. Disconnect deletes what we hold and says so plainly in
  the confirmation, with a link to the user's Google account permissions.
- **`AuditEvent` admin is not part of this milestone.** No new admin surface
  was needed to deliver the lifecycle, and adding one for its own sake would be
  scope nobody asked for.
- **Three M3 behaviours change deliberately**, all in `complete_authorization`
  and all covered by new tests: which audit event a completed authorization
  writes now depends on `previous_status` rather than being
  `INTEGRATION_AUTHORIZED` unconditionally; a callback no longer ends
  unconditionally in `awaiting_resource_selection`, because a still-valid
  selection is preserved by re-verifying it; and authorization no longer treats
  a new `IntegrationConnection` row as proof of first Google consent — when no
  trustworthy stored refresh token can be preserved, it forces
  `prompt=consent`, because the same Google account may already have authorized
  this application through another project and Google may then return no
  refresh token at all.

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
