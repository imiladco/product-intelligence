# Milestone 6 — Integration lifecycle: health, reconnect, disconnect, change

Design draft. 2026-09-04. Revised after review; not implemented; not approved.

**Revision 4 — what the third review found, and the decision it forced:**
credential refresh is itself an unfenced mutating outbound call, so it needs
its own optimistic concurrency and `google/credentials.py` can no longer be
listed as unchanged (§9.3.1); forced consent cannot be keyed on "first
connection", because a new row does not prove a first authorization of that
Google account for this application — it is keyed on whether we hold a refresh
token we can preserve (§5.3.1); and the authorization fence's cross-row
timestamp ordering is genuinely ambiguous, so **M6 now introduces a
`lifecycle_generation` column and a migration** (§9.4, §11). "No migration" is
no longer a goal of this design.

**Revision 3 — what the second review found:** forced consent is required
after a disconnect, because the local refresh token is gone while Google's
authorization is not (§5.3); the Race C fence only covered callbacks that had
not yet consumed their request, so authorization finalization needs its own
fence covering an already-running callback and a superseded older one (§9.4);
idempotent disconnect must still invalidate in-flight authorizations (§9.1);
reconnect verification failure needed the same taxonomy split as everything
else rather than one collapsed path (§5.1); the transition table misstated M3's
denial behaviour, which is non-destructive (§4.1); and the fence-convention test
must assert that `updated_at` **changed**, not that wall-clock time advanced
(§14).

**Revision 2 — what the first review asked for and where it lives:** a stale-result
fence, designed and tested against three named races (§9); a recovery model
keyed on `status` **and** `last_error_code` rather than status alone, with the
full action matrix (§7.1); a staging checklist that treats grant revocation as
the destructive, terminal step it is (§17); the §0 terminology corrected to
Google's *combined authorization for the user and API project*; and an explicit
rule for which audit event an authorization writes after a disconnect (§8.1).

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

Put together: a user who connected GA4 and Search Console with the same Google
account holds **one combined authorization for that user and this API project**
— not merely one grant for one OAuth client. Google's documentation is explicit
that a combined authorization can span grants obtained through *different
clients under the same API project*, so the blast radius is wider than a single
client id would suggest, and wider than this system can see.

Revoking on disconnect of one provider would therefore break the other — and
not only in that project. The same Google account may have been used to connect
integrations in **other projects and other workspaces**. A single disconnect
could silently take down connections belonging to tenants the acting user
cannot even see.

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
| any *with a credential* | authorization **denied** by the user | *unchanged* | Nothing. Credential, status and resource all preserved; `INTEGRATION_AUTHORIZATION_FAILED` recorded (§5.5) |
| first authorization, no credential | authorization **denied** by the user | *row deleted* → `not_connected` | M3 behaviour, unchanged |
| any | **scope not granted** | `error` | `scope_not_granted`; M3 behaviour, unchanged |
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
    verify the stored resource with the new credential, and apply §5.1.1
```

So the ordinary reconnect — credential expired, user re-authorizes, everything
else still fine — returns straight to `connected` on the same property, with no
re-picking. That is "preserve the selection when still valid", and the *when
still valid* is decided by a live call, never assumed.

### 5.1.1 Reconnect verification outcomes

"Failure" is not one thing, and collapsing it into a single
`awaiting_resource_selection` path would contradict the taxonomy in §4.3. The
same four classes apply here as everywhere else:

| Verification outcome | Status | Resource | `last_error_code` | Primary recovery (§7.2) |
|---|---|---|---|---|
| Success | `connected` | kept | cleared | — |
| `ResourceNotAccessible` (403/404/`siteUnverifiedUser`) | `awaiting_resource_selection` | **retained** | `resource_not_accessible` | **Change property** |
| Credential rejected (401 / `invalid_grant`) | `reauth_required` | **retained** | `credential_refresh_failed` | **Reconnect** |
| Transient (429/5xx/timeout) | `awaiting_resource_selection` | **retained** | `resource_unavailable` | **Test connection** |

Three things this gets right that the collapsed version did not:

- **A transient blip never claims the resource became inaccessible.** The error
  code is `resource_unavailable`, which is the transient class, so the card
  offers *Test connection* — non-destructive, and the truthful next step. The
  user is not sent to re-pick a property that is probably fine.
- **A credential rejected immediately after a successful token exchange is
  real, and says so.** It happens when access is revoked between the exchange
  and the verify. `reauth_required` is honest: we hold a credential Google will
  not accept. Recovery is Reconnect, and the resource is retained so the repair
  costs nothing extra.
- **Only a genuine resource failure sends the user to the picker**, which is the
  only case where re-picking is the actual fix.

In every non-success row the resource id and label are **retained** (§5.2), and
in none of them is `last_successful_check_at` touched.

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
  and M6 adds one state where it is (§5.3.1).
- Granted scopes are verified explicitly; a missing required scope is
  `ScopeNotGranted` → `error`, never a silent partial connection.
- `granted_scopes` is overwritten with what Google actually granted this time.
- The OAuth `state` is single-use, hashed, expiring, and re-checked for
  membership at callback time.

**Scope change is not special-cased.** If the user unticks a permission during
re-consent, the existing check fails the reconnect into `error` with
`scope_not_granted`. If they grant *more* than we need, we store what was
granted and carry on: extra scope is Google's business, not a failure.

### 5.3.1 `DISCONNECTED` must force consent

`_needs_forced_consent` returns true today for `REAUTH_REQUIRED` and for
`ERROR` + `no_refresh_token`. **`DISCONNECTED` must be added**, and the reason
is a direct consequence of §0's decision not to revoke:

```
disconnect                    → our refresh token is deleted
                              → Google's authorization still exists
user clicks Connect           → no prompt=consent, because the state is not
                                one of the two that force it
Google sees existing consent  → may omit refresh_token entirely
_store_credentials            → no new token, and none stored to preserve
                              → NoRefreshToken → error
```

The user's first attempt after a disconnect fails. It self-heals on the second
attempt — `ERROR` + `no_refresh_token` does force consent — but a first try that
fails for a reason we designed in is not acceptable when one predicate fixes it.

`DISCONNECTED` is *precisely* the state where a new refresh token is required:
we deliberately destroyed ours while leaving Google's authorization intact.

### 5.3.2 The predicate is local capability, not "is this the first time"

M3's rule rested on an assumption that is false: *"Google issues a refresh
token on the first authorization anyway, so a new connection need not force
consent."* A brand-new `IntegrationConnection` row proves nothing of the kind.
It proves this **project** has not connected this provider. The same Google
account may already have authorized this same application through **another
project or another workspace**, in which case Google sees existing consent and
may return no `refresh_token` at all — and a first connection then fails on
`NoRefreshToken` exactly as a post-disconnect one does.

This system deliberately holds no Google identity (§0), so it cannot ask "has
this account authorized us before?". It does not need to. The question it can
always answer is local, and it is the one that matters:

> **Can this authorization preserve an existing refresh token? If not, it must
> guarantee acquiring a new one.**

So the predicate is: **force consent unless we hold a refresh token we have no
reason to distrust.**

```python
def _needs_forced_consent(connection) -> bool:
    if connection is None:
        return True                      # nothing stored: nothing to preserve
    if connection.status in (REAUTH_REQUIRED, DISCONNECTED):
        return True                      # stored token is dead, or deleted
    credential = stored_credential(connection)
    if credential is None or not credential.refresh_token:
        return True                      # nothing to preserve
    if connection.status == ERROR and connection.last_error_code == NO_REFRESH_TOKEN:
        return True                      # the previous attempt proved it
    return False                         # a live token we can carry forward
```

| Situation | Force consent? | Reasoning |
|---|---|---|
| **Brand-new connection, account never authorized this app** | **Yes** | Consent would be shown anyway for new scopes; `prompt=consent` changes nothing the user sees, and guarantees the refresh token |
| **Brand-new connection, same account already authorized this app elsewhere** | **Yes** | The case M3 could not see. Without forcing, Google may omit `refresh_token` and the first connection fails |
| `PENDING_AUTHORIZATION` with no credential stored | Yes | Nothing to preserve |
| `AWAITING_RESOURCE_SELECTION` with a stored refresh token | **No** | We hold a token; a response omitting one preserves it |
| `CONNECTED` (voluntary re-authorization) | **No** | The stored refresh token works; preserving it is correct and re-consent is noise |
| `ERROR` + `no_refresh_token` | Yes | Unchanged from M3 |
| `ERROR`, other codes, credential intact | No | The credential is not the problem |
| `REAUTH_REQUIRED` | Yes | Unchanged from M3 |
| **`DISCONNECTED`** | **Yes** | Our token is gone; Google's consent is not |

The two `DISCONNECTED` and status checks are redundant with the credential
check — disconnect deletes the row — and are kept deliberately, so that a future
change to *how* disconnect clears credentials cannot silently remove forced
consent.

**What this costs.** A genuinely first authorization now carries
`prompt=consent`. Google shows a consent screen for newly requested scopes
regardless, so for that case the parameter changes nothing the user experiences.
The saving M3 was protecting turns out to be mostly imaginary, and the failure
it exposes is real. Old behaviour is not preserved merely for being old when it
cannot guarantee offline access — and offline access *is* a hard requirement
here, because the backend must reach the provider with no user present.

### 5.4 Cancellation is non-destructive

M3's denial path is **not** an error path, and the first draft of this design
described it wrongly. What it actually does, and what M6 preserves:

| Situation | Behaviour |
|---|---|
| A first authorization is denied, and no credential is stored | The connection row is **deleted**; the integration returns to `not_connected` |
| An authorization on an **existing** integration is denied | The credential, the status and the resource are all **left exactly as they were**. No `error`, no state change |
| Either case | `INTEGRATION_AUTHORIZATION_FAILED` is recorded with `access_denied` |

Backing out of a Google consent screen must never damage a working integration.
A user who clicks Reconnect, thinks better of it, and presses back still has the
integration they had a minute ago.

**`ScopeNotGranted` is a different case and is not conflated with it.** Denial
means "I did not do this"; a missing scope means "I did this, but withheld
something the integration requires". The second is a genuine misconfiguration
and does set `error`, which is M3 behaviour and correct.

### 5.5 Revoked credentials

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

### 7.1 The recovery model

Status alone is not enough. `error` is reached by causes whose repairs have
nothing to do with each other: a deleted GA4 property and a declined OAuth scope
both land there, and offering "Try again" — an *authorization* action — for the
first is simply wrong. The credential is healthy; re-running consent fixes
nothing and teaches the user that the button does not work.

So recovery is keyed on **`status` + `last_error_code`**, both of which are
already provider-neutral (M5 §13 checked this: the codes say `resource`,
`credential`, `scope`, never `property` or `site`).

Error codes are grouped into four **recovery classes**, and the class picks the
primary action:

| Class | Codes | Primary action | Why |
|---|---|---|---|
| `credential` | `credential_refresh_failed`, `no_refresh_token` | **Reconnect** | The grant is gone; only re-authorizing helps |
| `authorization` | `scope_not_granted`, `token_exchange_failed`, `invalid_state`, `access_denied`, `provider_mismatch`, `oauth_error` | **Try again** | The authorization itself failed or was declined |
| `resource` | `resource_not_accessible`, `resource_missing` | **Change property** | The credential is fine; what it points at is not |
| `transient` | `resource_unavailable`, `google_api_error` | **Test connection** | Nothing is broken; the last attempt did not land |

### 7.2 The action matrix

| Status | `last_error_code` | Primary | Also offered | Badge |
|---|---|---|---|---|
| `not_connected` | — | Connect | — | Not connected |
| `pending_authorization` | — | Restart authorization | — | Connecting |
| `awaiting_resource_selection` | *(none)* | Select property | Disconnect | Select a property |
| `awaiting_resource_selection` | `resource` class | **Change property** | Test connection, Disconnect | Select a property + error note |
| `awaiting_resource_selection` | `transient` class | **Test connection** | Change property, Disconnect | Select a property + muted note |
| `awaiting_resource_selection` | `credential` class | **Reconnect** | Disconnect | Select a property + error note |
| `connected` | *(none)* | Test connection | Change property, Disconnect | Connected |
| `connected` | `transient` class | Test connection | Change property, Disconnect | Connected + **muted note** |
| `error` | `resource` class | **Change property** | Test connection, Disconnect | Error |
| `error` | `authorization` class | **Try again** | Disconnect | Error |
| `error` | `credential` class | **Reconnect** | Disconnect | Error |
| `error` | unknown / empty | Try again | Disconnect | Error |
| `reauth_required` | any | **Reconnect** | Disconnect | Reauthorization required |
| `disconnected` | — | Connect | — | Disconnected |

Three rules the matrix encodes, each of which was wrong in the first draft:

1. **A `resource`-class error never offers an OAuth action as its primary.**
   Re-authorizing a healthy credential is busywork that looks like a fix.
2. **A transient failure on a `connected` integration offers no destructive
   recovery.** The badge stays green, the note is muted, and the only action
   suggested is checking again. Nothing is broken.
3. **An unknown error code falls back to the state's safe default** rather than
   guessing. A new backend code that nobody mapped yet degrades to "Try again",
   never to a wrong-but-confident action, and never to a blank card.

### 7.3 Shape in code

```ts
type RecoveryClass = "credential" | "authorization" | "resource" | "transient";

/** Provider-neutral, and the only place a code is interpreted. */
const RECOVERY_CLASS: Record<string, RecoveryClass> = { … };

interface StatusPresentation {
  label: string;
  variant: BadgeVariant;
  needsAttention: boolean;
  actionLabel: string | null;              // authorization action, or none
  resourceAction: "select" | "change" | null;
  canTestConnection: boolean;
  canDisconnect: boolean;
  note: string | null;
}

function presentationFor(
  status: IntegrationStatus,
  errorCode: string,
): StatusPresentation;
```

`presentationFor` is the single entry point; components never read a status or
an error code themselves, exactly as `statusPresentation` established in M2.
Two gates still apply on top, both from data the entry already carries and
neither naming a provider: `resourceAction` requires
`entry.supports_resource_selection`, and `canTestConnection` additionally
requires a selected resource.

**Drift guard.** The frontend map and the backend taxonomy can fall out of step
silently. §14 pins both ends: a frontend test enumerates every code the map
handles, and a backend test asserts that the set of error codes that can persist
on a connection is exactly that set. Adding a code to the taxonomy without
mapping it fails the backend test, which names the frontend file to update.

### 7.4 Components

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

### 7.5 Error and recovery states

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

### 8.1 Which event a completed authorization writes

M3 writes `INTEGRATION_AUTHORIZED` unconditionally. M6 makes the choice
explicit, because "authorized" and "reconnected" are now both reachable and
writing both would be the paired duplicate invariant 12 forbids.

**Exactly one event per completed authorization, chosen by `previous_status` —
never by whether a credential row happens to exist:**

| `previous_status` | Event | Reasoning |
|---|---|---|
| `reauth_required` | `INTEGRATION_RECONNECTED` | Repairing a live integration whose credential died |
| `error` | `INTEGRATION_RECONNECTED` | Repairing a live integration whose last authorization failed |
| `pending_authorization` (or the row was just created) | `INTEGRATION_AUTHORIZED` | A first authorization |
| `awaiting_resource_selection` | `INTEGRATION_AUTHORIZED` | Re-running an authorization that never finished; nothing was repaired |
| **`disconnected`** | **`INTEGRATION_AUTHORIZED`** | The user deliberately ended the integration. Authorizing again starts it, it does not repair it — the lifecycle ended and a new one began |
| `connected` | `INTEGRATION_RECONNECTED` | A voluntary re-authorization of a working integration |

Credential existence is deliberately **not** the discriminator. It is an
implementation detail that happens to correlate today — a disconnected
connection has no credential row — and it would give the wrong answer the moment
credential storage changed. Previous status is the lifecycle fact the event is
actually about.

This is a change to M3 behaviour on the repair paths, so it is called out in
§15 and covered by a test rather than left to be noticed.

Deliberately **not** audited: health checks. A user-triggered read that changes
nothing is not a security event, and auditing every click turns the log into
noise that hides the events that matter. When a health check *does* transition
the connection (to `error` or `reauth_required`), the status and
`last_error_code` on the row are the record — consistent with M4, where a
credential-refresh failure writes no event either.

An idempotent disconnect (already disconnected) writes **no** event, for the
same reason: nothing happened.

---

## 9. Idempotency, concurrency, and the stale-result fence

### 9.1 Idempotency

| Operation | Idempotent? | How |
|---|---|---|
| Health check | Yes, apart from timestamps | Pure re-verification; no resource state is written |
| Disconnect | Yes, for connection state and audit — **but never a no-op** | Already `disconnected` → 200, no connection fields written and no audit row, **but outstanding authorization requests are still invalidated** (see below) |
| Change resource | Yes for the same id | Existing M4 behaviour, unchanged |
| Reconnect | Yes | Each attempt is its own single-use OAuth state; M3 supersedes older unconsumed requests, and §9.4 supersedes ones already in flight |

**Disconnect is idempotent, not inert.** A connection can be `DISCONNECTED`
*while a new authorization is in flight*, because `start_authorization`
deliberately preserves durable status (M3). So "already disconnected → do
nothing" would leave a live authorization attempt able to complete against an
integration the user has just told us, again, to switch off.

Every disconnect therefore invalidates outstanding authorization requests for
that (project, provider), whether or not it changes the connection:

| Aspect | Second disconnect on an already-disconnected integration |
|---|---|
| Connection fields | Not written |
| Credential | Already gone; nothing to delete |
| Audit event | **Not** written — nothing happened to the integration |
| Outstanding authorization requests | **Consumed** — this is the part that is not idempotent-as-no-op |
| Response | 200 with the current entry |

### 9.2 The problem the row lock does not solve

Every M6 operation has the same shape: read state, call Google, write a result.
`select_for_update` taken **after** the outbound call prevents torn writes, and
that is all it prevents. It does nothing about a result that was *computed
against state that has since changed*. The lock happily commits a stale answer,
consistently.

Three races make this concrete, and all three are reachable in normal use:

| | Sequence | Wrong outcome without a fence |
|---|---|---|
| **A** | Health check reads credential A → reconnect stores credential B → the old check returns 401 | A freshly repaired connection is knocked back to `reauth_required` by a 401 about a credential that no longer exists |
| **B** | Health check starts against resource A → user changes to resource B → the old check returns 403 for A | A working connection to B is marked `error`, citing a resource it no longer points at |
| **C** | Reconnect callback is in flight → user disconnects → the callback lands | Credentials are recreated and an explicitly disconnected connection is resurrected |

A and B are one problem: an outbound result must only be applied to the state it
was computed against. C is a different problem — an authorization *is* the
user's intent, so it is not stale merely because time passed; it is invalid
because a later, more explicit intent (disconnect) superseded it.

### 9.3 The fence for A and B — an optimistic-concurrency snapshot

Captured **immediately before the outbound call**, and — crucially — *after* any
token refresh the operation performs, so an operation never fences out its own
refresh:

```python
@dataclass(frozen=True)
class Fence:
    """What the outbound result will be about."""
    connection_updated_at: datetime
    external_resource_id: str
    credential_updated_at: datetime | None   # None when no credential is stored
```

At write time, inside the transaction, after `select_for_update`:

```python
current = Fence.capture(reread_connection)
if current != snapshot:
    return DISCARD          # write nothing at all, not even a timestamp
```

**Discard means discard.** Not `last_health_check_at`, not an error code,
nothing. A stale result has no claim on any field, and writing "we checked at
T" from a check about superseded state would be its own small lie. The operation
returns the connection's *current* entry, which is the truthful answer to "what
is the state now".

Why these three fields:

| Field | Catches |
|---|---|
| `connection.updated_at` | Every connection mutation — reconnect, resource change, disconnect, another health check. `auto_now`, and every existing save already lists it in `update_fields` (verified across `oauth_service`, `resource_service` and `credentials`), so it already moves on every write |
| `external_resource_id` | Race B directly and explicitly, rather than inferring it from a timestamp |
| `credential.updated_at` | Race A directly. A reconnect **reuses** the credential row rather than replacing it, so the primary key is not a discriminator — the timestamp is. `None` covers "the credential was deleted by a disconnect" |

The last two are redundant with the first *if* the `updated_at` convention holds
everywhere. They are in the fence because that convention is a convention: one
future `save()` that forgets `updated_at` would silently disable the whole
fence, and these two fields keep the specific races covered even then. §14 adds
a test that asserts the convention directly, so the redundancy is a backstop
rather than the plan.

### 9.3.1 The refresh is itself a fenced operation

§9.3 says to capture the snapshot *after* any token refresh, so an operation
does not fence out its own refresh. That instruction is right and insufficient:
**`access_token_for` is itself an outbound, state-mutating operation**, and by
the time the caller captures its snapshot the damage is already committed.

```python
access_token_for(connection):
    ...refresh call to Google...          # outbound, slow, blockable
    on success        -> _persist(credential, refreshed)      # WRITE
    on invalid_grant  -> mark_reauth_required(connection)     # WRITE
```

Two races, and the second is the more dangerous because it looks like success:

> **A1 — stale `invalid_grant`.** Health check refreshes with token A → the call
> is in flight → a reconnect stores credential B and the connection is repaired
> → A's refresh returns `invalid_grant` → `mark_reauth_required` writes
> `reauth_required` over a connection that is working perfectly.

> **A2 — stale success.** A's refresh is in flight → a reconnect stores
> credential B → A's refresh *succeeds* → `_persist` overwrites B's access
> token and expiry with ones derived from the superseded refresh token A. The
> connection now holds a credential nobody asked for, and B's rotation is lost.

Neither is reachable by the caller's fence, because both writes happen inside
`access_token_for` before the caller ever takes a snapshot.

**So the refresh gets its own optimistic concurrency**, the same shape as §9.3
and one level down:

```python
@dataclass(frozen=True)
class RefreshFence:
    """The credential state this refresh is derived from."""
    credential_id: int | None        # None once a disconnect deleted it
    credential_updated_at: datetime | None
```

```
1. read the credential; if the access token is still usable, return it
2. capture RefreshFence
3. call Google  — no database lock held across the network
4. open a transaction, select_for_update the connection, re-read the credential
5. if RefreshFence changed:
       DISCARD.  Persist nothing.  Do not mark_reauth_required, whatever
       Google said — that verdict belongs to a credential that no longer
       exists.  Then:
         - if the current stored token is usable, return it
         - else retry from step 1, at most once
         - if the retry is also superseded, raise ResourceUnavailable
6. else: apply the result — persist on success, mark_reauth_required on
   invalid_grant — and return
```

Three points this turns on:

- **`invalid_grant` is a verdict about a specific refresh token, not about the
  connection.** Once that token has been replaced, the verdict is
  meaningless and must not be applied to its replacement. This is the whole of
  race A1.
- **The retry is bounded at one.** State churning under us twice in a row is a
  reason to report a transient failure, not to loop. `ResourceUnavailable` is
  the honest code: nothing is proven broken.
- **`credential_id` is in the fence alongside the timestamp** because disconnect
  *deletes* the row. A refresh that returns after a disconnect finds no
  credential, which is a fence mismatch and a discard — not a crash, and not a
  resurrection.

Only once the refresh has **committed** does the caller capture its §9.3
snapshot, which is what makes that instruction meaningful rather than a hope.

**`google/credentials.py` therefore changes.** §10 lists it as modified; the
previous revision was wrong to call it unchanged.

### 9.4 The authorization finalization fence — a generation, not a timestamp

The first draft fenced Race C by having disconnect consume outstanding
authorization requests. That is necessary but not sufficient, because the
callback consumes its request *before* the token exchange:

```
_consume_request()          # consumed_at committed, transaction closed
  ...token exchange with Google...   # seconds, and blockable
_store_credentials(); status = …     # the write
```

Once consumed, disconnect can no longer see it. Two orderings slip through:

> **Race C′** — callback consumes its request → token exchange is slow → user
> disconnects (finds nothing unconsumed) → callback resumes and writes
> credentials → the explicitly disconnected connection is resurrected.

> **Race D** — callback R1 consumes its request and begins its exchange → the
> user starts R2 → R2 completes → R1 returns late and overwrites R2's
> credentials and state with older ones.

Revision 3 answered both with timestamp comparisons. **That answer was wrong,
and the review is right about why.** Both comparisons were *orderings*, not
equalities, and §9.3's argument — that the fence needs no monotonic clock
because it only ever tests equality — did not extend to them:

- `created_at > request.created_at` compares two rows in the same table, so a
  tie is at least resolvable by adding `id`. Survivable, but still an ordering.
- `connection.updated_at > request.created_at` compares **two different tables**.
  Equal timestamps are genuinely ambiguous: nothing in the data says whether a
  disconnect stamped at exactly T happened before or after an authorization
  request stamped at exactly T. No amount of tie-breaking within one row fixes a
  comparison across two.

This is precisely the escape hatch the previous revision named, and the
condition for taking it has been met.

#### The decision: `lifecycle_generation`

**M6 introduces a generation counter and a migration.** Both fence checks become
integer equality, exactly like §9.3.

| Model | Field | Meaning |
|---|---|---|
| `IntegrationConnection` | `lifecycle_generation` (`PositiveIntegerField`, default `0`) | Bumped whenever the connection's authorization intent changes |
| `OAuthAuthorizationRequest` | `connection_generation` (`PositiveIntegerField`, default `0`) | The generation this attempt was started against |

**It increments in exactly two places**, both of which are "the user expressed a
new intent for this integration":

1. **`start_authorization`** — a new attempt supersedes any older one.
2. **`disconnect`** — an explicit end supersedes any attempt in flight.

**Finalization is one comparison:**

```python
if connection.lifecycle_generation != request.connection_generation:
    raise InvalidState        # superseded; write nothing
```

Walk the four cases, and note that every one is an equality test with no clock
involved:

| Scenario | Generations | Outcome |
|---|---|---|
| Ordinary authorization | start bumps 0→1, request captures 1; callback sees 1 | `1 == 1` → **proceed** |
| **C′** — disconnect mid-flight | request captures 1; disconnect bumps to 2; callback sees 2 | `2 ≠ 1` → **discard** |
| **D** — newer attempt wins | R1 captures 1; R2 bumps to 2 and captures 2; R1's callback sees 2 | R1 `2 ≠ 1` → **discard**; R2 `2 == 2` → proceed |
| **Connect after disconnect** (must work) | disconnect bumps to 2; start bumps to 3 and captures 3; callback sees 3 | `3 == 3` → **proceed** |

The fourth row is the one that killed every simpler rule: a legitimate
Connect-after-disconnect still has `status == DISCONNECTED` when the callback
lands, because `start_authorization` deliberately preserves durable status (M3).
A generation distinguishes it from Race C′ without consulting status or a clock
at all.

#### Consequences worth stating

- **`start_authorization` now writes the connection row**, which it does not do
  today for an existing connection. It writes exactly two fields —
  `lifecycle_generation` and `updated_at` — and touches neither `status` nor any
  resource field, so M3's "do not destroy durable state on start" property
  holds.
- That write bumps `connection.updated_at`, so an in-flight **health check** is
  fenced out by §9.3 when a user starts an authorization. Correct, and cheap:
  a discarded health result costs nothing.
- The **consume-on-disconnect** behaviour from revision 3 is *kept*. It stops a
  not-yet-started callback earlier and more cheaply, at consumption rather than
  finalization, and it is what makes an idempotent disconnect non-inert (§9.1).
  Generation is the backstop for the window consumption cannot see.
- Scoping is settled by construction: the generation lives on the connection,
  so the newest intent for **the connection** wins regardless of which member
  started it. No separate per-user reasoning is needed.

### 9.5 Race resolution table

| Race | Fenced by | Comparison | Result |
|---|---|---|---|
| **A1 — stale `invalid_grant` after reconnect** | `RefreshFence` (§9.3.1) | equality | Discarded inside the refresh; `reauth_required` never written |
| **A2 — stale *successful* refresh after reconnect** | `RefreshFence` (§9.3.1) | equality | Discarded; the newer credential is not overwritten |
| A — stale 401 from the *provider* after reconnect | `credential_updated_at` | equality | Discarded; connection stays `connected` |
| B — stale 403 after resource change | `external_resource_id` | equality | Discarded; connection stays `connected` on the new resource |
| C — disconnect **before** the callback consumes its request | `consumed_at` set by disconnect | n/a | `InvalidState` at consumption; nothing written |
| **C′ — disconnect *after* consumption, callback still running** | `lifecycle_generation` (§9.4) | **equality** | Discarded at finalization; no credential, no resurrection |
| **D — older callback returns after a newer authorization completed** | `lifecycle_generation` (§9.4) | **equality** | Discarded; the newer attempt's state survives |
| Two concurrent health checks | `connection_updated_at` | equality | The later one discards |
| Two concurrent resource changes | Row lock, then fence | equality | Serialized; the second discards |
| Health check racing a resource change it started before | `external_resource_id` | equality | Discarded |
| Legitimate Connect **after** a disconnect | `lifecycle_generation` matches | **equality** | **Allowed** — the case that ruled out every status- or clock-based rule |

Every row is now an equality test. No fence in this design compares two
timestamps for ordering, and none depends on a monotonic clock.

### 9.6 Why the fences use what they use

Two different mechanisms, chosen for two different situations, and it is worth
being explicit that this is not inconsistency.

**§9.3 and §9.3.1 use `updated_at` snapshots.** The comparison is equality, made
inside one transaction, on rows just re-read under a lock. Ordering, monotonicity
and clock skew never enter, so a dedicated version column would add a migration
and buy nothing. The one real weakness — a future `save()` that omits
`updated_at` — is covered by redundant fields in the snapshot and by a direct
test (§14).

**§9.4 uses an explicit generation.** Here a snapshot could not work, because
the question spans two tables and two lifetimes: *did an explicit end-of-life
happen after this attempt began?* There is no pair of existing columns whose
equality answers that, and the ordering comparison that seemed to was ambiguous
on a tie. A counter that only the two intent-changing operations increment
answers it exactly, in one integer comparison.

**"No migration" is no longer a goal of this design.** It was a happy property
of M4 and M5 because those milestones genuinely needed no new state. M6 needs
one piece of state that no existing column expresses, and inventing a fragile
proxy for it would be the wrong trade. The migration is small, additive,
defaulted, and reversible (§11).

## 10. Backend service boundaries

```
integrations/
  lifecycle_service.py   NEW  health_check() and disconnect()
  resource_service.py    MODIFIED  guard lifted; ERROR admitted; shared result mapper
  oauth_service.py       MODIFIED  terminal status preserves a valid selection (§5.1)
  google/errors.py       MODIFIED  ResourceChangeNotSupported retired; one code added (§12)
  google/credentials.py  MODIFIED  the refresh becomes a fenced operation (§9.3.1)
  models.py              MODIFIED  lifecycle_generation, connection_generation (§9.4)
  migrations/0003_…      NEW       two additive columns (§11)
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

The second most important boundary decision: **the refresh fence lives in
`credentials.py`, not in its callers.** Every caller would otherwise need to
know that obtaining a token can mutate two tables, and each would implement the
same discard logic slightly differently. One fence, at the boundary that owns
the credential, is the only version of this that stays correct.

---

## 11. Migration impact

**One migration, and it is required.** M4 and M5 needed none; M6 needs one piece
of state no existing column expresses (§9.4, §9.6). Preserving a
"no migration" streak by substituting a fragile proxy would be the wrong trade.

### 11.1 The migration

```python
# integrations/migrations/0003_lifecycle_generation.py
migrations.AddField(
    model_name="integrationconnection",
    name="lifecycle_generation",
    field=models.PositiveIntegerField(default=0),
)
migrations.AddField(
    model_name="oauthauthorizationrequest",
    name="connection_generation",
    field=models.PositiveIntegerField(default=0),
)
```

Two additive, defaulted, non-null integer columns. No data migration, no
backfill, no index, no constraint, nothing dropped or renamed.

### 11.2 Why it is safe to deploy

- **Additive with a constant default.** PostgreSQL 11+ adds such a column
  without rewriting the table, so the lock is brief even if the table were
  large — and here it holds a handful of rows.
- **Existing rows get `0` on both sides.** An authorization already in flight
  across the deploy captured no generation, so its request row defaults to `0`
  and the connection defaults to `0`: `0 == 0`, and the callback completes
  normally instead of being spuriously discarded. That is the correct outcome —
  the deploy is not a lifecycle event and must not invalidate a user's
  in-progress consent.
- **Ordering is the usual one**, already automated: the API container's
  entrypoint runs `migrate` before Gunicorn starts, so the columns exist before
  any code reads them.
- **Reversible.** `RemoveField` is generated automatically and loses only the
  counters, which are meaningful solely to code that is being rolled back
  anyway (§16).

### 11.3 Still rejected as unnecessary

| Candidate | Why not |
|---|---|
| `disconnected_at` | The audit event carries who and when; a second copy could disagree — and the generation already answers the question that timestamp was wanted for |
| `health_check_count` / failure streaks | Nothing in V1 consumes it; that is retry policy, and V1 has no scheduler |
| A separate `last_transient_error` | `last_error_code` plus the status already distinguishes the classes (§4.3) |
| A version column for the §9.3 fence | That fence tests equality inside one transaction; `updated_at` already does the job (§9.6) |
| `google_account_email` backfill | Would need an identity scope; §0 explains why it is wanted and §13 why it is still deferred |

Beyond the two columns above, implementation stops and returns here rather than
adding schema.

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
`connected`, same resource, no re-pick; with no stored resource →
`awaiting_resource_selection` (M3 behaviour intact); a token response without a
refresh token leaves the stored one intact; a missing scope → `error`
`scope_not_granted`; one `INTEGRATION_RECONNECTED` row.

**Reconnect verification outcomes (§5.1.1)** — one test per row of that matrix,
asserting status, retained resource, error code **and** the resulting primary
action together, so a transient blip can never be shown to the user as a lost
property.

**Forced consent (§5.3.1–5.3.2)** — one test per row of the capability table,
asserted by inspecting the built authorization URL as M3 already does.
`prompt=consent` **is** sent when no connection row exists, when no credential
is stored, when the stored refresh token is empty, from `DISCONNECTED`, from
`REAUTH_REQUIRED`, and from `ERROR` + `no_refresh_token`. It is **not** sent
from `CONNECTED`, from `AWAITING_RESOURCE_SELECTION` with a stored refresh
token, or from `ERROR` with any other code while the credential is intact.

The case that motivates the change gets its own test: a **brand-new connection
in a second project**, where a credential for the same provider already exists
in another project, still forces consent — because the predicate reads *this*
connection's credential, not the database at large.

**The disconnect → reconnect round trip** — disconnect, then authorize, with the
token response carrying a **new** refresh token, then the remembered resource
re-verifies: the connection reaches `connected` on the same resource, the new
refresh token is stored, and the audit row is `integration.authorized` (not
`reconnected`, per §8.1). This is the path §5.3.1 exists to make work on the
first attempt.

**Cancellation is non-destructive (§5.4)** — denial of an authorization on an
integration that already has a credential leaves status, credential and resource
untouched and records `access_denied`; denial of a first authorization removes
the row; neither produces `error`. `ScopeNotGranted` still does produce `error`,
asserted separately so the two are never conflated.

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

**Concurrency — the three named races (§9.2), each asserted directly.** The
outbound call is stubbed to mutate the row before it returns, which reproduces
the interleaving deterministically without threads:

- *Race A* — a health check whose 401 arrives after a reconnect stored a new
  credential: the connection stays `connected`, `last_error_code` stays empty,
  and `last_health_check_at` is **not** written.
- *Race B* — a health check whose 403 for resource A arrives after the user
  changed to resource B: the connection stays `connected` on B, with B's label
  and metadata intact.
- *Race C* — a disconnect **before** the callback consumes its request: the
  callback redirects with `invalid_state`, **no** `IntegrationCredential` row is
  created, and the connection is still `disconnected`. Plus the direct unit
  assertion that disconnect marks outstanding authorization requests consumed.
- *Race A1 — stale `invalid_grant`* (§9.3.1): the refresh call is stubbed to
  perform a reconnect (storing a new credential) before returning
  `invalid_grant`. Assert the connection is **still `connected`**,
  `last_error_code` is empty, and the newer credential is intact — the verdict
  about the superseded token is never applied.
- *Race A2 — stale successful refresh* (§9.3.1): the refresh call is stubbed to
  perform a reconnect before returning **success**. Assert the stored access
  token and expiry are the **reconnect's**, not the stale refresh's, and that
  the stale refresh token was not written back.
- *Refresh retry budget*: a fence mismatch whose re-read yields a usable token
  returns it without a second Google call; a mismatch twice in a row raises
  `ResourceUnavailable` rather than looping.
- *Race C′* — the ordering the first draft missed, reproduced explicitly:
  **consume the request, then disconnect, then let the callback resume**. The
  token exchange is stubbed to perform the disconnect before returning, so the
  interleaving is deterministic. Assert no `IntegrationCredential` row exists,
  the status is still `disconnected`, `granted_scopes` is unchanged, and no
  `INTEGRATION_AUTHORIZED` or `INTEGRATION_RECONNECTED` event was written.
- *Race D* — R1 consumes its request and begins its exchange; R2 is started and
  completes; R1 then returns. Assert the stored credential is **R2's**, the
  status is the one R2 produced, and R1 wrote nothing. Deterministic via the
  same stub-performs-the-interleaving technique.
- *The legitimate case the fence must not break* — disconnect, then Connect,
  then complete the callback: it **succeeds**, because `start_authorization`
  captured the post-disconnect generation. This is the counterpart that stops
  the C′ fence from being implemented as "status is disconnected → always
  discard".

**Tie cases — the reason the fence is a generation and not a clock.** Each of
these runs with time frozen so every row carries an **identical** timestamp,
and each must still resolve correctly:

- Disconnect and an authorization request created at the same frozen instant,
  disconnect second: the callback is **discarded** (generations differ), where a
  `updated_at > created_at` comparison would have allowed it.
- Disconnect and an authorization request at the same frozen instant, disconnect
  **first**: the callback **succeeds**, where the same comparison would also
  have allowed it — asserting the fence is not merely stricter but *correct* in
  both directions.
- R1 and R2 created at the same frozen instant: exactly one succeeds, and it is
  R2 — the one whose generation the connection holds.
- A generation counter test proving the increment happens in exactly the two
  places §9.4 names, and nowhere else: a health check, a resource change and a
  repeat disconnect on an already-disconnected connection all leave
  `lifecycle_generation` unchanged.
- Two concurrent health checks: the later result discards.
- A discarded result writes **nothing at all** — asserted field by field, since
  "discard but still stamp the timestamp" is the tempting wrong implementation.

**The fence convention** — a test asserts that every service function which
mutates `IntegrationConnection` or `IntegrationCredential` leaves `updated_at`
**different from** what it was. Deliberately not "strictly greater": §9.6's
whole argument is that the fence compares for *equality* and therefore needs no
monotonic clock, and a test demanding wall-clock ordering would quietly assert a
property the design says it does not rely on — and could fail on a clock
adjustment for reasons that have nothing to do with the fence.

**Migration** — `makemigrations --check` is clean after the two `AddField`s;
existing rows default to `0` on both sides; and an authorization request created
**before** the migration (generation `0`) completes successfully against a
connection at generation `0`, which is the in-flight-across-deploy case from
§11.2.

**Recovery classes (§7.1)** — a backend test asserts the set of error codes that
can persist on a connection equals the set the frontend map handles, and names
`lib/integrations/status.ts` in its failure message.

**Leakage** — no token, refresh token or Google error text in any response, log
line or audit row, for every new path.

**Tenancy** — both new endpoints: foreign project 404, unknown provider 404,
unauthenticated 403.

**Mutation checks before hand-off** — make a transient failure clear
`last_successful_check_at`; let disconnect blank the credential instead of
deleting the row; let a failed reconnect verification set `connected`; add a
revoke call; **remove each of the three fence fields in turn**; let a discarded
result still write `last_health_check_at`; **stop disconnect from consuming
outstanding authorization requests**; **remove `DISCONNECTED` from
`_needs_forced_consent`**; **make `_needs_forced_consent` return False when no
credential is stored**; **remove the §9.3.1 refresh fence**, then remove only
its `invalid_grant` arm, then only its success arm; **stop
`start_authorization` from incrementing the generation**, then stop
`disconnect` from incrementing it; **compare generations with `>` instead of
`!=`**; **collapse the §5.1.1 transient outcome into
`resource_not_accessible`**; make denial set `error`; and make `error` always
offer the authorization action regardless of error class. Each must turn the
suite red.

Frontend — **the §7.2 matrix asserted row by row**, `status` ×
`last_error_code`, including that a `resource`-class error offers Change
property as primary and *not* an authorization action, that an unknown code
falls back safely, and that a `transient` error on `connected` renders a muted
note rather than a destructive alert; no test button without a selected
resource; the disconnect dialog states that access is not revoked at Google and
that revoking there affects every integration sharing the authorization; the
recovery map enumerated for the drift guard; no provider-specific branching in
any shared component (the existing source-scan test extended to the new
components).

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

### Two M3 behaviours M6 changes deliberately

Called out here rather than discovered in a diff. Both are in
`complete_authorization`, both are covered by new tests, and neither weakens an
invariant above:

17. It no longer writes `INTEGRATION_AUTHORIZED` unconditionally — which event
    is written now depends on `previous_status` (§8.1). Invariant 12 still
    holds: exactly one event per completed authorization.
18. It no longer ends unconditionally in `AWAITING_RESOURCE_SELECTION` — a
    still-valid selection is preserved by re-verifying it (§5.1). Invariant 6
    still holds: `connected` is reached only via a live successful call.

---

## 16. Rollback

M6 carries a migration, so rollback has an ordering rule it did not have in M4
or M5. The rule is the ordinary one, stated so nobody has to derive it under
pressure:

**Revert the code first; the columns can stay.** `git revert` of the merge
commit plus a redeploy restores M5 behaviour completely. The two generation
columns are simply unread by M5 code — an unused integer with a default harms
nothing, and leaving them in place keeps the rollback a single, fast step.

**Only drop the columns if the rollback is permanent**, and only after the
reverted code is running. `RemoveField` in both directions is generated by
Django; dropping them while M6 code is still live would break every
authorization.

What survives a rollback, and is safe under M5 code:

- Connections that reached `disconnected` stay `disconnected` with no
  credential. M5 renders that as "Disconnected" and offers Connect — correct,
  and a state it already knows.
- Connections whose resource was *changed* keep the new resource. M5 treats it
  as the selection it is.
- A connection left in `awaiting_resource_selection` with a retained resource id
  (§5.2) renders under M5 as "Select a property" with the old property shown.
  Slightly odd, harmless, and one selection fixes it.
- Non-zero `lifecycle_generation` values are ignored by M5 code. If M6 is later
  re-applied, counters resume from where they were, which is fine: the fence
  only ever compares a captured value against the current one.

Nothing M6 writes is unreadable by M5 code, which is what makes the revert safe
rather than merely possible.

---

## 17. Staging acceptance checklist

Preconditions: staging on merged `main`, both containers healthy, no `.env`
change (M6 introduces no setting), **the `0003_lifecycle_generation` migration
applied by the entrypoint on deploy** (§11.2), and the two live connections from
M4/M5 —
GA4 on `properties/549483499` (*poolino*) and Search Console on
`sc-domain:poolinogroup.com` (`siteFullUser`).

**Ordering matters in this checklist.** Phases 1–4 are non-destructive and use
the real account freely. Phase 5 revokes a combined authorization and is
deliberately last, because it degrades **every** integration sharing it.

### Phase 1 — regression

1. Both integrations still read **Connected** with their existing resources,
   unchanged, before any M6 action.

### Phase 2 — health check

2. **Test connection** on GA4 → stays Connected; `last_health_check_at` and
   `last_successful_check_at` both advance.
3. Same for Search Console.
4. No audit row is written by either check.

### Phase 3 — change property

5. GA4: change to a different property → Connected on the new one; id, label
   and metadata all change together; one `integration.resource_selected` row.
6. Change back to `properties/549483499` → Connected, *poolino* restored.
7. Search Console: change to a different verified site and back.
8. Attempt a change to an inaccessible identifier → refused, and the existing
   resource is **completely unchanged** (id, label, metadata, both timestamps).

### Phase 4 — disconnect and reconnect (no revocation)

This phase tests **disconnect**, which deletes stored credentials and does
**not** revoke (§0). That is exactly why GA4 is expected to survive it.

9. **Disconnect** Search Console → status `disconnected`; the
   `integrations_integrationcredential` row for it is **gone** (`SELECT count(*)`
   = 0 for that connection); the resource id and label remain;
   `last_successful_check_at` remains.
10. Exactly one `integration.disconnected` row.
11. **GA4 is still Connected with its credential intact.** This is the check
    that disconnect is correctly scoped — valid precisely because nothing was
    revoked.
12. Disconnect again → no change, no second audit row.
13. Reconnect Search Console → Connected on the remembered site, with **no
    re-picking**. Audit records `integration.authorized`, not
    `integration.reconnected`, because the previous status was `disconnected`
    (§8.1).

### Phase 5 — DESTRUCTIVE: grant revocation, run last

> **Read before starting.** Revoking at
> `https://myaccount.google.com/permissions` revokes the **combined
> authorization for this Google account and API project**, not one integration.
> Expect **both** GA4 and Search Console to degrade. Do not run this phase
> unless you are prepared to reconnect both. A disposable Google account is the
> better option if one is available; with the real account, budget for the full
> restoration in steps 18–20.

14. Record the current state of both integrations first, so the blast radius can
    be stated rather than guessed.
15. Revoke access at `https://myaccount.google.com/permissions`.
16. **Test connection** on GA4 → `Reauthorization required`, primary action
    **Reconnect** (§7.2, `credential` class).
17. **Test connection** on Search Console → also `Reauthorization required`.
    **This is the expected blast radius**, and confirms §0's reasoning against
    revoking on disconnect. Record it as an observed result, not an anomaly.
18. **Reconnect GA4** → Google consent → returns to **Connected on
    `properties/549483499` (*poolino*) without re-picking**. This is the
    milestone's headline behaviour.
19. **Reconnect Search Console** → Connected on `sc-domain:poolinogroup.com`.
20. Both integrations verified Connected again, with refresh tokens present and
    Fernet-encrypted. One `integration.reconnected` row each (previous status
    was `reauth_required`, §8.1).

**Do not report "GA4 unaffected" anywhere in this phase.** After a grant-wide
revoke GA4 *is* affected; the only true statement is that it was restored in
step 18. The "unaffected" claim belongs to step 11 and to disconnect alone.

### Phase 6 — security and integrity

21. API log leak check over the whole session returns **0** for `ya29.`,
    `1//`, `client_secret`, `"access_token"`, `"refresh_token"`.
22. Both credentials Fernet-encrypted; no plaintext tokens in the database.
23. The migration applied cleanly on deploy (`0003_lifecycle_generation` in
    `showmigrations`), and `makemigrations --check` reports no further changes.
24. Both pre-existing connections still work after the migration — they were
    created before `lifecycle_generation` existed and default to `0`, which is
    the §11.2 case, and Phase 1 already proved it before any M6 action.

**Optional observation** — with a spare GA4 property, delete it in Google and
run **Test connection**, to record what `properties.get` actually returns for a
trashed property (§2). Not an acceptance gate; a fact worth having.

**Concurrency races are not staged.** A and B (§9.2) need two requests
interleaved at sub-second precision, which a browser cannot reliably produce;
they are covered by the automated tests in §14 and by mutation checks. Claiming
them as staging-verified would be false. Race C is observable by hand if wanted:
start a reconnect, disconnect in a second tab before completing Google consent,
then finish consent — the callback must land on the projects page with an
`invalid_state` error and the integration must remain `disconnected`.

## 18. Hand-off

M6 completes the V1 integration lifecycle. What remains after it is Milestone 7
— production deployment, backups, monitoring and CI — and nothing in this design
anticipates or blocks it.

Explicitly still out of scope, and still out of V1: scheduled or background
health checks, retry policies, notifications, any analytics data, and
grant-wide revocation (§13).
