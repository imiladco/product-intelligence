# Milestone 6 — Integration lifecycle: health, reconnect, disconnect, change

Design draft. 2026-09-04. Not implemented; not approved.

M4 and M5 got both providers *to* `connected`. M6 is everything that happens
afterwards: proving a connection still works, repairing it when it does not,
changing what it points at, and ending it deliberately.

Lifecycle management only. No ingestion, no analytics, no scheduled jobs.

---

## 0. The finding that shapes disconnect

**Token revocation cannot be scoped to one integration, and this project cannot
currently tell which integrations would be caught in the blast radius.**

Verified against current Google documentation on 2026-09-04:

- Revocation endpoint: `POST https://oauth2.googleapis.com/revoke`, token as a
  parameter.
- "If the token is an access token and it has a corresponding refresh token,
  **the refresh token will also be revoked**."
  ([OAuth 2.0 revocation](https://developers.google.com/identity/protocols/oauth2/native-app))
- M3 sends `include_granted_scopes=true` (incremental authorization), and
  Google's web-server guide states the new access token "will also cover any
  scopes to which the user previously granted the application access"
  ([web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server)).
  Scopes accumulate onto **one grant per (client, Google account)**.

Put together: a user who connected GA4 and Search Console with the same Google
account has **one** Google grant. Revoking on disconnect of one provider would
break the other — and not only in that project. The same Google account may
have been used to connect integrations in **other projects and other
workspaces**. A single disconnect could silently take down connections
belonging to tenants the acting user cannot even see.

Worse, we cannot detect the overlap: `google_account_email` is **empty on every
row** (M4 finding 8 — the granted scopes are `analytics.readonly` and
`webmasters.readonly` only, with no identity scope, so nothing ever populated
it). There is no field that would let disconnect ask "is any other connection
using this grant?".

**Decision: M6 does not call the revoke endpoint.** Disconnect deletes our
stored credentials, which is complete from this system's side, and the UI
points the user at Google's own account permissions page, which is where
grant-wide revocation belongs and where its consequences are visible to them.

This is a deliberate departure from `docs/V1_BUILD_PLAN.md` §Milestone 6, which
says "disconnect with revocation + credential deletion". The build plan was
written before the incremental-authorization decision in M3. §13 covers what it
would take to do revocation safely, if you want it later.

---

## 1. Inspection of `main` (`8eb3ffd`)

| # | Finding | Consequence for M6 |
|---|---|---|
| 1 | `ConnectionStatus` has six states; `NOT_CONNECTED` is synthesized. | **No new state is needed** (§4). M6 adds transitions, not statuses. |
| 2 | `IntegrationConnection` already has `last_health_check_at`, `last_successful_check_at`, `last_error_code`, `last_error_message`. | **No migration** (§11). |
| 3 | `AuditEvent.Action` already declares `INTEGRATION_RECONNECTED` and `INTEGRATION_DISCONNECTED`, both unwritten. The metadata allowlist already has `status`, `previous_status`, `error_code`, `reason`. | **No new audit action, no allowlist change** (§8). |
| 4 | `complete_authorization` ends by unconditionally setting `AWAITING_RESOURCE_SELECTION`. | This is the one M3 behaviour M6 must change, to preserve a still-valid selection across a reconnect (§5). |
| 5 | `_needs_forced_consent` already sends `prompt=consent` for `REAUTH_REQUIRED` and for a prior `no_refresh_token`. | Reconnect needs **no new OAuth code**; it reuses the existing authorize endpoint. |
| 6 | `resource_service.select_resource` refuses a *different* resource while `connected` (`ResourceChangeNotSupported`), and re-verifies the same one idempotently. Verification-before-persistence and "nothing written on failure" are already in place. | Change-resource is mostly a **deletion**: lift the guard, retire the now-dead error class (§6). |
| 7 | `credentials.access_token_for` and `mark_reauth_required` are provider-agnostic and already map `invalid_grant` → `reauth_required`. | Health checks and reconnect reuse them unchanged. |
| 8 | `ResourceCatalog.verify_resource` is exactly what a health check needs, for both providers. | The health check is `verify_resource` against the **stored** resource — no new provider method, and the catalog stays at three methods. |
| 9 | `resource_service._usable_connection` treats `REAUTH_REQUIRED` as a hard stop and restricts work to `{AWAITING_RESOURCE_SELECTION, CONNECTED}`. | Health check and change-resource need `ERROR` admitted too (§4), since a connection in `ERROR` is exactly one the user wants to test or repoint. |
| 10 | `IntegrationEntry` exposes `supports_resource_selection` (M5). | The lifecycle actions gate on capability the same way, with no provider names in components (§7). |
| 11 | `status.ts` has two action channels: `actionLabel` (authorization) and `resourceAction`. | M6 adds two booleans rather than overloading either (§7.1). |
| 12 | Slashless URL convention, `projects/<int:project_id>/integrations/<str:provider>/…`. | Two new paths, no more (§3). |

---

## 2. Lifecycle differences between GA4 and Search Console

Only three, and each is handled at the provider boundary rather than in shared
code.

| Difference | GA4 | Search Console | Where it lives |
|---|---|---|---|
| **How access is lost** | The property becomes unreadable: 403 or 404. | Two ways: 403/404, **or** a 200 whose `permissionLevel` drops to `siteUnverifiedUser` when an owner removes the user. | `verify_resource` in each provider module already handles its own case; both raise `ResourceNotAccessible`. Shared code sees one outcome. |
| **Resource deletion** | Google's `properties.get` page does **not** document what it returns for a trashed property, and this design does not guess. Whatever comes back goes through the existing mapping: 200 → healthy, 403/404 → not accessible. | A removed site returns 403/404. | No special handling. §14 includes an optional staging observation to record GA4's actual behaviour, which would be a fact worth having before any future ingestion work. |
| **Grant sharing** | Both providers share one Google grant when authorized with the same account (§0). | Same. | Disconnect never revokes (§0), so this difference stays inert. |

Everything else is identical, which is the payoff from M5's `ResourceCatalog`:
health check, reconnect, disconnect and change-resource are written **once**.

---

## 3. API contracts

Two new endpoints. Both session-authenticated, tenant-scoped through
`get_project_for_user`, throttled under the existing `integrations` scope, and
both returning the provider's `IntegrationEntry` so the client re-renders from
one authoritative payload.

### 3.1 `POST …/integrations/{provider}/health-check`

Re-verifies the **stored** resource. Reads nothing from the body — the
identifier comes from the database, never from the request.

- `200` — the entry, with whatever status the check produced.
- `409 conflict` — `credential_missing` (no credential), or `resource_missing`
  (nothing selected yet; nothing to check).
- `404` — unknown project/provider, or a provider with no catalog.
- Transient provider failure is **200**, not 503: the check completed and its
  result is "could not reach Google", which is information the entry carries.
  A 503 would make the client retry a thing that already told it the truth.

### 3.2 `POST …/integrations/{provider}/disconnect`

- `200` — the entry, now `disconnected`. Idempotent: disconnecting an already
  disconnected integration is a 200 that changes nothing (§9).
- `404` — unknown project/provider.
- No body.

### 3.3 Changed behaviour on existing endpoints

| Endpoint | Change |
|---|---|
| `POST …/resource` | A **different** resource is now accepted while `connected` (§6). Same-resource idempotency is unchanged. `resource_change_not_supported` is retired. |
| `POST …/authorize` | Unchanged on the wire. Reconnect is this endpoint; the frontend labels it differently by state. |
| `GET …/integrations` | Entry gains no field. The lifecycle actions are derived from `status` + `supports_resource_selection` + whether a resource is selected, all of which the payload already carries. |
| `GET /api/integrations/oauth/google/callback` | Unchanged on the wire; its terminal status logic changes (§5). |

**No endpoint is removed or renamed. No response field is removed or renamed.**
`resource_change_not_supported` disappearing is the only vocabulary change, and
it can only ever have been received by a client that tried something now
allowed.

---

## 4. State machine

### 4.1 Transition table

| From | Event | To | Writes |
|---|---|---|---|
| `connected` | health check succeeds | `connected` | both timestamps; errors cleared |
| `connected` | health check → resource not accessible | `error` | `last_health_check_at`, error fields |
| `connected` | health check → credential rejected | `reauth_required` | `last_health_check_at`, error fields |
| `connected` | health check → transient | `connected` | `last_health_check_at`, error fields; **`last_successful_check_at` untouched** |
| `connected` | different resource verified | `connected` | resource fields, both timestamps, errors cleared |
| `connected` | different resource fails verification | `connected` | nothing — the existing selection survives intact (§6) |
| `connected` \| `error` \| `reauth_required` \| `awaiting_resource_selection` | disconnect | `disconnected` | status; credential row deleted; errors cleared |
| `error` | health check succeeds | `connected` | both timestamps; errors cleared |
| `error` | reauthorization completes, stored resource verifies | `connected` | as above |
| `reauth_required` | reauthorization completes, stored resource verifies | `connected` | as above |
| `reauth_required` | reauthorization completes, no stored resource | `awaiting_resource_selection` | M3 behaviour, unchanged |
| `reauth_required` | reauthorization completes, stored resource fails to verify | `awaiting_resource_selection` | resource id **retained**, error fields set (§5.2) |
| any | reauthorization denied / scope not granted | `error` | M3 behaviour, unchanged |
| `disconnected` | authorize completes, stored resource verifies | `connected` | credential recreated; resource restored |
| `disconnected` | health check | — | `409 credential_missing`; no state change |

### 4.2 What each state means after M6

Unchanged definitions, stated so the new transitions cannot quietly redefine
them:

- **`connected`** — a credential is held **and** the selected resource returned
  success on a real call. Still never set without live proof (M4 invariant).
- **`awaiting_resource_selection`** — a credential is held; access to a
  resource has not been proven. It may now hold a *retained* resource id from
  before a reconnect (§5.2); "awaiting" refers to the proof, not the choice.
- **`error`** — the connection cannot do its job and needs a decision from the
  user: authorization failed, or the selected resource is gone.
- **`reauth_required`** — the credential is dead; only re-authorizing helps.
- **`disconnected`** — deliberately ended by a user. No credential is stored.

### 4.3 Failure taxonomy — which errors persist, and when they clear

The rule that separates a dead connection from a bad afternoon:

| Class | Examples | Status effect | `last_error_*` | `last_successful_check_at` |
|---|---|---|---|---|
| **Success** | 200 and, for GSC, an accepted permission level | → `connected` | cleared | **set to now** |
| **Credential** | `invalid_grant`, 401 | → `reauth_required` | set | untouched |
| **Permanent resource** | 403, 404, `siteUnverifiedUser` | → `error` | set | untouched |
| **Transient** | 429, 5xx, timeout, DNS | **status unchanged** | set | untouched |
| **Configuration** | scope not granted, no refresh token | → `error` | set | untouched |

Two things follow, and both are deliberate:

- **A transient failure never changes status.** Google having a bad minute must
  not present as a broken integration, and must not cost the user a reconnect.
  It is recorded so the card can say "connected; last check could not reach
  Google", with `last_successful_check_at` still showing when it did work.
- **`last_successful_check_at` is never cleared by anything.** Not by a failure,
  not by disconnect. It is the historical fact "this worked at least once, at
  this time".

Errors are cleared on exactly four events: a successful health check, a
successful resource selection, a completed reauthorization, and a disconnect.
Nothing else touches them, so a stale error message cannot outlive its cause.

---

## 5. Reconnect

Reconnect is not a new flow. It is the existing authorize endpoint, reached
from a different state, with one change to what happens when the callback lands.

### 5.1 What changes in `complete_authorization`

Today it ends by unconditionally setting `AWAITING_RESOURCE_SELECTION`. That is
correct for a first authorization and wrong for a reconnect: it discards a
selection the user already made and still wants.

New terminal logic, after credentials are stored and scopes verified:

```
if no stored external_resource_id:
    status = awaiting_resource_selection          # unchanged from M3
else:
    verify the stored resource with the new credential
      success  -> status = connected, both timestamps, errors cleared
      failure  -> status = awaiting_resource_selection,
                  resource id and label RETAINED, error fields set
```

So the ordinary reconnect — credential expired, user re-authorizes, everything
else still fine — returns straight to `connected` on the same property, with no
re-picking. That is "preserve the selection when still valid", and the *when
still valid* is decided by a live call, never assumed.

### 5.2 Why a failed re-verify retains the resource id

Dropping it would lose information the user wants and we still have. Keeping it
lets the card say "this pointed at *poolino*, which is no longer accessible" and
offer **Change property**, which is a better recovery than an empty picker.

The status is `awaiting_resource_selection` rather than `error` because the
credential is *good* — the remaining problem is which resource, which is
exactly what that state is for. A retained id with an error code and no
`connected` status cannot be mistaken for a working connection.

### 5.3 Credentials and scopes on reconnect

All M3 rules preserved verbatim:

- A token response without a `refresh_token` never blanks the stored one.
- `prompt=consent` is sent only when a new refresh token is actually needed —
  `_needs_forced_consent` already returns true for `REAUTH_REQUIRED`.
- Granted scopes are verified explicitly; a missing required scope is
  `ScopeNotGranted` → `error`, never a silent partial connection.
- `granted_scopes` is overwritten with what Google actually granted this time.
- The OAuth `state` is single-use, hashed, expiring, and re-checked for
  membership at callback time.

**Scope change is not special-cased.** If the user unticks a permission during
re-consent, the existing check fails the reconnect into `error` with
`scope_not_granted`. If they grant *more* than we need, we store what was
granted and carry on: extra scope is Google's business, not a failure.

### 5.4 Revoked credentials

A user who revokes access in their Google account sees, on the next call:
`invalid_grant` → `reauth_required` → **Reconnect** → `prompt=consent` (because
the state is `reauth_required`) → a fresh refresh token → stored resource
re-verified → `connected`. No new code; this is M3 and M4 machinery reaching its
intended end.

---

## 6. Change selected resource

Almost entirely a removal.

- Lift the guard: `select_resource` no longer raises
  `ResourceChangeNotSupported` when a *different* resource is submitted while
  `connected`, and neither does the re-check under the row lock.
- Retire `ResourceChangeNotSupported` from the taxonomy. A dead error class that
  can never be raised is worse than no class: it invites a future reader to
  believe the restriction still exists.
- Everything protective stays exactly as M4/M5 built it, and this is the whole
  safety argument for lifting the guard:
  - the identifier is normalized and validated before any outbound call;
  - the new resource is **verified against the provider** before anything is
    written;
  - the label and metadata come from the verification response, never the body;
  - the write happens once, under `select_for_update`, in one transaction;
  - **nothing is written on any failure path**, so a failed change leaves the
    previous resource, its label, its metadata and both timestamps untouched.
- Same-resource re-submission stays idempotent.
- Works identically for both providers, through the same catalog.

`_usable_connection` gains `ERROR` alongside `AWAITING_RESOURCE_SELECTION` and
`CONNECTED`: a connection whose property was deleted sits in `error` and
repointing it is precisely the repair.

---

## 7. Frontend

### 7.1 The action model

`status.ts` gains two booleans beside the two existing channels, rather than
overloading either:

```ts
interface StatusPresentation {
  label: string;
  variant: BadgeVariant;
  needsAttention: boolean;
  actionLabel: string | null;             // authorization: Connect / Reconnect / …
  resourceAction: "select" | "change" | null;
  canTestConnection: boolean;             // NEW
  canDisconnect: boolean;                 // NEW
  note: string | null;
}
```

| Status | `actionLabel` | `resourceAction` | Test | Disconnect |
|---|---|---|---|---|
| `not_connected` | Connect | — | no | no |
| `pending_authorization` | Restart authorization | — | no | no |
| `awaiting_resource_selection` | — | select | no | yes |
| `connected` | — | change | **yes** | **yes** |
| `error` | Try again | change | yes | yes |
| `reauth_required` | **Reconnect** | — | no | yes |
| `disconnected` | Connect | — | no | no |

Two gates on top, both from data the entry already carries and neither naming a
provider (the M5 rule):

- `resourceAction` is suppressed unless `entry.supports_resource_selection`.
- `canTestConnection` additionally requires a selected resource — there is
  nothing to test otherwise, and the endpoint would return 409.

`reauth_required` offers no test: the answer is already known, and the action
that helps is Reconnect.

### 7.2 Components

- **`TestConnectionButton`** (new, client): posts, then `router.refresh()`.
  Reports the outcome from the returned entry rather than inventing one —
  "Connected" on success, the entry's own error message otherwise.
- **`DisconnectDialog`** (new, client): a confirm dialog before an irreversible
  action, built from the existing `dialog` primitive. Its copy states plainly
  what happens: stored credentials are deleted, the selection is remembered,
  and **access is not revoked at Google** — with a link to
  `https://myaccount.google.com/permissions` for users who want that too. Being
  vague here would be the security failure, not the UX one.
- **`ResourcePickerDialog`** (existing): reused unchanged for **Change
  property**; only its trigger label varies, which is already a prop.
- **`IntegrationCard`**: renders the action row from the flags above. No new
  provider branching anywhere.

### 7.3 Error and recovery states

The card already renders `last_error_message`. M6 adds the distinction the
taxonomy makes: a **transient** failure on a `connected` integration renders as
a muted note beside a still-green badge, not as a destructive alert. Only
`error` and `reauth_required` get the destructive treatment, because only they
need the user to do something.

---

## 8. Audit

| Action | When | Metadata |
|---|---|---|
| `INTEGRATION_RECONNECTED` | a reauthorization completes on a connection that already had a credential | `provider`, `status`, `previous_status` |
| `INTEGRATION_DISCONNECTED` | a disconnect that actually changed state | `provider`, `previous_status`, `reason` |
| `INTEGRATION_RESOURCE_SELECTED` | unchanged, including for a *change* | `provider`, `resource_id`, `resource_label`, `status`, `previous_status` |

Both new actions are already declared, and every metadata key is already
allowlisted: **no new action, no allowlist change, no migration.**

Deliberately **not** audited: health checks. A user-triggered read that changes
nothing is not a security event, and auditing every click turns the log into
noise that hides the events that matter. When a health check *does* transition
the connection (to `error` or `reauth_required`), the status and
`last_error_code` on the row are the record — consistent with M4, where a
credential-refresh failure writes no event either.

An idempotent disconnect (already disconnected) writes **no** event, for the
same reason: nothing happened.

---

## 9. Idempotency and concurrency

| Operation | Idempotent? | How |
|---|---|---|
| Health check | Yes, apart from timestamps | Pure re-verification; no resource state is written |
| Disconnect | Yes | Already `disconnected` → 200, no writes, no audit row |
| Change resource | Yes for the same id | Existing M4 behaviour, unchanged |
| Reconnect | Yes | Each attempt is its own single-use OAuth state; M3 supersedes older unconsumed requests for the same user+project+provider |

**Concurrency.** Every state-changing operation takes `select_for_update` on the
connection row for its write, and — as in M4 — the lock is taken **after** the
outbound call, so a slow Google request never holds a row lock.

Three races worth naming, all resolved by that lock:

- *Disconnect during an in-flight health check.* The check's write re-reads the
  row under the lock and **must not resurrect a disconnected connection**: if
  the status is `disconnected` when the lock is acquired, the check's result is
  discarded. Losing a health result is correct; undoing a user's explicit
  disconnect is not.
- *Two concurrent resource changes.* Serialized; last writer wins with a
  complete, consistent row. Neither can produce a torn mix of one resource's id
  and another's label, because id, label and metadata are written in one save.
- *Reconnect racing a health check.* Both end at a status justified by a live
  call; the lock decides the order, and both write the same kind of state.

---

## 10. Backend service boundaries

```
integrations/
  lifecycle_service.py   NEW  health_check() and disconnect()
  resource_service.py    MODIFIED  guard lifted; ERROR admitted; shared result mapper
  oauth_service.py       MODIFIED  terminal status preserves a valid selection (§5.1)
  google/errors.py       MODIFIED  ResourceChangeNotSupported retired; one code added (§12)
  google/credentials.py  UNCHANGED
  google/ga4.py          UNCHANGED
  google/search_console.py UNCHANGED
  resources.py           UNCHANGED — the catalog stays at three methods
  views.py, urls.py, serializers.py  MODIFIED  two endpoints
```

The single most important boundary decision: **`verify_resource` is not
duplicated.** Health check, reconnect and change-resource all reach the provider
through the same catalog method, and all three interpret its outcome through
one shared mapper that applies §4.3. Three copies of "what does this failure
mean" would drift, and the drift would be invisible until a connection lied
about its state.

No provider module changes at all. That is the M5 abstraction paying for itself
a second time.

---

## 11. Migration impact

**None required.** Every field M6 writes already exists (finding 2), and no
field changes type, width, nullability or default. `makemigrations --check`
must stay clean, and that is an acceptance criterion.

Considered and rejected as unnecessary:

| Candidate | Why not |
|---|---|
| `disconnected_at` | The audit event carries who and when; a second copy could disagree |
| `health_check_count` / failure streaks | Nothing in V1 consumes it; that is retry policy, and V1 has no scheduler |
| A separate `last_transient_error` | `last_error_code` plus the status already distinguishes the classes (§4.3) |
| `google_account_email` backfill | Would need an identity scope; §0 explains why it is wanted and §13 why it is still deferred |

If implementation finds a schema change unavoidable, it stops and returns here
for approval rather than writing one.

---

## 12. Error taxonomy changes

| Code | Change |
|---|---|
| `resource_change_not_supported` | **Removed.** Its restriction is lifted (§6) |
| `resource_missing` | **Added** — 409, health check on a connection with nothing selected |
| Everything else | Unchanged |

`resource_not_accessible` keeps its single-code treatment of 403, 404 and
`siteUnverifiedUser`: the existence-oracle argument from M4/M5 applies just as
much to a health check as to a selection.

---

## 13. Security

Every M3/M4/M5 guarantee is preserved; §15 lists them as invariants with the
tests that hold them. Specific to M6:

- **Disconnect deletes credential material.** The `IntegrationCredential` row is
  hard-deleted, not blanked, so no ciphertext lingers in a row that reads as
  empty. Deletion happens inside the same transaction as the status change.
- **Disconnect does not revoke** (§0), and the UI says so rather than letting
  the user assume otherwise. Silence here would be the real risk: a user who
  believes access is revoked and finds it is not.
- **Disconnect is tenant-scoped like everything else** — project resolved from
  the user's memberships first, connection found by `(project, provider)` from
  the resolved project. No connection id is ever accepted from a client.
- **Health check reads its identifier from the database**, never the request
  body, so it cannot be turned into a probe for arbitrary resources.
- **Change-resource keeps every M4 protection**; lifting the guard changes *when*
  a selection may be made, never *how* it is proven (§6).
- No new scope, no new token storage, no change to encryption or refresh.
- New endpoints are POST, so CSRF applies through the existing
  `SessionAuthentication` path, and both are throttled.

**If grant-wide revocation is wanted later**, it needs, in order: an identity
scope to populate `google_account_email`; a way to enumerate connections
sharing a grant *across tenants* without leaking their existence to the acting
user; and an explicit, unambiguous confirmation naming what else will break.
That is its own milestone, not a checkbox in this one.

---

## 14. Test plan

Backend — `tests/test_lifecycle.py`, plus additions to the existing suites.
All HTTP stubbed with `responses`; no test reaches Google. Every case runs for
**both providers**, which is the parity the M5 abstraction earns.

**Health check** — success sets both timestamps and clears errors; 403/404 →
`error` with `last_successful_check_at` untouched; GSC `siteUnverifiedUser` →
`error` identically; 401 → `reauth_required`; 429/5xx/timeout → **status
unchanged**, `last_health_check_at` set, `last_successful_check_at` untouched;
no resource selected → 409 `resource_missing` with zero outbound calls;
disconnected → 409 `credential_missing`; the request body is ignored (a posted
`resource_id` for another resource is never called).

**Reconnect** — from `reauth_required` with a valid stored resource →
`connected`, same resource, no re-pick; with a resource that fails verification
→ `awaiting_resource_selection` with the id **retained** and an error recorded;
with no stored resource → `awaiting_resource_selection` (M3 behaviour intact);
`prompt=consent` is sent from `reauth_required` and **not** from `connected`; a
token response without a refresh token leaves the stored one intact; a missing
scope → `error` `scope_not_granted`; one `INTEGRATION_RECONNECTED` row.

**Disconnect** — deletes the `IntegrationCredential` row; leaves the resource id
and label; leaves `last_successful_check_at`; status `disconnected`; one
`INTEGRATION_DISCONNECTED` row; a second disconnect is 200, writes nothing and
records **no** second event; **no request is made to the revoke endpoint** —
asserted explicitly, because this is a decision a future reader might otherwise
"fix"; reconnect after disconnect restores `connected` with the remembered
resource.

**Change resource** — a different resource while `connected` now succeeds and
replaces id, label and metadata together; a failed verification leaves **all
four** of id, label, metadata and both timestamps untouched; same-resource
re-submission stays idempotent; a body-supplied label still has no effect;
`resource_change_not_supported` no longer exists.

**Error lifecycle** — the §4.3 table asserted directly, one test per class,
including that a transient failure does not change status and does not clear
`last_successful_check_at`; errors cleared by each of the four clearing events
and by nothing else.

**Concurrency** — a health-check result is discarded if the row is
`disconnected` when the lock is taken.

**Leakage** — no token, refresh token or Google error text in any response, log
line or audit row, for every new path.

**Tenancy** — both new endpoints: foreign project 404, unknown provider 404,
unauthenticated 403.

**Mutation checks before hand-off** — make a transient failure clear
`last_successful_check_at`; let disconnect blank the credential instead of
deleting the row; let a failed reconnect verification set `connected`; add a
revoke call. Each must turn the suite red.

Frontend — the action matrix in §7.1 asserted per status; no test button
without a selected resource; the disconnect dialog states that access is not
revoked at Google; a transient error on a `connected` card renders as a note,
not a destructive alert; no provider-specific branching in any shared component
(the existing source-scan test extended to the new components).

---

## 15. Invariants M6 must preserve

From M3:

1. OAuth `state` is single-use, hashed at rest, expiring, bound to
   user+project+provider, and membership is re-checked at callback time.
2. A token response without a `refresh_token` never overwrites a stored one.
3. `prompt=consent` is sent only when a new refresh token is actually needed.
4. A granted scope is verified against the token response, never assumed.
5. No token, code, client secret or OAuth state reaches a log line.

From M4:

6. `connected` requires a live successful call against the **selected**
   resource. No exceptions, and none added here.
7. The stored label comes from the provider's verification response, never the
   request body.
8. `403` and `404` are one indistinguishable outcome.
9. A malformed identifier is rejected before any outbound call.
10. `connected_by` records who *authorized*, and selection does not reassign it.
11. `external_resource_meta` stays minimal, unserialized, and free of
    timestamps that health fields already own.
12. One audit event per user-meaningful outcome; no paired duplicates.

From M5:

13. Provider vocabulary stays inside the provider module; shared code names no
    provider (source-scanned by test).
14. `ResourceCatalog` has exactly three methods — M6 adds none.
15. Search Console verification refuses any permission level not on the
    allowlist, including unrecognized future ones.
16. The picker's action is gated on provider capability, not connection status.

Every one of these has a test today. **The M6 rule is the M5 rule:** those
tests may change where a symbol was renamed, never where a value is asserted.

---

## 16. Rollback

No schema change, so rollback is `git revert` of the merge commit plus a
redeploy — no data migration, no cleanup.

What survives a rollback, and is safe:

- Connections that reached `disconnected` stay `disconnected` with no
  credential. Reverted code renders them as "Disconnected" and offers Connect,
  which is exactly right — M5 already knows that state.
- Connections whose resource was *changed* keep the new resource. Reverted code
  treats it as the selection it is.
- A connection left in `awaiting_resource_selection` with a retained resource id
  (§5.2) renders under reverted code as "Select a property" with the old
  property shown. Slightly odd, entirely harmless, and one selection fixes it.

Nothing M6 writes is unreadable by M5 code, which is what makes the revert safe
rather than merely possible.

---

## 17. Staging acceptance checklist

Preconditions: staging on merged `main`, both containers healthy, no `.env`
change (M6 introduces no setting), and the two live connections from M4/M5 —
GA4 on `properties/549483499` (*poolino*) and Search Console on
`sc-domain:poolinogroup.com` (`siteFullUser`).

**Regression first**

1. Both integrations still read **Connected** with their existing resources,
   unchanged, before any M6 action.

**Health check**

2. **Test connection** on GA4 → stays Connected; `last_health_check_at` and
   `last_successful_check_at` both advance.
3. Same for Search Console.
4. No audit row is written by either check.

**Change property**

5. GA4: change to a different property → Connected on the new one; id, label
   and metadata all change together; one `integration.resource_selected` row.
6. Change back to `properties/549483499` → Connected, *poolino* restored.
7. Search Console: change to a different verified site and back, same
   expectations.
8. Attempt a change to an inaccessible identifier → refused, and the existing
   resource is **completely unchanged** (id, label, metadata, both timestamps).

**Reconnect**

9. Revoke access at `https://myaccount.google.com/permissions`, then **Test
   connection** → `Reauthorization required`.
10. **Reconnect** → Google consent → returns to **Connected on the same
    property, without re-picking**. This is the milestone's headline behaviour.
11. One `integration.reconnected` row; the stored refresh token is present and
    still Fernet-encrypted.

**Disconnect**

12. **Disconnect** Search Console → status `disconnected`; the
    `integrations_integrationcredential` row for it is **gone** (`SELECT count(*)`
    = 0 for that connection); the resource id and label remain;
    `last_successful_check_at` remains.
13. Exactly one `integration.disconnected` row.
14. **GA4 is unaffected** — still Connected, credential intact. This is the §0
    blast-radius check, and the reason disconnect does not revoke.
15. Disconnect again → no change, no second audit row.
16. Reconnect Search Console → back to Connected on the remembered site.

**Security**

17. API log leak check over the whole session returns **0** for `ya29.`,
    `1//`, `client_secret`, `"access_token"`, `"refresh_token"`.
18. Both credentials Fernet-encrypted; no plaintext tokens in the database.
19. `makemigrations --check` reports no changes.

**Optional observation** — with a spare GA4 property, delete it in Google and
run **Test connection**, to record what `properties.get` actually returns for a
trashed property (§2). Not an acceptance gate; a fact worth having.

---

## 18. Hand-off

M6 completes the V1 integration lifecycle. What remains after it is Milestone 7
— production deployment, backups, monitoring and CI — and nothing in this design
anticipates or blocks it.

Explicitly still out of scope, and still out of V1: scheduled or background
health checks, retry policies, notifications, any analytics data, and
grant-wide revocation (§13).
