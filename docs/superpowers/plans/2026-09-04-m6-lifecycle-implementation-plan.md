# Milestone 6 — implementation plan

Plan only. No production code is written until this plan is approved.

**Source of truth:** the approved M6 design at commit
`2d33a6c65e8027a248f2fd38ca6fddd0eb4cfb0c`
(`docs/superpowers/specs/2026-09-04-m6-lifecycle-design.md`). Section references
below (§n) are to that document. Where this plan and the design disagree, the
design wins and the disagreement is a defect in this plan.

**Base:** `main` at `8eb3ffd` (Milestone 5). Branch:
`claude/m6-lifecycle-implementation`.

**Method:** strict TDD. Every task writes failing tests first, runs them to
observe the *expected* failure, then implements the minimum that makes them
pass. A task is not complete until its verification commands are green and the
full suite is green.

---

## 0. Ground rules for the whole milestone

1. **No production code before a failing test.** Each task states the exact
   command and the exact failure to expect. An unexpected failure mode (an
   import error where an assertion was expected) means the test is wrong —
   fix the test before implementing.
2. **The three fences stay three mechanisms** (§9.6). They are never merged,
   never share a dataclass, and never share a helper:

   | Mechanism | Question it answers | Where | Compares |
   |---|---|---|---|
   | `lifecycle_generation` | Is this authorization still the current *intent*? | `concurrency.advance_generation`, checked in the callback | integer equality |
   | `Fence` | Is this provider result about the state it was computed from? | captured by lifecycle operations before an outbound call | `updated_at` / resource id / credential `updated_at` equality |
   | `RefreshFence` | Is this refresh result about the credential it was derived from? | inside `google/credentials.py` | credential id + `updated_at` equality |

3. **No provider-specific branching in shared lifecycle code.** `ga4.py`,
   `search_console.py` and `resources.py` are not modified by any task. The
   existing `test_provider_boundary.py` source scan is extended, not relaxed.
4. **Exactly one migration**, containing exactly the two approved fields. If a
   second migration appears necessary, **stop and report** rather than adding
   it (§11.3).
5. **No scope beyond M6.** No scheduled health checks, ingestion, analytics,
   notifications, or grant-wide revocation. A test asserts no request is ever
   made to the revoke endpoint.
6. **M3/M4/M5 invariant tests** (§15) may change only where a symbol was
   renamed, never where a value is asserted — the M5 rule, restated. The two
   deliberate M3 behaviour changes (§15 items 17–18) are the only exceptions,
   and each is covered by a new test in the task that makes it.

### Standing commands

```bash
# Postgres is not always running in this container.
service postgresql start

# Backend, one file
cd apps/api && ../../.venv/bin/python -m pytest tests/<file> -q

# Backend, full suite
cd apps/api && ../../.venv/bin/python -m pytest -q

# Migrations must stay at exactly one new file
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run

# Frontend
cd apps/web && npm run test
cd apps/web && npx tsc --noEmit && npm run lint
cd apps/web && NODE_OPTIONS=--max-old-space-size=640 npm run build
```

### New modules this milestone introduces

| Path | Purpose |
|---|---|
| `apps/api/integrations/concurrency.py` | The generation primitives and the `Fence` dataclass. One module so the three mechanisms are visibly distinct and separately testable |
| `apps/api/integrations/lifecycle_service.py` | `health_check()` and `disconnect()` |
| `apps/api/integrations/migrations/0003_lifecycle_generation.py` | The one migration |
| `apps/api/tests/test_lifecycle.py` | Health check, disconnect, reconnect terminal behaviour |
| `apps/api/tests/test_concurrency.py` | Every race in §9.5 |
| `apps/web/components/integrations/test-connection-button.tsx` | Test connection |
| `apps/web/components/integrations/disconnect-dialog.tsx` | Disconnect confirmation |

---

## Task index

Foundations land before behaviour; behaviour lands before UI.

| # | Task | Commit prefix |
|---|---|---|
| T01 | Migration and model generation fields | `feat(integrations)` |
| T02 | Concurrency primitives; atomic generation bump on an existing row | `feat(integrations)` |
| T03 | First-connection creation race | `feat(integrations)` |
| T04 | Forced-consent predicate | `fix(integrations)` |
| T05 | Callback staging and the stage-3 generation fence | `refactor(integrations)` |
| T06 | Credential refresh fence | `fix(integrations)` |
| T07 | General stale-result fence | `feat(integrations)` |
| T08 | Health-check service and endpoint | `feat(integrations)` |
| T09 | Disconnect service and endpoint | `feat(integrations)` |
| T10 | Reconnect terminal behaviour | `feat(integrations)` |
| T11 | Change resource, and the error taxonomy | `feat(integrations)` |
| T12 | Audit event selection | `feat(integrations)` |
| T13 | Frontend recovery model | `feat(web)` |
| T14 | Test connection UI | `feat(web)` |
| T15 | Change property UI | `feat(web)` |
| T16 | Disconnect UI | `feat(web)` |
| T17 | Reconnect and recovery UI states | `feat(web)` |
| T18 | Concurrency race suite | `test(integrations)` |
| T19 | Security, audit and leakage tests | `test(integrations)` |
| T20 | Migration regression, mutation checks, staging handoff | `chore` |

---

## T01 — Migration and model generation fields

Implements §9.4, §11.1.

**Files**
- Modified: `apps/api/integrations/models.py`
- Created: `apps/api/integrations/migrations/0003_lifecycle_generation.py`
- Created: `apps/api/tests/test_concurrency.py` (first tests)

**Interfaces produced**
- `IntegrationConnection.lifecycle_generation: PositiveIntegerField(default=0)`
- `OAuthAuthorizationRequest.connection_generation: PositiveIntegerField(default=0)`

**Failing tests first** — in `tests/test_concurrency.py`:
- `test_new_connection_starts_at_generation_zero`
- `test_new_authorization_request_defaults_to_generation_zero`
- `test_migration_state_matches_models` — `makemigrations --check` exits 0

```bash
service postgresql start
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
```

**Expected failure:** `AttributeError` / `FieldError` — the fields do not exist.
An `ImportError` on `test_concurrency.py` means the file was written wrong.

**Minimal implementation**
1. Add both fields with `default=0`, each with a comment pointing at §9.4.
2. `python manage.py makemigrations integrations --name lifecycle_generation`
3. Read the generated migration and confirm it contains **exactly two
   `AddField` operations** and nothing else. Anything more → stop and report.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run   # "No changes detected"
cd apps/api && ../../.venv/bin/python -m pytest -q                                  # full suite still green
ls apps/api/integrations/migrations/                                                # exactly one new file
```

**Commit:** `feat(integrations): add lifecycle generation fields`

---

## T02 — Concurrency primitives; atomic generation bump on an existing row

Implements §9.4.1 (existing-row path). The creation path is T03.

**Files**
- Created: `apps/api/integrations/concurrency.py`
- Modified: `apps/api/integrations/oauth_service.py`
- Modified: `apps/api/tests/test_concurrency.py`

**Interfaces produced**
```python
# integrations/concurrency.py
def locked_connection(project, provider_key, *, user=None) -> IntegrationConnection
def advance_generation(connection) -> int      # bumps, saves, returns the new value

@dataclass(frozen=True)
class Fence:                                   # defined here, first used in T07
    connection_updated_at: datetime
    external_resource_id: str
    credential_updated_at: datetime | None
    @classmethod
    def capture(cls, connection) -> "Fence"
    def matches(self, connection) -> bool
```

**Interfaces consumed:** `IntegrationConnection`, `transaction.atomic`,
`select_for_update`.

**Failing tests first**
- `test_start_authorization_advances_the_generation`
- `test_request_carries_the_generation_it_was_assigned`
- `test_two_starts_on_an_existing_connection_get_distinct_generations`
- `test_connection_holds_the_newest_generation`
- `test_generation_advances_even_from_pending_authorization` (§9.4: every
  invocation, not only state changes)

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
```

**Expected failure:** `ModuleNotFoundError: integrations.concurrency`, then
assertion failures showing the generation stayed at its previous value.

**Minimal implementation**
1. `concurrency.py` with `locked_connection` (existing-row branch only, via
   `select_for_update().get(...)`) and `advance_generation`.
2. In `start_authorization`, wrap the existing body so the connection is
   obtained through `locked_connection`, `advance_generation` runs inside the
   same `transaction.atomic()`, and the `OAuthAuthorizationRequest` is created
   in that transaction carrying the returned value.
3. Do **not** use `F()` — §9.4.1 explains why the lock makes it unnecessary and
   the read-back hazardous.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py tests/test_oauth.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `feat(integrations): assign authorization generations under a row lock`

---

## T03 — First-connection creation race

Implements §9.4.1a. This is the case `select_for_update` cannot cover.

**Files**
- Modified: `apps/api/integrations/concurrency.py`
- Modified: `apps/api/tests/test_concurrency.py`

**Interfaces produced:** `locked_connection` gains its creation branch —
savepointed `create`, `IntegrityError` recovery via
`select_for_update().get(...)`. Signature unchanged.

**Failing tests first**
- `test_first_authorization_creates_the_connection_and_advances_generation`
- `test_creation_race_recovery_branch` — patch
  `IntegrationConnection.objects.create` to raise `IntegrityError` once; assert
  recovery selects the existing row, locks it, bumps, and creates a request
  carrying that value, and that `IntegrityError` never escapes
- `test_two_first_authorizations_race` — two threads under
  `@pytest.mark.django_db(transaction=True)`; assert **exactly one**
  `IntegrationConnection`, **distinct** request generations, connection holds
  the **higher**, only the matching request could finalize, neither call raised

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q -k creation or race
```

**Expected failure:** the recovery test fails with an uncaught `IntegrityError`
propagating out of `locked_connection`; the threaded test fails with either two
connection rows or a 500.

**Minimal implementation**
1. `filter(...).first()` under `select_for_update`; return if found.
2. Otherwise `create(...)` inside a nested `transaction.atomic()` (savepoint),
   with `status=PENDING_AUTHORIZATION` and `connected_by=user`.
3. `except IntegrityError:` → `select_for_update().get(...)`.
4. Leave `lifecycle_generation` at its default on create; the caller's
   `advance_generation` is the single bump path for both branches.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `feat(integrations): serialize first-connection creation on the unique constraint`

---

## T04 — Forced-consent predicate

Implements §5.3.1 and §5.3.2.

**Files**
- Modified: `apps/api/integrations/oauth_service.py`
- Modified: `apps/api/tests/test_oauth.py`

**Interfaces produced:** `_needs_forced_consent(connection)` keyed on stored
credential capability rather than "first connection". Signature unchanged.

**Failing tests first** — one per row of the §5.3.2 table:
- consent **is** forced: no connection row; no credential stored; empty stored
  refresh token; `DISCONNECTED`; `REAUTH_REQUIRED`; `ERROR` + `no_refresh_token`
- consent **is not** forced: `CONNECTED`; `AWAITING_RESOURCE_SELECTION` with a
  stored refresh token; `ERROR` with another code and an intact credential
- `test_second_project_first_connection_still_forces_consent` — a credential
  exists for the same provider in *another* project; this connection has none

Asserted by parsing `prompt` out of the built authorization URL, as
`test_oauth.py` already does.

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_oauth.py -q -k consent
```

**Expected failure:** the "is forced" cases assert `prompt=consent` and get no
`prompt` parameter at all.

**Minimal implementation:** rewrite the predicate exactly as §5.3.2 gives it,
including the deliberately redundant `DISCONNECTED` check with its comment.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_oauth.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `fix(integrations): force consent whenever no refresh token can be preserved`

---

## T05 — Callback staging and the stage-3 generation fence

Implements §9.4.2 stages 1–3. Stages 4–5 land in T10.

**Files**
- Modified: `apps/api/integrations/oauth_service.py`
- Modified: `apps/api/tests/test_oauth.py`
- Modified: `apps/api/tests/test_concurrency.py`

**Interfaces produced**
```python
# oauth_service.py — internal structure made explicit
def _finalize_credentials(*, request, connection, result) -> str
# returns previous_status, read under the lock BEFORE mutation (§8.1)
```

**Interfaces consumed:** `concurrency.locked_connection`, the request's
`connection_generation`.

**Failing tests first**
- `test_callback_is_discarded_when_generation_advanced` — bump the connection's
  generation between consumption and finalization; assert **no**
  `IntegrationCredential` row, status unchanged, no authorization audit event,
  and an `invalid_state` redirect
- `test_callback_proceeds_when_generation_matches` — the ordinary path
- `test_no_database_lock_is_held_across_the_token_exchange` — the exchange stub
  performs an independent write to the same connection and does not deadlock or
  block

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_oauth.py tests/test_concurrency.py -q
```

**Expected failure:** the discard test fails because credentials are written
regardless of the generation.

**Minimal implementation**
1. Keep stage 1 (`_consume_request`) exactly as it is — single-use,
   transactional, unchanged.
2. Keep the token exchange outside any transaction.
3. Wrap credential persistence in `transaction.atomic()` +
   `locked_connection`; re-read; compare
   `connection.lifecycle_generation != request.connection_generation` → raise
   `InvalidState` writing nothing.
4. Read `previous_status` from the re-read row **before** mutating it, and
   return it for T12's audit decision.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_oauth.py tests/test_concurrency.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `refactor(integrations): stage the OAuth callback and fence credential persistence`

---

## T06 — Credential refresh fence

Implements §9.3.1. Independent of T05 and T07; the design forbids sharing a
mechanism with either.

**Files**
- Modified: `apps/api/integrations/google/credentials.py`
- Modified: `apps/api/integrations/concurrency.py` (adds `RefreshFence`)
- Modified: `apps/api/tests/test_ga4_resources.py` (the refresh suite lives there)
- Modified: `apps/api/tests/test_concurrency.py`

**Interfaces produced**
```python
@dataclass(frozen=True)
class RefreshFence:
    credential_id: int | None
    credential_updated_at: datetime | None
    @classmethod
    def capture(cls, connection) -> "RefreshFence"
    def matches(self, connection) -> bool
```
`access_token_for(connection) -> str` keeps its signature and gains the fence
plus a **single** bounded retry.

**Failing tests first**
- `test_stale_invalid_grant_does_not_mark_reauth_required` (race A1) — the
  refresh stub stores a new credential before returning `invalid_grant`; assert
  status stays `connected` and `last_error_code` stays empty
- `test_stale_successful_refresh_does_not_overwrite_newer_credential` (race A2)
- `test_refresh_retry_returns_a_usable_current_token_without_a_second_call`
- `test_refresh_superseded_twice_raises_resource_unavailable`
- `test_refresh_deleted_credential_is_a_fence_mismatch_not_a_crash`

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_ga4_resources.py -q -k refresh
```

**Expected failure:** A1 fails with the connection in `reauth_required`; A2
fails with the stale access token stored.

**Minimal implementation:** the §9.3.1 sequence — capture, call without a lock,
lock and re-read, discard on mismatch (persisting nothing and **not** marking
reauth), return the current token if usable, else one retry, else
`ResourceUnavailable`.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_ga4_resources.py tests/test_search_console_resources.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `fix(integrations): fence the credential refresh against superseded state`

---

## T07 — General stale-result fence

Implements §9.3. `Fence` was defined in T02; this task puts it to work.

**Files**
- Modified: `apps/api/integrations/resource_service.py`
- Modified: `apps/api/tests/test_concurrency.py`

**Interfaces produced:** `Fence` captured immediately **after** any refresh has
committed and before the provider call; compared under the write lock;
mismatch → discard **everything**, including timestamps.

**Failing tests first**
- `test_stale_provider_401_after_reconnect_is_discarded` (race A)
- `test_stale_403_after_resource_change_is_discarded` (race B)
- `test_a_discarded_result_writes_nothing_at_all` — field by field, including
  `last_health_check_at`
- `test_two_concurrent_selections_the_later_discards`

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
```

**Expected failure:** the stale results are applied; the connection is moved to
`reauth_required` / `error`.

**Minimal implementation:** capture in `select_resource` before
`catalog.verify_resource`; compare inside `_persist_selection`'s transaction
after `select_for_update`; return the current entry unchanged on mismatch.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py tests/test_ga4_resources.py tests/test_search_console_resources.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `feat(integrations): discard provider results computed against superseded state`

---

## T08 — Health-check service and endpoint

Implements §3.1, §4.3, §10.

**Files**
- Created: `apps/api/integrations/lifecycle_service.py`
- Created: `apps/api/tests/test_lifecycle.py`
- Modified: `apps/api/integrations/views.py`, `urls.py`, `google/errors.py`

**Interfaces produced**
```python
# lifecycle_service.py
def health_check(*, project, provider_key: str) -> IntegrationConnection

# the shared outcome mapper — one copy of "what does this failure mean" (§10)
def apply_verification_outcome(*, connection, outcome, fence) -> IntegrationConnection
```
```
POST /api/projects/{project_id}/integrations/{provider}/health-check
  → 200 IntegrationEntry | 409 credential_missing | 409 resource_missing | 404
```
New error class `ResourceMissing` (`resource_missing`, 409) in `google/errors.py`.

**Interfaces consumed:** `catalog.verify_resource`, `access_token_for`, `Fence`.

**Failing tests first**
- success sets both timestamps and clears errors
- 403/404 → `error`, `last_successful_check_at` untouched
- Search Console `siteUnverifiedUser` → `error`, identically
- 401 → `reauth_required`
- **429/5xx/timeout → status unchanged**, `last_health_check_at` set,
  `last_successful_check_at` untouched
- nothing selected → 409 `resource_missing`, zero outbound calls
- `disconnected` → 409 `credential_missing`
- a posted `resource_id` for another resource is ignored — the identifier comes
  from the database
- tenancy: foreign project 404, unknown provider 404, unauthenticated 403

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_lifecycle.py -q
```

**Expected failure:** 404 on the route — it does not exist yet.

**Minimal implementation:** the service, the outcome mapper implementing §4.3,
one view reusing `GoogleApiErrorMixin` and `throttle_scope = "integrations"`,
one URL, one error class.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_lifecycle.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run
```

**Commit:** `feat(integrations): on-demand health check`

---

## T09 — Disconnect service and endpoint

Implements §3.2, §9.1, §9.4 (the disconnect half of the generation).

**Files**
- Modified: `apps/api/integrations/lifecycle_service.py`, `views.py`, `urls.py`
- Modified: `apps/api/tests/test_lifecycle.py`, `tests/test_concurrency.py`

**Interfaces produced**
```python
def disconnect(*, user, project, provider_key: str) -> IntegrationConnection
```
```
POST /api/projects/{project_id}/integrations/{provider}/disconnect → 200 | 404
```

**Failing tests first**
- deletes the `IntegrationCredential` **row** (not blanked); leaves resource id
  and label; leaves `last_successful_check_at`; status `disconnected`
- one `INTEGRATION_DISCONNECTED` audit row
- **`lifecycle_generation` advances**
- outstanding unconsumed authorization requests are **consumed**
- **second disconnect**: 200, status unchanged, credential still absent, **no**
  second audit row, **generation advances**, `updated_at` changes, outstanding
  requests consumed (§9.1) — asserted field by field
- **no request is made to the revoke endpoint** — asserted explicitly (§13)
- tenancy: foreign project 404, unknown provider 404

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_lifecycle.py -q -k disconnect
```

**Expected failure:** 404 on the route.

**Minimal implementation:** one transaction — `locked_connection`,
`advance_generation`, delete credential, set status and clear errors *only when
the status actually changes*, consume outstanding requests unconditionally,
write the audit event only on a real transition.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_lifecycle.py tests/test_concurrency.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `feat(integrations): disconnect an integration without revoking the grant`

---

## T10 — Reconnect terminal behaviour

Implements §5.1, §5.1.1, §5.4, and §9.4.2 stages 4–5.

**Files**
- Modified: `apps/api/integrations/oauth_service.py`
- Modified: `apps/api/tests/test_oauth.py`, `tests/test_lifecycle.py`,
  `tests/test_concurrency.py`

**Interfaces consumed:** `lifecycle_service.apply_verification_outcome` — the
same mapper the health check uses, so the two cannot drift.

**Failing tests first** — one per row of §5.1.1:
- success → `connected`, same resource, no re-pick
- `ResourceNotAccessible` → `awaiting_resource_selection`, id **retained**,
  `resource_not_accessible`
- 401 → `reauth_required`, id retained, `credential_refresh_failed`
- transient → `awaiting_resource_selection`, id retained,
  `resource_unavailable` (**not** `resource_not_accessible`)
- no stored resource → `awaiting_resource_selection` (M3 behaviour intact)
- §5.4 cancellation: denial **with** a credential leaves status, credential and
  resource untouched and records `access_denied`; denial of a first
  authorization deletes the row; neither produces `error`; `ScopeNotGranted`
  still does
- stage 5 discard: a disconnect between credential persistence and verification
  → the verification result is discarded, no `connected`, no `error`

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_oauth.py tests/test_lifecycle.py -q
```

**Expected failure:** every reconnect ends in `awaiting_resource_selection`
regardless of outcome; the transient row asserts `resource_unavailable` and
gets nothing.

**Minimal implementation:** after stage 3 commits, capture the `Fence`, verify
the stored resource without a lock, then re-lock and apply
`apply_verification_outcome` **after** re-checking the generation.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `feat(integrations): preserve a still-valid selection across a reconnect`

---

## T11 — Change resource, and the error taxonomy

Implements §6 and §12.

**Files**
- Modified: `apps/api/integrations/resource_service.py`, `google/errors.py`
- Modified: `apps/api/tests/test_ga4_resources.py`,
  `tests/test_search_console_resources.py`

**Interfaces produced:** `ResourceChangeNotSupported` **removed**;
`USABLE_STATUSES` gains `ERROR`.

**Failing tests first**
- a **different** resource while `connected` now succeeds, replacing id, label
  and metadata together, for **both** providers
- a failed verification leaves all four of id, label, metadata and both
  timestamps untouched
- same-resource re-submission stays idempotent
- a body-supplied label still has no effect
- a connection in `error` can be repointed
- `resource_change_not_supported` no longer exists in the taxonomy

The existing tests asserting the 409 are **replaced**, not deleted quietly —
they were pinning a restriction this task lifts, and each replacement states
that in a comment.

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_ga4_resources.py tests/test_search_console_resources.py -q
```

**Expected failure:** the change is refused with 409.

**Minimal implementation:** delete both guards (pre-call and under-lock), delete
the error class, add `ERROR` to `USABLE_STATUSES`.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
grep -rn "resource_change_not_supported" apps/api apps/web   # expect no hits
```

**Commit:** `feat(integrations): allow changing a selected resource`

---

## T12 — Audit event selection

Implements §8.1.

**Files**
- Modified: `apps/api/integrations/oauth_service.py`
- Modified: `apps/api/tests/test_audit.py`, `tests/test_oauth.py`

**Interfaces consumed:** the `previous_status` returned by T05's
`_finalize_credentials`, read under the lock before mutation.

**Failing tests first** — one per row of the §8.1 table:
- `reauth_required` → `INTEGRATION_RECONNECTED`
- `error` → `INTEGRATION_RECONNECTED`
- `connected` → `INTEGRATION_RECONNECTED`
- `pending_authorization` / newly created → `INTEGRATION_AUTHORIZED`
- `awaiting_resource_selection` → `INTEGRATION_AUTHORIZED`
- **`disconnected` → `INTEGRATION_AUTHORIZED`** (the lifecycle ended; a new one
  begins — never inferred from credential existence)
- exactly **one** event per completed authorization in every case
- `test_previous_status_is_read_before_stage_three_mutates_it` — a reconnect
  from `reauth_required` writes `RECONNECTED`, which is only possible if the
  read preceded the mutation

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_audit.py tests/test_oauth.py -q
```

**Expected failure:** every case writes `INTEGRATION_AUTHORIZED`.

**Minimal implementation:** a small pure mapping from `previous_status` to the
action, applied where the event is written inside stage 3's transaction.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `feat(integrations): choose the authorization audit event from previous status`

---

## T13 — Frontend recovery model

Implements §7.1–7.3.

**Files**
- Modified: `apps/web/lib/integrations/status.ts`
- Modified: `apps/web/components/integrations/__tests__/status-badge.test.tsx`

**Interfaces produced**
```ts
type RecoveryClass = "credential" | "authorization" | "resource" | "transient";
const RECOVERY_CLASS: Record<string, RecoveryClass>;
function presentationFor(status: IntegrationStatus, errorCode: string): StatusPresentation;
```
`StatusPresentation` gains `canTestConnection: boolean` and
`canDisconnect: boolean`.

**Failing tests first**
- the §7.2 matrix **row by row**, `status` × `last_error_code`
- a `resource`-class error offers Change property as primary and **not** an
  authorization action
- an unknown code falls back to the state's safe default
- a `transient` error on `connected` keeps the green badge
- `presentationFor` is the only exported entry point components use

```bash
cd apps/web && npm run test
```

**Expected failure:** `presentationFor` is not exported; the matrix rows fail.

**Minimal implementation:** the class map, `presentationFor`, and the two new
flags. `statusPresentation` is kept as a thin wrapper so M5 call sites compile
until T14–T17 move them.

**Verification**
```bash
cd apps/web && npm run test && npx tsc --noEmit && npm run lint
```

**Commit:** `feat(web): key integration recovery on status and error code`

---

## T14 — Test connection UI

**Files**
- Created: `apps/web/components/integrations/test-connection-button.tsx` and its
  `__tests__` file
- Modified: `apps/web/components/integrations/integration-card.tsx`,
  `apps/web/lib/api/types.ts`

**Interfaces produced**
```ts
export function TestConnectionButton(props: {
  projectId: number | string;
  provider: string;
}): JSX.Element;
```

**Failing tests first:** posts to `…/health-check`; renders only when
`canTestConnection`; reports the outcome from the **returned entry** rather than
inventing one; calls `router.refresh()`; no button when no resource is selected.

```bash
cd apps/web && npm run test
```

**Expected failure:** module not found.

**Verification:** `npm run test && npx tsc --noEmit && npm run lint`

**Commit:** `feat(web): test connection action`

---

## T15 — Change property UI

**Files**
- Modified: `apps/web/components/integrations/integration-card.tsx`
- Modified: `__tests__/integration-card.test.tsx`,
  `__tests__/resource-picker-dialog.test.tsx`

**Interfaces consumed:** the existing `ResourcePickerDialog`, **unchanged** —
only its `triggerLabel` varies, which is already a prop.

**Failing tests first:** `connected` renders the picker with a **Change
property** trigger; it still posts `{resource_id}` only; a `resource`-class
error also offers it; no new component is introduced.

**Verification:** `npm run test && npx tsc --noEmit && npm run lint`

**Commit:** `feat(web): change the selected property from a connected card`

---

## T16 — Disconnect UI

**Files**
- Created: `apps/web/components/integrations/disconnect-dialog.tsx` and its
  `__tests__` file
- Modified: `integration-card.tsx`

**Interfaces produced**
```ts
export function DisconnectDialog(props: {
  projectId: number | string;
  provider: string;
  providerName: string;
}): JSX.Element;
```

**Failing tests first:** confirmation is required before the POST; the copy
states that stored credentials are deleted **and that access is not revoked at
Google**, with the permissions link; it warns that revoking there affects every
integration sharing the authorization (§7.4); renders only when `canDisconnect`;
`router.refresh()` on success.

**Verification:** `npm run test && npx tsc --noEmit && npm run lint`

**Commit:** `feat(web): disconnect with an honest confirmation`

---

## T17 — Reconnect and recovery UI states

**Files**
- Modified: `integration-card.tsx`, `connect-button.tsx`,
  `__tests__/integration-card.test.tsx`

**Failing tests first:** `reauth_required` offers **Reconnect** as primary and
no test button; `error` + `credential` class offers Reconnect; `error` +
`authorization` class offers Try again; a **transient** error on `connected`
renders a muted note, not a destructive alert (§7.5); the action row never
branches on a provider — the existing source-scan test is extended to the two
new components.

**Verification**
```bash
cd apps/web && npm run test && npx tsc --noEmit && npm run lint
cd apps/web && NODE_OPTIONS=--max-old-space-size=640 npm run build
```

**Commit:** `feat(web): recovery actions for error and reauthorization states`

---

## T18 — Concurrency race suite

Consolidates `tests/test_concurrency.py` and proves **every** race in §9.5 is
covered. Earlier tasks wrote most of these as they went; this task is the audit
that none is missing, plus the ones no single task owned.

**Files**
- Modified: `apps/api/tests/test_concurrency.py`

**Technique.** Interleaving is produced by stubbing the *outbound call* to
perform the competing mutation before it returns — deterministic, no threads,
no sleeps. The one exception is the creation race (T03), which needs real
concurrency and uses two threads under
`@pytest.mark.django_db(transaction=True)`.

**Coverage matrix — every row must map to a named test**

| § | Race | Test | Owner |
|---|---|---|---|
| 9.5 | Stale provider 401 after reconnect | `test_stale_provider_401_after_reconnect_is_discarded` | T07 |
| 9.5 | Stale resource result after resource change | `test_stale_403_after_resource_change_is_discarded` | T07 |
| 9.3.1 | Stale refresh `invalid_grant` | `test_stale_invalid_grant_does_not_mark_reauth_required` | T06 |
| 9.3.1 | Stale **successful** refresh | `test_stale_successful_refresh_does_not_overwrite_newer_credential` | T06 |
| 9.5 | Disconnect **before** callback consumption | `test_disconnect_before_consumption_invalidates_the_request` | T09 |
| 9.5 | Disconnect **after** callback consumption (C′) | `test_disconnect_after_consumption_discards_the_callback` | T18 |
| 9.1 | **Repeat** disconnect while Connect is in flight (C″) | `test_repeat_disconnect_supersedes_an_inflight_connect` | T18 |
| 9.5 | Older callback after a newer authorization (D) | `test_older_callback_does_not_overwrite_newer_authorization` | T18 |
| 9.4.2 | Disconnect between credential persistence and verification | `test_disconnect_between_stages_discards_the_verification` | T10 |
| 9.4.1 A | Two concurrent starts, connection exists | `test_two_starts_on_an_existing_connection_get_distinct_generations` | T02 |
| 9.4.1 B | Start racing disconnect | `test_start_racing_disconnect_last_commit_owns_the_generation` | T18 |
| 9.4.1a C | Two concurrent **first** starts, no row | `test_two_first_authorizations_race` | T03 |
| 9.5 | Legitimate Connect **after** disconnect | `test_connect_after_disconnect_succeeds` | T18 |
| 9.3 | Two concurrent health checks | `test_two_concurrent_health_checks_later_discards` | T18 |
| 9.3 | Discarded result writes nothing | `test_a_discarded_result_writes_nothing_at_all` | T07 |

**Tie cases** (§14) — run with time frozen so every row carries an identical
timestamp, proving the generation and not a clock is doing the work:
- `test_tie_disconnect_second_discards_the_callback`
- `test_tie_disconnect_first_allows_the_callback`
- `test_tie_two_requests_only_the_newest_finalizes`

**Generation increment discipline**
- `test_generation_advances_only_on_authorization_start_and_disconnect` — a
  health check, a resource change, a token refresh and a completing callback all
  leave it untouched; a repeat disconnect and a start from
  `pending_authorization` both advance it

**The fence convention** (§14)
- `test_every_mutating_service_changes_updated_at` — asserts the value
  **changed**, never that wall-clock time advanced (§9.6)

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
```

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
# every row of the matrix above resolves to a collected test:
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py --collect-only -q
```

**Commit:** `test(integrations): cover every approved lifecycle race`

---

## T19 — Security, audit and leakage tests

**Files**
- Modified: `apps/api/tests/test_lifecycle.py`, `tests/test_audit.py`,
  `tests/test_provider_boundary.py`

**Failing tests first**
- no token, refresh token or Google error text in any response, log line or
  audit row, for **every** new path (health check, disconnect, reconnect,
  change resource) — the `TestNoLeakage` pattern extended
- the health check reads its identifier from the database: a posted
  `resource_id` naming another resource is never called
- `external_resource_meta` remains unserialized on every new response
- audit: one event per user-meaningful outcome; **no** event for a health check,
  including one that transitions the connection; no duplicate on a repeat
  disconnect
- the metadata allowlist is unchanged and every written key is in it
- `test_no_revoke_request_is_ever_made` — over the whole lifecycle suite
- the provider-vocabulary source scan is extended to `lifecycle_service.py`,
  `concurrency.py`, and the two new components

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_lifecycle.py tests/test_audit.py tests/test_provider_boundary.py -q
```

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `test(integrations): lifecycle security, audit and leakage coverage`

---

## T20 — Migration regression, mutation checks, staging handoff

**Files**
- Modified: `docs/V1_BUILD_PLAN.md` (tick M6)
- Modified: the design document only if implementation revealed a genuine
  contradiction (§3 of the brief) — otherwise untouched

**Migration regression**
```bash
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run  # No changes detected
ls apps/api/integrations/migrations/                                              # 0001, 0002, 0003 only
cd apps/api && ../../.venv/bin/python -m pytest -q
```
- `test_existing_rows_default_to_generation_zero` — a connection and a request
  created **before** the migration both default to `0`, and a callback across
  that boundary completes normally (§11.2, the in-flight-across-deploy case)

**Mutation checks — every one must turn the suite red, then be restored**

| # | Mutation | Expected to break |
|---|---|---|
| 1 | Transient failure clears `last_successful_check_at` | §4.3 |
| 2 | Disconnect blanks the credential instead of deleting the row | §13 |
| 3 | A failed reconnect verification sets `connected` | §5.1.1 |
| 4 | Add a revoke call to disconnect | §0 |
| 5–7 | Remove each of the three `Fence` fields in turn | §9.3 |
| 8 | A discarded result still writes `last_health_check_at` | §9.3 |
| 9 | Disconnect stops consuming outstanding requests | §9.1 |
| 10 | Remove `DISCONNECTED` from `_needs_forced_consent` | §5.3.1 |
| 11 | `_needs_forced_consent` returns False when no credential is stored | §5.3.2 |
| 12 | Remove the `RefreshFence` entirely | §9.3.1 |
| 13 | Remove only its `invalid_grant` arm | §9.3.1 A1 |
| 14 | Remove only its success arm | §9.3.1 A2 |
| 15 | `start_authorization` stops incrementing the generation | §9.4 |
| 16 | `disconnect` stops incrementing the generation | §9.4 |
| 17 | Only the **already-disconnected** path stops incrementing | §9.1 (the revision-4 bug, restored deliberately) |
| 18 | Compare generations with `>` instead of `!=` | §9.4 |
| 19 | Move the increment outside `select_for_update` | §9.4.1 |
| 20 | Remove the savepoint around the create | §9.4.1a |
| 21 | Remove the `IntegrityError` recovery | §9.4.1a |
| 22 | Drop the stage-3 generation check | §9.4.2 |
| 23 | Drop the stage-5 generation check | §9.4.2 |
| 24 | Read `previous_status` after stage 3 mutates it | §8.1 |
| 25 | Collapse the §5.1.1 transient outcome into `resource_not_accessible` | §5.1.1 |
| 26 | Denial sets `error` | §5.4 |
| 27 | `error` always offers the authorization action | §7.2 |

**Full green gate**
```bash
service postgresql start
cd apps/api && ../../.venv/bin/python -m pytest -q
cd apps/web && npm run test && npx tsc --noEmit && npm run lint
cd apps/web && NODE_OPTIONS=--max-old-space-size=640 npm run build
```

**Staging handoff:** produce the §17 checklist as the PR body's verification
section, with its six phases intact and **revocation last**. Staging is run by
the user; this plan never claims it.

**Commit:** `chore(integrations): milestone 6 verification and build plan tick`

---

## Self-review

Performed against the brief's checklist before handoff.

| Check | Result |
|---|---|
| **Full spec coverage** | Every design section maps to a task: §0→T09/T20, §3→T08/T09, §4→T08, §5→T04/T10, §6→T11, §7→T13–T17, §8→T12, §9.3→T07, §9.3.1→T06, §9.4→T02, §9.4.1→T02, §9.4.1a→T03, §9.4.2→T05/T10, §11→T01/T20, §12→T08/T11, §13→T19, §14→T18/T19, §16→T20, §17→T20 |
| **Placeholders / TODOs** | None. Every task names real files, real commands and real assertions |
| **Consistent names** | `locked_connection`, `advance_generation`, `Fence`, `RefreshFence`, `health_check`, `disconnect`, `apply_verification_outcome`, `presentationFor`, `RECOVERY_CLASS`, `TestConnectionButton`, `DisconnectDialog` — each defined once and referenced identically throughout |
| **Missing race tests** | The §9.5 matrix in T18 has a named test for every row, each attributed to the task that writes it |
| **Provider-specific logic** | No task modifies `ga4.py`, `search_console.py` or `resources.py`. T19 extends the source scan to the new modules and components |
| **Scope expansion** | No scheduler, no ingestion, no analytics, no notifications, no revocation. T20 mutation 4 asserts the revoke endpoint stays unused |
| **Migration discipline** | One migration (T01), two fields, verified in T01 and again in T20. Any second migration stops the work |
| **Three fences kept distinct** | T02 (`Fence` defined), T06 (`RefreshFence`), T07 (`Fence` used), plus the generation in T02/T03/T05 — three mechanisms, three tasks, no shared helper |

### Design ambiguities discovered

Two, both resolved within the design's own rules rather than by reopening it:

1. **Which module owns the shared verification-outcome mapper.** §10 requires
   one copy of "what does this failure mean", used by the health check, the
   reconnect and the change-resource paths, but does not name its home. The plan
   puts `apply_verification_outcome` in `lifecycle_service.py` and has
   `oauth_service` import it, because the health check is its primary caller and
   that keeps `oauth_service` free of §4.3's taxonomy. If review prefers a
   neutral module, that is a one-line change to T08 and T10.

2. **Where `Fence` lives.** §9.3 defines it without naming a module. The plan
   puts it in `concurrency.py` beside the generation primitives so all three
   mechanisms are visible in one place and provably distinct, while
   `RefreshFence` is *used* only inside `google/credentials.py` as §9.3.1
   requires. Nothing in the design forbids this; it is a placement choice, not a
   semantic one.

Neither changes behaviour, and neither is a contradiction in the design.
