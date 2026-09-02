# V1 Product Spec — Projects + Integration Management

Status: approved. Milestones 1–2 complete; Milestone 3 in progress.
Last updated: 2026-09-02

## 1. Purpose

V1 lets a user create a project and securely connect the external tools that
future product-intelligence features will consume.

The primary V1 feature is the **project Integrations dashboard**.

V1 delivers **no** analytics, reasoning, or recommendations. It delivers a
correct, secure, production-deployed connection-management surface.

## 2. Non-goals (explicit)

Not in V1, and not to be designed for in V1: AI/LLM anything, anomaly detection,
statistics, causal inference, recommendations, findings, analytics dashboards,
GA4/GSC historical ingestion, data warehouse, DuckDB, Parquet, ClickHouse, Kafka,
Rust, crawler, Playwright, browser service, session replay, Google Ads, Clarity,
PostHog, Amplitude, CRM/support/revenue integrations, competitor or pricing
intelligence, billing, enterprise RBAC, vector DB, Kubernetes, microservices,
realtime streaming, autonomous changes.

Future extensibility is a design constraint. Speculative infrastructure is not.

## 3. Personas

One persona in V1: a **product/growth practitioner** who owns a website or app,
has Google Analytics 4 and Search Console access, and wants the platform wired
into their data sources. No admin/analyst/viewer differentiation in V1 beyond
the minimal workspace roles in §6.

## 4. Core objects (product language)

| Object | Meaning |
| --- | --- |
| Workspace | The tenant. Owns everything. A user reaches data only through membership. |
| Membership | Links a user to a workspace with a role. |
| Project | A single digital product inside a workspace. Has a name and a website domain. |
| Integration | A project's connection to one external provider (GA4 or Search Console). |
| Selected resource | The specific GA4 property or Search Console site chosen for that project. |
| Connection health | Whether the backend can *right now* reach the selected resource. |

## 5. V1 user flow (acceptance narrative)

1. User signs up / signs in.
2. On first sign-in a workspace is created (user may name it).
3. User creates a project: name + website domain.
4. User opens the project's Integrations page. GA4 and Search Console are listed
   as **Not connected**.
5. User clicks **Connect** on Google Analytics 4 → Google consent screen →
   grants read-only access → returns to the Integrations page.
6. The app lists GA4 properties the authorized Google account can access.
7. User selects the correct property.
8. The app runs a health check and shows **Connected**, the property name/ID,
   and the last successful check time.
9. User repeats 5–8 for Search Console (separate authorization, separate scope).
10. User can run **Test connection** on demand at any time.
11. If Google access is revoked or the refresh token is invalid, the integration
    shows **Reauthorization required** with a **Reconnect** action.
12. If a call fails for another reason, the integration shows **Error** with a
    human-readable message and the last time it *did* work.
13. User can **Disconnect**; credentials are destroyed and the selection cleared.
14. User signs out and back in; projects, integrations, and selections persist.

## 6. Roles (minimal)

Two roles, only because ownership needs a floor:

- `owner` — created with the workspace; can do everything, including managing
  members later.
- `member` — full read/write on projects and integrations in V1.

No per-project permissions, no custom roles, no invitations UI in V1 (members
can only be added via Django admin in V1). This is deliberately the smallest
model that still expresses a tenant boundary.

## 7. Screens

### 7.1 Authentication
`/login`, `/signup`. Email + password. Minimal, centered card, shadcn `Card`,
`Input`, `Button`, `Form`. Inline field errors, one general error region.

### 7.2 Projects list — `/projects`
Table or card grid of projects in the current workspace: name, domain,
integration status summary (e.g. two small provider badges). Primary action:
**New project** (dialog: name + website URL).

### 7.3 Project overview — `/projects/[projectId]`
Project name, domain, created date. A compact integrations summary card linking
to the Integrations page. No metrics — there is no data layer in V1.

### 7.4 Integrations — `/projects/[projectId]/integrations` (primary screen)
One card per provider (GA4, Search Console). Each card shows:

- provider name + icon
- **status badge**: Not connected / Connecting / Select a property /
  Connected / Error / Reauthorization required / Disconnected
- authorized Google account email (when connected)
- **selected resource**: display name + immutable external ID
- **last health check** (relative time) and **last successful access**
- error message when relevant, in plain language, never containing tokens
- actions by state:
  - Not connected / Disconnected → **Connect**
  - Awaiting selection → **Select property** (dialog with searchable list)
  - Connected → **Test connection**, **Change property**, **Disconnect**
  - Error → **Test connection**, **Reconnect**, **Disconnect**
  - Reauth required → **Reconnect**, **Disconnect**

Resource picker: shadcn `Dialog` + `Command`/list, radio selection, confirm.
Disconnect: shadcn `AlertDialog` confirming that stored credentials are deleted.

## 8. Error states the UI must handle

| Situation | User-visible behavior |
| --- | --- |
| User denies Google consent | Return to Integrations, neutral notice "Authorization was cancelled". No error status persisted. |
| OAuth `state` invalid/expired | Generic "That authorization link is no longer valid. Please try again." Nothing connected. |
| Google returns a provider error | Status `error`, message summarizing the provider error class. |
| Zero GA4 properties / GSC sites accessible | Empty picker with guidance: authorize a Google account that has access, or grant access in Google. **Reconnect** offered. |
| Selected property later inaccessible (403/404) | Status `error`, last successful access preserved. |
| Refresh token invalid (`invalid_grant`) | Status `reauth_required`, **Reconnect** offered. |
| Network/timeout to Google | Status `error`, transient message; last successful access preserved. |

## 9. V1 success criteria

V1 is done only when, **on the real production HTTPS domain**:

- a new user signs up, gets a workspace, creates a project with a domain;
- connects GA4 with read-only scope, sees real properties, selects one, health
  check passes;
- connects Search Console, sees real sites, selects one, health check passes;
- signs out and back in with all state intact;
- disconnects and reconnects successfully;
- credentials are never present in any browser payload or log;
- tenant-isolation and OAuth-state tests pass in CI-equivalent local runs.

Localhost-only success does not count.
