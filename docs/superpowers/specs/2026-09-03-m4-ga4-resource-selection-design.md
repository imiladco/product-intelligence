# Milestone 4 — GA4 property discovery, selection, verification

Design draft. 2026-09-03. Not implemented; not approved.

Takes a GA4 connection from `awaiting_resource_selection` to `connected` by
discovering the properties the granted Google account can actually read,
letting the user pick one, verifying that exact property server-side, and
persisting it. Search Console is Milestone 5.

---

## 0. Inspection findings that materially shape this design

| # | Finding | Consequence |
|---|---|---|
| 1 | `IntegrationConnection` already has `external_resource_id`, `external_resource_label`, `external_resource_meta`, `last_health_check_at`, `last_successful_check_at`, `last_error_code`, `last_error_message`, `connected_by`. | **No migration.** M4 is code only. |
| 2 | `integrations/google/oauth.py` exposes only `build_authorization_redirect` and `exchange_code`. There is **no refresh path**. | M4 must build token refresh and persist the rotated access token. This is the largest single piece of new backend work, and it is what makes health checks with no user present possible. |
| 3 | `AuditEvent.Action.INTEGRATION_RESOURCE_SELECTED` exists, and `audit.services.ALLOWED_METADATA_KEYS` already contains `resource_id` and `resource_label`. | Audit needs **no** new action and **no** allowlist widening. |
| 4 | `IntegrationProvider` is a frozen dataclass with `key`, `display_name`, `description`, `oauth_scopes` only — deliberately metadata-only since M2. | M4 is the first real implementation, so the boundary grows here. §3 keeps that growth to one optional field and two methods. |
| 5 | `IntegrationConnectionSerializer.Meta.fields` is an explicit list; `external_resource_meta`, `granted_scopes`, `connected_by` are excluded. | Safe by construction. `external_resource_meta` gains content in M4 and **stays unserialized**. |
| 6 | `requests==2.34.2` is already in `requirements.lock.txt` (transitively, via `requests-oauthlib` ← `google-auth-oauthlib`), and `google-auth==2.57.0` ships `google.auth.transport.requests`. | A direct HTTP client needs **no new package**, only promoting `requests` to a *direct* dependency in `requirements.txt` and re-running `./scripts/lock-python-deps.sh`. |
| 7 | Tests stub HTTP with `responses`, which patches the `requests` transport. | An implementation built on `requests` is stubbable exactly the way the existing OAuth tests already work. A gRPC client would not be. |
| 8 | `complete_authorization` never sets `google_account_email`, and the granted scopes are `analytics.readonly` only — no `openid`/`userinfo.email`. | `google_account_email` **stays empty in M4**. Adding a scope to fill a display field is not justified; the UI must not imply it is populated. §12 handles this honestly. |
| 9 | URL convention is slashless (`APPEND_SLASH = False`), nested under `projects/<int:project_id>/integrations/<str:provider>/…`. | New paths follow it exactly. |
| 10 | `common/errors.py` has a fixed error-code vocabulary and one envelope; `google/errors.py` has an OAuth-only taxonomy. | M4 extends `google/errors.py` with a second base class rather than inventing a parallel scheme (§13). |
| 11 | `apps/web/components/ui/` has `dialog`, `button`, `badge`, `card`, `alert` — but **no `select` or `radio-group`**. | The picker is a `Dialog` + a keyboard-accessible radio list built from existing primitives. No new shadcn component. |
| 12 | `lib/integrations/status.ts` documents `actionLabel` as *authorization* actions only, and `awaiting_resource_selection` currently reads "Choosing a property is not available yet." | Resource selection is a **second, separate action channel** in that module, not an overload of `actionLabel`. |
| 13 | Throttling uses `ScopedRateThrottle` with only an `auth` scope defined. | Discovery fans out to Google on every call and needs its own scope (§16). |
| 14 | `docs/V1_BUILD_PLAN.md` §Milestone 4 lists a `health-check/` endpoint and a **Test connection** action as part of M4. | Included here (§11.3). Flagged in §22 as the one scope item beyond the brief's wording — say the word and it moves to M6. |

### Verified against current Google documentation (2026-09-03)

- `GET https://analyticsadmin.googleapis.com/v1beta/accountSummaries`
  — query `pageSize` (default 50, **max 200**), `pageToken`.
  Response: `accountSummaries[]` with `name`, `account` (`accounts/{id}`),
  `displayName`, `propertySummaries[]` (each with `property` = `properties/{id}`,
  `displayName`, `propertyType`, `parent`), and `nextPageToken` (absent on the
  last page). `analytics.readonly` is sufficient.
- `GET https://analyticsadmin.googleapis.com/v1beta/{name=properties/*}`
  — path is `properties/{property_id}`.

---

## 1. Scope

**In:** GA4 property discovery; user selection; independent server-side
verification of the selected property; persistence of the canonical id and the
label *Google* returned; the credential-refresh path that all of the above
needs; the `awaiting_resource_selection → connected` transition; the
`invalid_grant → reauth_required` transition; a re-verify ("Test connection")
action; the picker UI; tests.

**Out:** Search Console anything (M5). Reconnect and disconnect (M6). Token
revocation (M6). Scheduled/background health checks (not in V1). Any GA4
*reporting* data — M4 reads property **metadata** only and stores no analytics
data, per CLAUDE.md.

---

## 2. User flow

1. Connection is `awaiting_resource_selection`; the card reads **Select a
   property** with a **Choose property** button.
2. Click opens a dialog which fetches `GET …/resources`.
3. The dialog shows properties grouped by account: property display name, the
   `properties/{id}`, and the account name. Zero results shows an empty state
   (§12.4).
4. The user selects one and confirms. `POST …/resource` sends **only**
   `{"resource_id": "properties/123456"}`.
5. The backend refreshes the access token if needed, calls `properties.get` on
   that exact id, and — only on success — persists id + label + meta, sets
   `connected`, and stamps both health timestamps.
6. The dialog closes, the page refreshes, the card reads **Connected** with the
   property name and "Last successful access: just now".
7. A connected card also offers **Change property** (same dialog) and **Test
   connection** (§11.3).

---

## 3. Backend architecture

```
integrations/
  google/
    oauth.py           (unchanged)
    credentials.py     NEW  access-token refresh + persistence
    ga4.py             NEW  GA4 Admin API calls, HTTP boundary
    errors.py          EXTENDED  second taxonomy branch
  providers/
    base.py            EXTENDED  one optional field: resources
    google_ga4.py      EXTENDED  supplies the GA4 catalog
  resource_service.py  NEW  orchestration: discover / select / verify
  views.py             EXTENDED  three views
  serializers.py       EXTENDED  input + output serializers
  urls.py              EXTENDED  three paths
```

Boundaries kept: `projects` imports nothing from `integrations`; nothing outside
`integrations/google/` and `integrations/providers/google_ga4.py` knows the word
"GA4"; `resource_service.py` speaks only in terms of the provider's resource
catalog; views do tenancy and HTTP shape and nothing else.

### The provider boundary extension

`IntegrationProvider` gains exactly one optional field:

```python
@dataclass(frozen=True)
class RemoteResource:
    id: str            # canonical, provider-issued: "properties/123456"
    label: str         # from the provider, never from the browser
    group_label: str   # account name, for grouping in the picker
    meta: dict         # small, non-sensitive; stored in external_resource_meta

class ResourceCatalog(Protocol):
    def list_resources(self, access_token: str) -> list[RemoteResource]: ...
    def verify_resource(self, access_token: str, resource_id: str) -> RemoteResource: ...

@dataclass(frozen=True)
class IntegrationProvider:
    ...
    resources: ResourceCatalog | None = None
```

Two methods, both demanded by M4 itself. `google_search_console.PROVIDER` keeps
`resources=None`, and the endpoints answer **404** for it — the honest answer for
"this provider does not support resource selection yet". M5 fills it in; if M5
cannot be expressed through these two methods, the build plan says fix the
abstraction there, and this shape is small enough to change cheaply.

---

## 4. The Google API boundary — three options

### Option A — thin `requests` client (recommended)

`google/ga4.py` builds two functions over `requests` with an explicit base URL,
`Authorization: Bearer …`, a timeout, and a status-code→our-error mapping.

- **+** No new dependency: `requests` is already in the lock file; it only moves
  from transitive to declared.
- **+** Stubs with `responses`, which every existing OAuth test already uses.
- **+** ~80 lines, fully readable; the exact URL, page size, and timeout are
  visible in the diff and in review.
- **+** Errors are mapped at the boundary, so a Google error string never
  reaches a log or a response (the rule `oauth.py` already follows).
- **−** Pagination and retries are ours to write (about 15 lines).
- **−** No compile-time knowledge of the API surface; correctness rests on the
  verified doc contract above plus tests.

### Option B — `google-api-python-client`

`build("analyticsadmin", "v1beta", credentials=…)`.

- **+** Google-maintained; pagination helpers.
- **−** New dependency tree (`httplib2`, `uritemplate`, `google-api-core`) for
  two GET requests.
- **−** Transport is `httplib2`, **not** `requests` — `responses` cannot stub it,
  so the test approach changes for this milestone alone.
- **−** Fetches or embeds discovery documents; more startup cost and more moving
  parts on a 1-core, 1.9 GiB VPS.
- **−** Errors arrive as `HttpError` whose `str()` includes the response body;
  every call site needs the same redaction care as Option A anyway.

### Option C — `google-analytics-admin` (generated client)

- **+** Typed resource objects, real client library.
- **−** gRPC transport and protobuf types: heaviest install, largest memory
  footprint, and not stubbable with `responses` at all.
- **−** Ties GA4 to a client-library idiom that Search Console (a REST-only API)
  cannot share, so M5 would need a second, different pattern — exactly the
  divergence the build plan warns about.

### Recommendation: **A**

Two GET endpoints, both already documented and verified, against a stack whose
whole point is "boring and maintainable" and whose test suite already stubs
`requests`. B and C both add weight and, worse, split the testing approach
between M3 and M4. Refresh is the one place we do **not** hand-roll: it uses
`google-auth`, which is already a dependency (§5).

---

## 5. Credential refresh

`google/credentials.py`:

```python
EXPIRY_SKEW = timedelta(seconds=60)

def access_token_for(connection) -> str
```

1. Load the `IntegrationCredential`. Missing row, or empty refresh token →
   `CredentialMissing`.
2. If `access_token` is non-empty and `access_token_expires_at` is in the future
   by more than `EXPIRY_SKEW`, return it unchanged.
3. Otherwise build `google.oauth2.credentials.Credentials(token=…,
   refresh_token=…, token_uri=TOKEN_URI, client_id=…, client_secret=…,
   scopes=connection.granted_scopes)` and call `.refresh(google.auth.transport.
   requests.Request())`.
4. On success: persist `access_token` and `access_token_expires_at` (made
   timezone-aware, as `_store_credentials` already does). **The refresh token is
   only overwritten when the response carries a non-empty one** — the standing
   rule, restated in code, not just in review.
5. On `google.auth.exceptions.RefreshError` (this is where `invalid_grant`
   lands: revoked grant, changed password, deleted account): set the connection
   to `reauth_required` with `last_error_code = "credential_refresh_failed"`,
   and raise `CredentialRefreshFailed`. **Never** log the exception text.
6. On transport failure (timeout, connection error): raise
   `ResourceUnavailable`; do **not** change status — a network blip is not a
   revoked grant.

Reused unchanged by M5 and M6.

---

## 6. Discovery

`ga4.list_properties(access_token)`:

- `GET {GA4_ADMIN_BASE}/accountSummaries?pageSize=200`, following
  `nextPageToken` until absent.
- Bounded: at most `MAX_PAGES = 10` (≈ 2000 accounts). If a token remains after
  that, stop and report `truncated: true` rather than looping forever on a
  pathological account.
- Flatten `accountSummaries[].propertySummaries[]` into `RemoteResource`:
  `id = property` (`properties/123`), `label = displayName`,
  `group_label = account displayName`,
  `meta = {"property_type": …, "account": …}`.
- Sort by `(group_label, label, id)` — stable ordering, no reliance on Google's.
- A property summary missing `property` or `displayName` is skipped, not
  crashed on.
- Timeout: 10 s connect+read per request.

Discovery is **not** a health check: it touches no selected resource, so it
writes no health timestamps and never sets `connected`. (A refresh failure
during discovery still sets `reauth_required` — that is §5's doing, and correct
regardless of the caller.)

---

## 7. Selection and verification — the trust rule

The request body carries **one** field:

```json
{ "resource_id": "properties/123456" }
```

- Validated against `^properties/[0-9]{1,32}$` before anything else. This is
  both an input check and a path-injection guard: the value is interpolated into
  a Google URL, so `../`, a full URL, or a query string must never survive.
- Any other key in the body is ignored. A submitted `label`, `status`,
  `external_resource_label`, or `meta` has **no effect whatsoever** — the
  serializer does not declare those fields, so they cannot be read.
- The backend then calls `GET {base}/{resource_id}` itself.
  - **200** → the property exists *and this credential can read it*. The stored
    label is `response["displayName"]`. The browser's idea of the label is never
    consulted.
  - **403 / 404** → `ResourceNotAccessible`, one code for both. Deliberate:
    distinguishing them would tell a user whether a GA4 property they cannot
    access exists — the same oracle argument that makes `InvalidState` a single
    class in the OAuth taxonomy.
  - **401** → treated as a credential problem: one refresh-and-retry at most,
    then `CredentialRefreshFailed` → `reauth_required`.
  - **429 / 5xx / timeout** → `ResourceUnavailable`. State untouched.
- Verification does **not** consult the discovery list. The list is a
  convenience for the human; the authority is this call. A property that was not
  in the list but that `properties.get` returns 200 for is legitimately
  accessible and is accepted.

So: **no sequence of crafted requests can produce `connected` without a live 200
from Google for that exact property under that connection's own credential.**

---

## 8. Persistence

On a verified selection, inside one `transaction.atomic()` holding
`select_for_update()` on the connection row:

```
external_resource_id     = resource.id            # "properties/123456"
external_resource_label  = resource.label         # from Google
external_resource_meta   = {"property_type": …, "account": …,
                            "account_display_name": …, "verified_at": iso8601}
status                   = connected
last_health_check_at     = now
last_successful_check_at = now
last_error_code          = ""
last_error_message       = ""
connected_by             = request.user
```

Single `save(update_fields=[…])`; nothing is written on any failure path, so a
rejected selection cannot half-apply. `external_resource_meta` holds only
provider-issued metadata — never a token, never a scope, never anything the
audit allowlist would refuse.

---

## 9. State transitions

| From | Event | To | Notes |
|---|---|---|---|
| `awaiting_resource_selection` | verification 200 | `connected` | `INTEGRATION_RESOURCE_SELECTED` + `INTEGRATION_CONNECTED` |
| `connected` | verification 200 (different property) | `connected` | `INTEGRATION_RESOURCE_SELECTED`; previous selection replaced atomically |
| `connected` | verification 200 (same property) | `connected` | Idempotent; timestamps refreshed; one audit row |
| `awaiting_resource_selection` / `connected` | 403/404 on the chosen property | *unchanged* | 400 with `resource_not_accessible`. Picking the wrong thing is a user input error, not a broken connection — and a connected project must not be knocked out of service by a mistyped attempt |
| any | refresh fails (`invalid_grant`) | `reauth_required` | Sets `last_error_code`; existing M3 restart path applies |
| any | Google 5xx / timeout | *unchanged* | 503; transient, so no durable state written |
| `not_connected` / `pending_authorization` / `error` / `reauth_required` / `disconnected` | selection attempted | *unchanged* | **409** `conflict`: no usable credential to verify with |

Nothing here moves a connection into `error`. `error` remains what M3 made it:
authorization failed. `disconnected` is still M6's to write.

---

## 10. Health semantics

`connected` means: a token refresh succeeded if one was needed, **and** a
request against the *selected* resource returned 200 — not merely that a token
is stored.

- `last_successful_check_at` is set only on a 200 against the selected resource,
  and is never cleared (the card shows "last worked at" beside an error).
- `last_health_check_at` is set on every completed check, pass or fail.
- Discovery never touches either field.

---

## 11. API contracts

All slashless; all session-authenticated; all tenant-scoped through
`get_project_for_user`.

### 11.1 `GET /api/projects/{project_id}/integrations/{provider}/resources`

```json
{
  "resources": [
    { "id": "properties/123456", "label": "example.com — GA4",
      "group_label": "Example Ltd", "property_type": "PROPERTY_TYPE_ORDINARY" }
  ],
  "truncated": false
}
```

`404` unknown project / no membership / unknown provider / provider without a
resource catalog. `409` connection missing or holding no credential. `503`
`resource_unavailable`. `502`→ not used; Google failures are 503 with a retry
message. Throttle scope `integrations`.

### 11.2 `POST /api/projects/{project_id}/integrations/{provider}/resource`

Request: `{"resource_id": "properties/123456"}`.
Response `200`: the full `IntegrationEntry` for that provider — the same shape
the page already renders, so the client re-renders from one authoritative
payload instead of patching local state.
Errors: `400 validation_error` (`invalid_resource_id`,
`resource_not_accessible`), `409 conflict`, `503`, `404` as above.

### 11.3 `POST /api/projects/{project_id}/integrations/{provider}/health-check`

Re-verifies the **stored** selection (reads nothing from the body) and returns
the updated `IntegrationEntry`. `409` when nothing is selected. POST because it
writes timestamps and can change status. In the build plan for M4 as **Test
connection**; see §22.

---

## 12. Frontend

### 12.1 `lib/integrations/status.ts`

Add a second, clearly separate channel — `resourceAction: "select" | "change" |
null` — leaving `actionLabel` as the authorization channel its docstring
promises. `awaiting_resource_selection` → `"select"`, note removed;
`connected` → `"change"`. Every other state stays `null`.

### 12.2 `components/integrations/resource-picker-dialog.tsx` (new, client)

`Dialog` + a keyboard-accessible radio list grouped by account, built from
existing primitives (no new shadcn component). Fetches on open, not on mount.
States: loading skeleton, list, empty, error with retry. Confirm posts, then
`router.refresh()`. The selected id is sent as the opaque string the API gave
us; the component never constructs a `properties/…` value itself.

### 12.3 `integration-card.tsx`

Renders the picker trigger when `resourceAction` is non-null. The existing
"Selected property" row already reads `external_resource_label` and needs no
change.

### 12.4 Empty state

"No Google Analytics properties are available to this Google account. Ask an
administrator for at least Viewer access to a GA4 property, or connect a
different Google account." — with a re-authorize link to the existing flow.
Truthful about the cause; no invitation to type an id by hand.

### 12.5 Honesty about `google_account_email`

It is empty and stays empty in M4 (finding 8). No UI element claims to show
which account authorized the connection. Filling it would mean adding an
identity scope, which is a separate decision, not a side effect of M4.

---

## 13. Error taxonomy

`google/errors.py` grows a second base class beside `OAuthError`:

| Class | `code` | HTTP | Effect on state |
|---|---|---|---|
| `GoogleApiError` (base) | `google_api_error` | 503 | none |
| `CredentialMissing` | `credential_missing` | 409 | none |
| `CredentialRefreshFailed` | `credential_refresh_failed` | 409 | → `reauth_required` |
| `ResourceNotAccessible` | `resource_not_accessible` | 400 | none |
| `InvalidResourceId` | `invalid_resource_id` | 400 | none |
| `ResourceUnavailable` | `resource_unavailable` | 503 | none |
| `ResourceSelectionUnsupported` | — | 404 | none |

Every one carries a fixed user-facing `message`. Google's own error text is
never forwarded, logged, or stored — the M3 rule, unchanged.

---

## 14. Audit

- `INTEGRATION_RESOURCE_SELECTED` — `{provider, resource_id, resource_label,
  previous_status}`. Every key already allowlisted.
- `INTEGRATION_CONNECTED` — on the first entry into `connected`, `{provider,
  status, previous_status}`.
- Failed *selections* write no audit row: a mis-click is not a security event,
  and writing one per rejected id turns the audit log into a spam target.
- Credential refresh failure writes no new action either; `reauth_required`
  plus `last_error_code` is the record, and M6 owns the reconnect events.

No allowlist change. No new action. No metadata that could carry credential
material.

---

## 15. Tenancy and security

- Project resolved through `get_project_for_user` on **every** endpoint before
  anything else; another tenant's project is `404`, never `403`.
- The connection is looked up by `(project, provider)` from the *resolved*
  project — a connection id is never accepted from the client, so there is no
  IDOR surface at all.
- Provider comes from the URL and is validated against the catalog.
- Access tokens live in local variables for the duration of one request. They
  are never returned, never serialized, never logged, never put in an audit row.
- `external_resource_meta` stays out of the serializer.
- CSRF applies to both POSTs through the existing `SessionAuthentication` path.
- All three endpoints are `IsAuthenticated` by the DRF default.
- The `resource_id` regex bounds both format and length before any string ends
  up in a URL.

---

## 16. Concurrency, idempotency, rate limiting

- Selection takes `select_for_update()` on the connection row *after*
  verification, so a slow Google call never holds a row lock. Two concurrent
  selections serialize; the loser's write is complete and consistent, never
  torn.
- Re-selecting the same property is idempotent apart from timestamps.
- Discovery is read-only and needs no lock.
- New throttle scope `integrations`, default `30/min`, env-overridable via
  `THROTTLE_INTEGRATIONS_RATE`, applied to all three endpoints. Cheap protection
  for a shared 1-core VPS whose outbound calls are someone else's quota.
- Discovery results are **not** cached in V1. A stale property list is worse
  than a second HTTP call, and a cache would be a new moving part with no
  demonstrated requirement.

---

## 17. Test strategy

Backend (`apps/api/tests/test_ga4_resources.py`, plus additions to
`test_integrations.py` and `test_tenancy.py`). No test touches a live Google
endpoint; all HTTP stubbed with `responses`.

**Tenancy** — foreign project → 404 on all three endpoints; unknown provider →
404; Search Console (no catalog) → 404; unauthenticated → 401.

**Discovery** — single page; multi-page via `nextPageToken`; `pageSize=200` is
actually sent; page cap sets `truncated: true`; zero properties → empty list,
not an error; malformed summary entries skipped.

**Selection / verification** — the golden path persists Google's `displayName`,
**not** a label supplied in the request body (a body carrying a hostile label is
asserted to have zero effect); `properties/{id}` persisted verbatim; 403 and 404
produce the identical code and message; a non-matching `resource_id` is rejected
before any HTTP call is made (assert `len(responses.calls) == 0`); path-traversal
and absolute-URL ids rejected; selection from `pending_authorization` → 409;
selection while `connected` and verification failing leaves the previous
property intact.

**Credentials** — a still-valid token is not refreshed; an expired one is, and
the new token and expiry are persisted; a refresh response **without**
`refresh_token` leaves the stored one intact (regression on the standing rule);
`invalid_grant` → `reauth_required` + `credential_refresh_failed`; a transport
error does **not** change status.

**Health** — 200 sets both timestamps; failure sets only `last_health_check_at`
and never clears `last_successful_check_at`.

**Leakage** — no access token, refresh token, or Google error text appears in
any response body or in captured log output (`caplog`) for any of the paths
above; `external_resource_meta` is absent from every serialized response.

**Audit** — one `INTEGRATION_RESOURCE_SELECTED` row with exactly the allowlisted
keys; no row on a rejected selection.

Frontend (Vitest): status mapping for the two new `resourceAction` values;
picker renders grouped options; confirm posts the id it was given; empty state;
error state with retry; the card shows the trigger only in the two intended
states.

Mutation check before hand-off, per established practice: break the label-trust
rule (persist the body's label) and the id regex, and confirm the suite goes
red.

---

## 18. Files likely to change

New: `integrations/google/credentials.py`, `integrations/google/ga4.py`,
`integrations/resource_service.py`, `apps/api/tests/test_ga4_resources.py`,
`apps/web/components/integrations/resource-picker-dialog.tsx`,
`apps/web/components/integrations/__tests__/resource-picker-dialog.test.tsx`.

Modified: `integrations/google/errors.py`, `integrations/providers/base.py`,
`integrations/providers/google_ga4.py`, `integrations/serializers.py`,
`integrations/views.py`, `integrations/urls.py`, `config/settings.py` (throttle
scope + GA4 base URL/timeout constants), `apps/api/requirements.txt` +
both lock files (`requests` promoted to direct),
`apps/web/lib/integrations/status.ts`,
`apps/web/lib/api/types.ts`, `apps/web/lib/api/client.ts` usage,
`apps/web/components/integrations/integration-card.tsx`, existing frontend
tests, `docs/V1_BUILD_PLAN.md` (milestone tick).

Not touched: `projects/`, `accounts/`, `workspaces/`, `audit/`, any migration,
`compose.staging.yaml`, Caddy, `docker/`.

---

## 19. Migration

**None.** Every field M4 writes already exists (finding 1). If implementation
turns out to need a schema change, that is a signal the design was wrong and
comes back here for approval before any migration is written.

---

## 20. Acceptance criteria

1. A GA4 connection in `awaiting_resource_selection` lists the real properties
   the granted account can read, across multiple pages.
2. Selecting one yields `connected` with Google's own property name and a
   fresh `last_successful_check_at`.
3. `POST …/resource` with an id the credential cannot read leaves the
   connection exactly as it was and returns `resource_not_accessible`, with 403
   and 404 indistinguishable.
4. A label or status submitted in the request body has no effect on anything
   stored.
5. Revoking the grant in the Google account and re-checking yields
   `reauth_required`, and the existing authorization flow restores the
   connection.
6. No token, no refresh token, and no Google error string appears in any
   response body, log line, or audit row.
7. A member of another workspace gets 404 from all three endpoints.
8. Search Console is untouched and still reads "Not connected".
9. `pytest` and `vitest` green; no new migration; `requirements*.lock.txt`
   regenerated by the script and committed with the change.

---

## 21. Rollback

Additive and behind no schema change, so rollback is `git revert` of the merge
commit and a redeploy — no data migration, no cleanup. Rows that reached
`connected` keep their `external_resource_id`; reverted code simply stops
offering the picker, and the M3 behaviour of those rows is unchanged. Partial
rollback of just the frontend also works: the endpoints go unused. The one
lock-file change (`requests` direct) is inert on revert.

---

## 22. Milestone 5 hand-off boundary

M4 stops at: GA4 only; `resources=None` for Search Console; a `ResourceCatalog`
with exactly two methods; no `siteUrl` encoding anywhere; no
`siteUnverifiedUser` logic; no shared "resource" vocabulary beyond
`RemoteResource`'s four fields.

M5 adds `providers/google_search_console.py` with `sites.list` / `sites.get`,
both `https://…/` and `sc-domain:…` id forms and their path encoding, and
unverified-site filtering — **reusing the frontend unchanged**. If M5 cannot
reuse `resource-picker-dialog.tsx` and the two catalog methods as written, the
build plan is explicit that the abstraction gets fixed there rather than
special-cased.

**One open scope question for approval:** §11.3, the `health-check` endpoint and
**Test connection** action. `docs/V1_BUILD_PLAN.md` places it in Milestone 4;
the M4 brief describes discovery/selection/verification only. It is included
here because the build plan is source of truth and because the verification code
path is already written for §7 — the endpoint is roughly twenty extra lines.
Say so and it moves to M6 without affecting anything else in this design.
