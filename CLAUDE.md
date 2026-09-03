# CLAUDE.md — permanent rules for this repository

Read `docs/V1_PRODUCT_SPEC.md`, `docs/V1_TECHNICAL_DESIGN.md`, and
`docs/V1_BUILD_PLAN.md` before changing anything. They are the source of truth.

## Scope

- **V1 is Projects + Integration Management only.** Do not expand it.
- No AI, LLM, analytics, statistics, anomaly detection, causal inference,
  recommendations, findings, or dashboards. Not now, not as placeholders.
- No data warehouse, DuckDB, Parquet, ClickHouse, Kafka, or analytics ingestion.
  V1 stores connection configuration, never GA4/GSC historical data.
- No Rust, no crawler, no Playwright, no browser service, no session replay.
- No providers beyond Google Analytics 4 and Google Search Console.
- Do not create speculative models, services, or packages for future features.

## Locked stack — do not change silently

Next.js + React + TypeScript + shadcn/ui + Tailwind · Python + Django + DRF ·
PostgreSQL · monorepo · Docker Compose on a self-hosted Linux VPS behind Caddy.

Do not introduce NestJS, FastAPI, Rails, Laravel, Go, Rust, Kubernetes, or a
managed PaaS requirement. Do not add a queue, broker, cache, second database, or
new language without a demonstrated V1 requirement — say what the requirement is
and get approval first.

## Architecture

- Django is the control plane: users, auth, workspaces, memberships, projects,
  tenant authorization, integrations, OAuth, credentials, connection state and
  health, audit, and the UI-facing REST API.
- Next.js is the frontend only. It holds no tokens and no business logic.
- Provider-specific Google logic stays behind `integrations/providers/`. The
  `projects` app must not import Google code.
- Follow the existing module boundaries. Inspect what exists before adding a
  dependency or a new module.
- Python dependencies are installed from `apps/api/requirements*.lock.txt`.
  After changing a direct dependency, run `./scripts/lock-python-deps.sh` and
  commit the regenerated locks with the change.
- Prefer boring and maintainable. YAGNI. Avoid premature abstraction.

## Security — non-negotiable

- **Tenant isolation is mandatory.** Every tenant queryset derives from
  `request.user`'s memberships, never from a client-supplied workspace id.
  Cross-tenant access returns 404. Use the shared scoped viewset.
- OAuth credentials are secrets: server-side only, encrypted at rest with
  `MultiFernet`, never in an API response, serializer, admin display, or the
  browser.
- **Never log tokens, codes, client secrets, or OAuth state.**
- **Never commit secrets.** `.env` is ignored; `.env.example` holds names and
  placeholders only. Never generate a real production secret into the repo.
- OAuth `state` is single-use, hashed at rest, expiring, and bound to
  user + project + provider. Always validate it, and re-check membership at
  callback time — authorized at the start of a flow is not authorized at the
  end.
- Never infer that a requested scope was granted. Verify the required scope
  against what the token response actually granted.
- Audit metadata goes through the allowlist in `audit.services`; never widen it
  to carry credential material. Workspace is derived from project there.
- Use minimum read-only Google scopes: `analytics.readonly`,
  `webmasters.readonly`.
- Never overwrite a stored refresh token with a null or empty value. A token
  response that omits `refresh_token` is normal; keep the existing one. Send
  `prompt=consent` only when a new refresh token is actually needed
  (reauthorization, revoked grant, changed scopes) — never by default.
- A stored token does not mean healthy. `connected` requires a successful call
  against the selected resource.
- The domain is always read from environment configuration. Never hard-code it.
- Verify current official Google OAuth/API documentation before writing or
  changing integration code. Do not rely on remembered scopes or endpoints.

## Tests

Required, and written in the same milestone as the code:

- tenant isolation on every endpoint
- OAuth state validation (missing, expired, replayed, tampered, cross-user)
- integration authorization and permission boundaries
- connect / reconnect / disconnect behavior and state transitions
- credential-leakage regressions (no token in any response, encrypted at rest)

No test hits a live Google API; stub HTTP.

## Production

- Never change the production server without showing the intended change and
  getting explicit approval. Never make DNS changes without approval.
- Inspect the server before touching it. Do not overwrite existing services,
  delete containers or data, or change firewall or reverse-proxy configuration
  blindly. Do not install or upgrade system packages unnecessarily.
- Never print or expose production secrets.
