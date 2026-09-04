# Milestone 6 — implementation plan

Plan only. No production code is written until this plan is approved.

**Source of truth:** the approved M6 design at commit
`2d33a6c65e8027a248f2fd38ca6fddd0eb4cfb0c`
(`docs/superpowers/specs/2026-09-04-m6-lifecycle-design.md`). Section references
below (§n) are to that document. Where this plan and the design disagree, the
design wins and the disagreement is a defect in this plan. **The design is
closed**; this revision changes execution only.

**Base:** `main` at `8eb3ffd` (Milestone 5). Branch:
`claude/m6-lifecycle-implementation`.

**Method:** strict TDD. Every task writes failing tests first, runs them to
observe the *expected* failure, then implements the minimum that makes them
pass. **A task is not complete until the full backend suite is green** — which
means no task may knowingly land a commit that breaks an existing path.

> **Revision 3 (execution only).** Review found three more execution
> contradictions and no design contradiction. `locked_connection` could create a
> connection for *any* caller, so `disconnect` and callback finalization could
> have brought back a row they are meant to end — split into two explicitly
> named functions (§T02). `apply_verification_outcome` was specified to write
> selection fields on success, which is broader than the design allows —
> selection fields are now never written outside the selection path (§T07).
> And `access_token_for` can fail *before* `verify()` runs, so the conversion of
> lifecycle errors into outcomes is now defined for that path too, with the
> "only place" claim corrected (§T07).

> **Revision 2 (execution only).** Review found six execution defects and no
> design contradiction. Former T02 changed `start_authorization` to use a
> `locked_connection` that could not handle a missing row, so a first
> authorization would have failed until former T03 landed — a knowingly broken
> commit. T02 and T03 are now **one** task (§T02). The stale-health-result
> races moved to the task that creates `health_check`. `apply_verification_outcome`
> now has a complete type contract. T01's TDD order is explicit. Every mutation
> in the final task now names a targeted command. Task count is **19**.

---

## 0. Ground rules for the whole milestone

1. **No production code before a failing test.** Each task states the exact
   command and the exact failure to expect. An unexpected failure mode (an
   import error where an assertion was expected) means the test is wrong —
   fix the test before implementing.
2. **No task lands a knowingly broken path.** Every task's gate is the **full**
   backend suite, not just its own file. If a task cannot reach that gate
   alone, it is the wrong size and must be merged with its neighbour.
3. **The three fences stay three mechanisms** (§9.6). Never merged, never
   sharing a dataclass, never sharing a helper:

   | Mechanism | Question it answers | Where | Compares |
   |---|---|---|---|
   | `lifecycle_generation` | Is this authorization still the current *intent*? | `concurrency.advance_generation`, checked in the callback | integer equality |
   | `Fence` | Is this provider result about the state it was computed from? | captured by lifecycle operations before an outbound call | `updated_at` / resource id / credential `updated_at` equality |
   | `RefreshFence` | Is this refresh result about the credential it was derived from? | inside `google/credentials.py` | credential id + `updated_at` equality |

4. **No provider-specific branching in shared lifecycle code.** `ga4.py`,
   `search_console.py` and `resources.py` are not modified by any task. The
   `test_provider_boundary.py` source scan is extended, never relaxed.
5. **Exactly one migration**, containing exactly the two approved fields. If a
   second appears necessary, **stop and report** (§11.3).
6. **No scope beyond M6.** No scheduled health checks, ingestion, analytics,
   notifications, or grant-wide revocation. A test asserts the revoke endpoint
   is never called.
7. **M3/M4/M5 invariant tests** (§15) may change only where a symbol was
   renamed, never where a value is asserted. The two deliberate M3 behaviour
   changes (§15 items 17–18) are the only exceptions, each covered by a new
   test in the task that makes it.

### Standing commands

```bash
service postgresql start                                    # not always running here

cd apps/api && ../../.venv/bin/python -m pytest tests/<file> -q
cd apps/api && ../../.venv/bin/python -m pytest -q
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run

cd apps/web && npm run test
cd apps/web && npx tsc --noEmit && npm run lint
cd apps/web && NODE_OPTIONS=--max-old-space-size=640 npm run build
```

### New modules

| Path | Purpose |
|---|---|
| `apps/api/integrations/concurrency.py` | Generation primitives and the `Fence` dataclass |
| `apps/api/integrations/verification.py` | The provider-neutral verification outcome types (§T07 interface contract) |
| `apps/api/integrations/lifecycle_service.py` | `health_check()` and `disconnect()` |
| `apps/api/integrations/migrations/0003_lifecycle_generation.py` | The one migration |
| `apps/api/tests/test_lifecycle.py` | Health check, disconnect, reconnect terminal behaviour |
| `apps/api/tests/test_concurrency.py` | Every race in §9.5 |
| `apps/web/components/integrations/test-connection-button.tsx` | Test connection |
| `apps/web/components/integrations/disconnect-dialog.tsx` | Disconnect confirmation |

---

## Task index — 19 tasks

| # | Task | Commit |
|---|---|---|
| T01 | Migration and model generation fields | `feat(integrations): add lifecycle generation fields` |
| T02 | Concurrency primitives, atomic generation, **and the creation race** | `feat(integrations): assign authorization generations atomically` |
| T03 | Forced-consent predicate | `fix(integrations): force consent whenever no refresh token can be preserved` |
| T04 | Callback staging and the stage-3 generation fence | `refactor(integrations): stage the OAuth callback and fence credential persistence` |
| T05 | Credential refresh fence | `fix(integrations): fence the credential refresh against superseded state` |
| T06 | Stale-result fence for resource selection | `feat(integrations): discard selection results computed against superseded state` |
| T07 | Verification outcome contract, health check, **races A and B** | `feat(integrations): on-demand health check` |
| T08 | Disconnect service and endpoint | `feat(integrations): disconnect an integration without revoking the grant` |
| T09 | Reconnect terminal behaviour | `feat(integrations): preserve a still-valid selection across a reconnect` |
| T10 | Change resource, and the error taxonomy | `feat(integrations): allow changing a selected resource` |
| T11 | Audit event selection | `feat(integrations): choose the authorization audit event from previous status` |
| T12 | Frontend recovery model | `feat(web): key integration recovery on status and error code` |
| T13 | Test connection UI | `feat(web): test connection action` |
| T14 | Change property UI | `feat(web): change the selected property from a connected card` |
| T15 | Disconnect UI | `feat(web): disconnect with an honest confirmation` |
| T16 | Reconnect and recovery UI states | `feat(web): recovery actions for error and reauthorization states` |
| T17 | Concurrency race suite | `test(integrations): cover every approved lifecycle race` |
| T18 | Security, audit and leakage tests | `test(integrations): lifecycle security, audit and leakage coverage` |
| T19 | Migration regression, mutation checks, staging handoff | `chore(integrations): milestone 6 verification and build plan tick` |

---

## T01 — Migration and model generation fields

Implements §9.4, §11.1.

**Files**
- Modified: `apps/api/integrations/models.py`
- Created: `apps/api/integrations/migrations/0003_lifecycle_generation.py`
- Created: `apps/api/tests/test_concurrency.py`

**Interfaces produced**
- `IntegrationConnection.lifecycle_generation: PositiveIntegerField(default=0)`
- `OAuthAuthorizationRequest.connection_generation: PositiveIntegerField(default=0)`

### Explicit TDD sequence

`makemigrations --check` is **green before the model changes**, so it is not the
initial failing test. It becomes a *mid-task* signal, not the starting one.

| Step | Action | Expected result |
|---|---|---|
| **A** | Write model-field tests and run them: `test_new_connection_starts_at_generation_zero`, `test_new_authorization_request_defaults_to_generation_zero`. `cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q` | **RED** — `TypeError: 'lifecycle_generation' is an invalid keyword argument` / `AttributeError`. An `ImportError` here means the test file is wrong |
| **B** | Add exactly the two fields to `models.py`, each with a comment citing §9.4 | — |
| **C** | `cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run` | **Now non-zero exit**, reporting two pending field additions. This confirms B and only B is outstanding |
| **D** | `cd apps/api && ../../.venv/bin/python manage.py makemigrations integrations --name lifecycle_generation` | Creates `0003_lifecycle_generation.py` |
| **E** | Read the generated file | **Exactly two `AddField` operations and nothing else.** Anything more → stop and report (ground rule 5) |
| **F** | Re-run `makemigrations --check --dry-run` | "No changes detected" |
| **G** | `cd apps/api && ../../.venv/bin/python -m pytest -q` | Full suite green |

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run
ls apps/api/integrations/migrations/     # 0001, 0002, 0003, __init__ only
```

**Commit:** `feat(integrations): add lifecycle generation fields`

---

## T02 — Concurrency primitives, atomic generation, and the creation race

Implements §9.4.1 **and** §9.4.1a. **Merged from the former T02 and T03**: the
former T02 routed `start_authorization` through a single `locked_connection`
that could only handle an existing row, so a first authorization — the most common
path there is — would have raised `DoesNotExist` until the former T03 landed.
That commit could not have met its own full-suite gate. Both halves of
the connection-acquisition path therefore land together.

**Files**
- Created: `apps/api/integrations/concurrency.py`
- Modified: `apps/api/integrations/oauth_service.py`
- Modified: `apps/api/tests/test_concurrency.py`

**Interfaces produced**
```python
# integrations/concurrency.py

# --- Two functions, because creation capability must be explicit. ------------
# Only an authorization start may bring an IntegrationConnection into
# existence. Every other lifecycle operation acts on a row that already exists,
# and must fail rather than create one: a disconnect that creates the thing it
# is ending, or a stale callback that resurrects a deleted connection, would
# both be defects the type system should make hard to write.

def locked_existing_connection(project, provider_key) -> IntegrationConnection
# select_for_update().get(...). NEVER creates. Raises
# IntegrationConnection.DoesNotExist, which each caller maps to its own
# contract (see the usage table below).

def _existing_locked(project, provider_key) -> IntegrationConnection | None
# The same lookup returning None instead of raising. A named seam so a test can
# force the no-row branch deterministically (see the recovery test below).

def locked_or_create_connection_for_authorization(
    project, provider_key, *, user
) -> IntegrationConnection
# The ONLY creating entry point, and its name says so.
# Existing row -> locked and returned.
# No row -> created inside a savepoint; on IntegrityError, recovered with
# select_for_update().get(). Never raises IntegrityError to its caller.
# §9.4.1a.

def advance_generation(connection) -> int
# Increments in Python under the caller's lock, saves
# update_fields=["lifecycle_generation", "updated_at"], returns the new value.
# No F() -- see 9.4.1 for why the lock makes it unnecessary and the read-back
# hazardous.

@dataclass(frozen=True)
class Fence:                       # defined here; first used in T06
    connection_updated_at: datetime
    external_resource_id: str
    credential_updated_at: datetime | None

    @classmethod
    def capture(cls, connection) -> "Fence": ...
    def matches(self, connection) -> bool: ...
```

**Interfaces consumed:** `IntegrationConnection`, `transaction.atomic`,
`select_for_update`, the existing
`UniqueConstraint(fields=["project", "provider"])` from M1.

### Which lifecycle operation may create a connection

| Caller | Function | May create? |
|---|---|---|
| `start_authorization` | `locked_or_create_connection_for_authorization` | **Yes** — the only one |
| `disconnect` (T08) | `locked_existing_connection` | No |
| Callback stage 3 (T04) | `locked_existing_connection` | No |
| Callback stage 5 / `apply_verification_outcome` (T07, T09) | `locked_existing_connection` | No |
| Credential refresh finalization (T05) | `locked_existing_connection` | No |
| `select_resource` / `_persist_selection` (T06, T10) | `locked_existing_connection` | No |

A test in T18 asserts that `locked_or_create_connection_for_authorization`
appears in exactly one call site across `apps/api/integrations/`.

### Failing tests first

Existing-row path:
- `test_start_authorization_advances_the_generation`
- `test_request_carries_the_generation_it_was_assigned`
- `test_two_starts_on_an_existing_connection_get_distinct_generations`
- `test_connection_holds_the_newest_generation`
- `test_generation_advances_even_from_pending_authorization` (§9.4: every
  invocation, not only state changes)

Creation path:
- `test_first_authorization_creates_the_connection_and_advances_generation` —
  **the path the former split would have broken**; it must pass at this commit
- `test_locked_existing_connection_never_creates` — on a missing row it raises
  `DoesNotExist` and leaves the table empty
- `test_creation_race_recovery_branch` — see below
- `test_two_first_authorizations_race` — two threads under
  `@pytest.mark.django_db(transaction=True)`; assert **exactly one**
  `IntegrationConnection`, **distinct** request generations, connection holds the
  **higher**, only the matching request could finalize, neither call raised

#### The recovery test, concretely

Patching `create()` to raise `IntegrityError` is **not** sufficient: the
recovery `select_for_update().get(...)` needs a winner row to find, and a faked
exception leaves none. The test must make a real winner visible while forcing
the initial lookup down the no-row branch.

```
Setup:   a connection for (project, provider) genuinely EXISTS in the database
Patch:   concurrency._existing_locked -> returns None on its first call,
         then delegates to the real implementation
Effect:  locked_or_create_connection_for_authorization observes "no row"
         and calls create()
         -> the REAL unique constraint raises a REAL IntegrityError
         -> the savepoint rolls back
         -> select_for_update().get() finds the row that was there all along
Assert:  the returned connection is the pre-existing row (same pk)
         the generation advanced by exactly one
         the request carries that value
         no IntegrityError escaped
         exactly one connection row exists
```

This exercises the true sequence — *no row observed → create loses the unique
race → savepoint rollback → locked get sees the winner* — against the real
constraint and real savepoint semantics, with no fabricated exception. The
threaded test above remains the integration proof that Postgres actually
serializes two genuine first Connects.

```bash
service postgresql start
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
```

**Expected failure:** `ModuleNotFoundError: integrations.concurrency`, then
assertion failures showing the generation unchanged; the recovery test fails
with an uncaught `IntegrityError`; the threaded test fails with two connection
rows or a 500.

**Minimal implementation**
1. `concurrency.py` with `_existing_locked`, `locked_existing_connection`,
   `locked_or_create_connection_for_authorization`, `advance_generation`, and
   the `Fence` dataclass (unused for now).
2. `start_authorization`: obtain the connection through
   `locked_or_create_connection_for_authorization`, call `advance_generation`
   in the same `transaction.atomic()`, create the `OAuthAuthorizationRequest`
   in that transaction carrying the returned value. The existing
   `get_or_create` call is **replaced**, not wrapped.
3. Preserve M3's rule that starting an authorization does not destroy durable
   state: only `lifecycle_generation` and `updated_at` are written on an
   existing row.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py tests/test_oauth.py -q
cd apps/api && ../../.venv/bin/python -m pytest -q          # FULL suite green
```

**Commit:** `feat(integrations): assign authorization generations atomically`

---

## T03 — Forced-consent predicate

Implements §5.3.1, §5.3.2.

**Files:** `apps/api/integrations/oauth_service.py`; `apps/api/tests/test_oauth.py`

**Interfaces produced:** `_needs_forced_consent(connection) -> bool`, keyed on
stored credential capability. Signature unchanged.

**Failing tests first** — one per row of the §5.3.2 table.
Forced: no connection row; no credential stored; empty stored refresh token;
`DISCONNECTED`; `REAUTH_REQUIRED`; `ERROR` + `no_refresh_token`.
Not forced: `CONNECTED`; `AWAITING_RESOURCE_SELECTION` with a stored refresh
token; `ERROR` with another code and an intact credential.
Plus `test_second_project_first_connection_still_forces_consent` — a credential
exists for the same provider in *another* project; this connection has none.
Asserted by parsing `prompt` from the built authorization URL, as `test_oauth.py`
already does.

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_oauth.py -q -k consent
```
**Expected failure:** the forced cases assert `prompt=consent` and find no
`prompt` parameter at all.

**Minimal implementation:** the predicate exactly as §5.3.2 gives it, including
the deliberately redundant `DISCONNECTED` check with its comment.

**Verification:** `cd apps/api && ../../.venv/bin/python -m pytest -q`

**Commit:** `fix(integrations): force consent whenever no refresh token can be preserved`

---

## T04 — Callback staging and the stage-3 generation fence

Implements §9.4.2 stages 1–3. Stages 4–5 land in T09.

**Files:** `apps/api/integrations/oauth_service.py`; `tests/test_oauth.py`;
`tests/test_concurrency.py`

**Interfaces produced**
```python
def _finalize_credentials(*, request, connection, result) -> str
# Locks, re-reads, requires generation equality, reads previous_status BEFORE
# mutating, persists credentials + granted_scopes + awaiting_resource_selection,
# clears errors. Returns previous_status for T11's audit decision.
# Raises InvalidState (writing nothing) when the generation has advanced.
```

**Failing tests first**
- `test_callback_is_discarded_when_generation_advanced` — bump the generation
  between consumption and finalization; assert **no** `IntegrationCredential`
  row, status unchanged, no authorization audit event, `invalid_state` redirect
- `test_callback_proceeds_when_generation_matches`
- `test_no_database_lock_is_held_across_the_token_exchange` — the exchange stub
  performs an independent write to the same connection and neither blocks nor
  deadlocks
- `test_callback_does_not_recreate_a_deleted_connection` — the connection is
  deleted between consumption and finalization; assert the callback fails with
  `InvalidState`, **no** `IntegrationConnection` row is created, **no**
  `IntegrationCredential` is created, and no audit event is written

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_oauth.py tests/test_concurrency.py -q
```
**Expected failure:** credentials are written regardless of the generation.

**Minimal implementation:** keep stage 1 (`_consume_request`) unchanged; keep
the token exchange outside any transaction; wrap persistence in
`transaction.atomic()` + **`locked_existing_connection`**; compare
generations; read `previous_status` before mutation. A callback whose
connection no longer exists raises `DoesNotExist`, which stage 3 converts to
`InvalidState` — never a recreated row.

**Verification:** `cd apps/api && ../../.venv/bin/python -m pytest -q`

**Commit:** `refactor(integrations): stage the OAuth callback and fence credential persistence`

---

## T05 — Credential refresh fence

Implements §9.3.1. Shares no mechanism with T04 or T06.

**Files:** `apps/api/integrations/google/credentials.py`;
`apps/api/integrations/concurrency.py` (adds `RefreshFence`);
`tests/test_ga4_resources.py`; `tests/test_concurrency.py`

**Interfaces produced**
```python
@dataclass(frozen=True)
class RefreshFence:
    credential_id: int | None
    credential_updated_at: datetime | None
    @classmethod
    def capture(cls, connection) -> "RefreshFence": ...
    def matches(self, connection) -> bool: ...
```
`access_token_for(connection) -> str` keeps its signature; gains the fence and
one bounded retry.

**Failing tests first**
- `test_stale_invalid_grant_does_not_mark_reauth_required` (A1) — the refresh
  stub stores a new credential before returning `invalid_grant`; status stays
  `connected`, `last_error_code` stays empty
- `test_stale_successful_refresh_does_not_overwrite_newer_credential` (A2)
- `test_refresh_retry_returns_a_usable_current_token_without_a_second_call`
- `test_refresh_superseded_twice_raises_resource_unavailable`
- `test_refresh_deleted_credential_is_a_fence_mismatch_not_a_crash`

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_ga4_resources.py -q -k refresh
```
**Expected failure:** A1 leaves the connection in `reauth_required`; A2 stores
the stale access token.

**Minimal implementation:** the §9.3.1 sequence — capture, call unlocked, lock
and re-read, discard on mismatch (persist nothing, **do not** mark reauth),
return the current token if usable, else one retry, else `ResourceUnavailable`.

**Verification:** `cd apps/api && ../../.venv/bin/python -m pytest -q`

**Commit:** `fix(integrations): fence the credential refresh against superseded state`

---

## T06 — Stale-result fence for resource selection

Implements §9.3 for the path that exists at this point in the sequence.

**Scope note (review fix 2).** Design races A and B are both described as
starting with a *health check*, and `health_check` does not exist until T07.
This task therefore covers only **resource-selection** staleness, which
`resource_service` genuinely performs today. No synthetic test is written for a
call path that does not yet exist; races A and B belong to T07.

**Files:** `apps/api/integrations/resource_service.py`; `tests/test_concurrency.py`

**Interfaces consumed:** `Fence` (defined in T02).

**Failing tests first**
- `test_stale_selection_result_is_discarded` — the verification stub mutates the
  connection before returning; the result is discarded
- `test_a_discarded_selection_writes_nothing_at_all` — field by field
- `test_two_concurrent_selections_the_later_discards`

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
```
**Expected failure:** the stale result is applied over newer state.

**Minimal implementation:** capture the `Fence` in `select_resource` after any
refresh has committed and before `catalog.verify_resource`; compare inside
`_persist_selection`'s transaction after `select_for_update`; on mismatch return
the current entry, writing nothing.

**Verification:** `cd apps/api && ../../.venv/bin/python -m pytest -q`

**Commit:** `feat(integrations): discard selection results computed against superseded state`

---

## T07 — Verification outcome contract, health check, and races A and B

Implements §3.1, §4.3, §10, and design races A and B.

**Files**
- Created: `apps/api/integrations/verification.py`
- Created: `apps/api/integrations/lifecycle_service.py`
- Created: `apps/api/tests/test_lifecycle.py`
- Modified: `apps/api/integrations/views.py`, `urls.py`, `google/errors.py`
- Modified: `apps/api/tests/test_concurrency.py`

### The verification contract (review fix 3)

The former plan declared `apply_verification_outcome(*, connection, outcome, fence)`
without defining `outcome`. Defined here in full so no architecture is invented
during implementation.

**Established behaviour it wraps.** `ResourceCatalog.verify_resource(access_token,
resource_id)` (M5) returns a `RemoteResource` on success and otherwise raises one
of the project's **own, already provider-neutral** errors:
`ResourceNotAccessible`, `CredentialRefreshFailed`, `ResourceUnavailable`, or
`GoogleApiError`. Provider modules have already translated Google's responses;
nothing below ever sees a Google status code or body.

```python
# integrations/verification.py  — the shared, provider-neutral vocabulary

class VerificationResult(StrEnum):
    SUCCESS = "success"
    RESOURCE_NOT_ACCESSIBLE = "resource_not_accessible"
    CREDENTIAL_REJECTED = "credential_rejected"
    TRANSIENT = "transient"


@dataclass(frozen=True)
class VerificationOutcome:
    result: VerificationResult
    resource: RemoteResource | None   # SUCCESS only; None otherwise
    error_code: str                   # "" on SUCCESS
    error_message: str                # "" on SUCCESS


class VerificationContext(StrEnum):
    HEALTH_CHECK = "health_check"     # §4.3 status table
    RECONNECT = "reconnect"           # §5.1.1 status table


def outcome_from_lifecycle_error(error: GoogleApiError) -> VerificationOutcome
# THE single conversion table from a lifecycle error to an outcome:
#   ResourceNotAccessible  -> RESOURCE_NOT_ACCESSIBLE
#   CredentialRefreshFailed -> CREDENTIAL_REJECTED
#   ResourceUnavailable     -> TRANSIENT
#   GoogleApiError (base)   -> TRANSIENT
# Carries the error's own code and message. Contains no provider branch: it
# switches on our error classes, which are already provider-neutral.
# CredentialMissing is deliberately NOT in this table -- see below.

def verify(*, catalog, access_token: str, resource_id: str) -> VerificationOutcome
# Calls catalog.verify_resource and converts any of the four errors above
# through outcome_from_lifecycle_error. Never raises for them. Never inspects a
# provider response.
```

#### Where errors are converted — the precise claim

An earlier revision said `verify()` is "the only place provider errors are
converted". **That cannot be literally true**, because `health_check` must call
`access_token_for(connection)` *before* `verify()`, and that call can itself
raise. The accurate statement, and the one the implementation must satisfy:

> **`verification.py` is the only module that turns a lifecycle error into a
> `VerificationOutcome`, and `outcome_from_lifecycle_error` is the only table
> that does it.** `verify()` is one caller of that table; `health_check` is
> another, for the failures that happen before verification can begin.

There is therefore **one** taxonomy implementation, used from two points.

#### `access_token_for` failures during a health check

`access_token_for` (M4, fenced in T05) raises three things, and they do **not**
all mean the same thing for the §3.1 contract:

| Raised | Health-check handling | HTTP |
|---|---|---|
| `CredentialMissing` | **Propagates.** §3.1 makes this a `409 credential_missing`: the check never began, so there is no outcome to report | **409** |
| `CredentialRefreshFailed` | Converted via `outcome_from_lifecycle_error` → `CREDENTIAL_REJECTED`. The check *completed*, with the answer "this credential is dead" | **200** entry, `reauth_required` |
| `ResourceUnavailable` | Converted → `TRANSIENT`. The check completed with "could not reach Google" | **200** entry, status unchanged |

A completed check always returns `200` carrying the resulting state (§3.1); only
a check that could not start is a `409`.

#### `health_check` — the exact flow

```
1. resolve the connection (unlocked read)
     no row / no credential            -> raise CredentialMissing        (409)
     no external_resource_id           -> raise ResourceMissing          (409)

2. try:
       access_token = access_token_for(connection)
   except CredentialMissing:
       raise                                                             # 409
   except (CredentialRefreshFailed, ResourceUnavailable, GoogleApiError) as exc:
       access_token = None
       outcome = verification.outcome_from_lifecycle_error(exc)

3. connection.refresh_from_db()
   # A failed refresh may already have written status via mark_reauth_required,
   # which is T05's own fenced write. Re-read before capturing, so the Fence
   # describes the state as it is now.
   fence = Fence.capture(connection)

4. if access_token is not None:
       outcome = verification.verify(
           catalog=catalog,
           access_token=access_token,
           resource_id=connection.external_resource_id,   # from the DB, never the request
       )

5. return apply_verification_outcome(
       connection=connection, outcome=outcome, fence=fence,
       context=VerificationContext.HEALTH_CHECK,
   )
```

Notes that matter:
- **The Fence is captured after step 2, whatever its outcome** — success or
  failure — so it always describes post-token-acquisition state. Uniform for
  both branches, and it means a refresh that wrote `reauth_required` is inside
  the snapshot rather than fencing out the very check that caused it.
- **Stale-refresh behaviour stays T05's.** `RefreshFence` governs whether a
  refresh result is applied at all; by the time `access_token_for` returns or
  raises, that question is settled. The two fences do not overlap and are not
  merged.
- **No provider branch anywhere in this flow**, and no second copy of the
  taxonomy.

```python
# integrations/lifecycle_service.py

def apply_verification_outcome(
    *,
    connection: IntegrationConnection,
    outcome: VerificationOutcome,
    fence: Fence,
    context: VerificationContext,
    expected_generation: int | None = None,
) -> IntegrationConnection
# Locks the row, re-reads it, and BEFORE ANY WRITE:
#   1. if expected_generation is not None and it differs -> discard, write nothing
#   2. if not fence.matches(reread) -> discard, write nothing (not even a timestamp)
# Then writes per the (context, result) table below, in one save, and returns
# the row. Contains no provider branch: it switches on VerificationResult only.
```

**The (context, result) status table** — the single place §4.3 and §5.1.1 live:

| `result` | `HEALTH_CHECK` status | `RECONNECT` status | `last_health_check_at` | `last_successful_check_at` |
|---|---|---|---|---|
| `SUCCESS` | `connected` | `connected` | set | **set** |
| `RESOURCE_NOT_ACCESSIBLE` | `error` | `awaiting_resource_selection` | set | untouched |
| `CREDENTIAL_REJECTED` | `reauth_required` | `reauth_required` | set | untouched |
| `TRANSIENT` | **unchanged** | `awaiting_resource_selection` | set | untouched |

Errors are cleared only on `SUCCESS`; otherwise `error_code`/`error_message` are
written from the outcome.

#### `apply_verification_outcome` never writes selection fields

There is deliberately **no resource column** in that table.
`external_resource_id`, `external_resource_label` and `external_resource_meta`
are **never written** by this function, in either context and on every result
including `SUCCESS`:

- **A health check is not a selection.** It answers "does the stored selection
  still work", and the design gives it status, the two health timestamps and
  error clearing — nothing else.
- **A reconnect success proves the remembered selection is still valid**, and
  returns to `connected` on **that same stored selection**. There is nothing to
  replace.
- The design's boundary that provider-authoritative selection metadata is
  persisted **only through the selection path** (M4's rule, carried into M5)
  therefore survives: `_persist_selection` remains the single writer of those
  three fields.

`VerificationOutcome.resource` still exists — it is the evidence the provider
returned, and its presence is what makes a result `SUCCESS` — but
`apply_verification_outcome` reads it only to know that, never to write it. The
save's `update_fields` list simply does not contain the three selection fields,
which makes the guarantee structural rather than a matter of care.

**Tests (T07 for health check, T09 for reconnect)**
- `test_health_check_success_leaves_selection_fields_unchanged` — id, label and
  metadata compared byte-for-byte before and after, including the case where the
  provider returns a *different* label than the one stored
- `test_reconnect_success_leaves_selection_fields_unchanged` — status becomes
  `connected` and both timestamps advance while the three fields do not move

**Both callers use the same two functions.** T07's `health_check` calls
`verify(...)` then `apply_verification_outcome(..., context=HEALTH_CHECK)`. T09's
reconnect calls the same `verify(...)` then
`apply_verification_outcome(..., context=RECONNECT, expected_generation=request.connection_generation)`.
There is one classifier and one writer; only the table row differs.

**Resource selection does not use this mapper.** Per the §9 transition table a
failed change writes nothing and the error surfaces as an HTTP response — T06
and T10 keep that path as it is. Stated so the mapper is not over-applied.

**Other interfaces produced**
```python
def health_check(*, project, provider_key: str) -> IntegrationConnection
```
```
POST /api/projects/{project_id}/integrations/{provider}/health-check
  → 200 IntegrationEntry | 409 credential_missing | 409 resource_missing | 404
```
New error class `ResourceMissing` (`resource_missing`, 409) in `google/errors.py`.

**Failing tests first** — `tests/test_lifecycle.py`:
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

**Token-acquisition failures (fix 3), each asserting the endpoint returns 200
with the correct persisted entry:**
- `test_health_check_credential_refresh_failure_returns_200_reauth_required` —
  `access_token_for` raises `CredentialRefreshFailed`; response is **200**, the
  entry reads `reauth_required` with `credential_refresh_failed`,
  `last_health_check_at` is set, `last_successful_check_at` untouched, and **no
  provider call was made**
- `test_health_check_transient_token_failure_returns_200_status_unchanged` —
  `access_token_for` raises `ResourceUnavailable`; response is **200**, status
  **unchanged**, `resource_unavailable` recorded, `last_health_check_at` set,
  `last_successful_check_at` untouched
- `test_health_check_without_a_credential_is_409` — `CredentialMissing`
  propagates; the check never began

**Races A and B**, in `tests/test_concurrency.py`, now that the call path exists:
- `test_stale_provider_401_after_reconnect_is_discarded` (A) — the health
  check's verification stub performs a reconnect before returning 401; the
  connection stays `connected` and nothing is written
- `test_stale_403_after_resource_change_is_discarded` (B) — the stub changes the
  resource before returning 403; the connection stays `connected` on the new
  resource

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_lifecycle.py tests/test_concurrency.py -q
```
**Expected failure:** 404 on the route (it does not exist), then
`ModuleNotFoundError: integrations.verification`.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run
```

**Commit:** `feat(integrations): on-demand health check`

---

## T08 — Disconnect service and endpoint

Implements §3.2, §9.1, and the disconnect half of §9.4.

**Files:** `lifecycle_service.py`, `views.py`, `urls.py`;
`tests/test_lifecycle.py`, `tests/test_concurrency.py`

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
- **no request is made to the revoke endpoint**
- tenancy: foreign project 404, unknown provider 404
- **`test_disconnect_with_no_connection_row_creates_nothing`** — POST disconnect
  for a known provider that has never been connected: **no**
  `IntegrationConnection` is created, no credential, no audit row, and the
  response is **200 carrying the synthesized `not_connected` entry** (see the
  contract note below)

#### Disconnect when no connection row exists

`locked_existing_connection` raises `DoesNotExist` here, and the plan must say
what the endpoint does with it. §3.2 enumerates only `200` and `404`, and `404`
is reserved there for an unknown project or provider — which this is not.

The resolution, consistent with §9.1's idempotency principle: **200 with the
synthesized `not_connected` entry, creating nothing and writing nothing.**
Disconnect's externally meaningful result — not connected, no credential, no
audit event — is already true, which is exactly the §9.1 definition of an
idempotent disconnect. The row is simply absent rather than `disconnected`.

The one part of §9.1 that is *not* inert — consuming outstanding authorization
requests — has nothing to do here: an `OAuthAuthorizationRequest` is always
created alongside its connection, and the only path that deletes a connection
(denial of a first authorization) consumes the request first. No unconsumed
request can outlive its connection, and a test asserts that.

This fills a gap §3.2 did not enumerate rather than contradicting it; it is
listed as an ambiguity in the self-review.

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_lifecycle.py -q -k disconnect
```
**Expected failure:** 404 on the route.

**Minimal implementation:** one transaction — **`locked_existing_connection`**,
`advance_generation`, delete credential, set status and clear errors *only when
the status changes*, consume outstanding requests unconditionally, audit only on
a real transition.

**Verification:** `cd apps/api && ../../.venv/bin/python -m pytest -q`

**Commit:** `feat(integrations): disconnect an integration without revoking the grant`

---

## T09 — Reconnect terminal behaviour

Implements §5.1, §5.1.1, §5.4, and §9.4.2 stages 4–5.

**Files:** `oauth_service.py`; `tests/test_oauth.py`, `tests/test_lifecycle.py`,
`tests/test_concurrency.py`

**Interfaces consumed:** `verification.verify` and
`lifecycle_service.apply_verification_outcome` with
`context=VerificationContext.RECONNECT` and
`expected_generation=request.connection_generation` — the same two functions
T07 uses, so the two paths cannot drift.

**Failing tests first** — one per row of §5.1.1, plus:
- no stored resource → `awaiting_resource_selection` (M3 behaviour intact)
- §5.4 cancellation: denial **with** a credential leaves status, credential and
  resource untouched and records `access_denied`; denial of a first
  authorization deletes the row; neither produces `error`; `ScopeNotGranted`
  still does
- stage-5 discard: a disconnect between credential persistence and verification
  → result discarded, no `connected`, no `error`

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_oauth.py tests/test_lifecycle.py -q
```
**Expected failure:** every reconnect ends in `awaiting_resource_selection`
regardless of outcome; the transient row expects `resource_unavailable` and gets
nothing.

**Verification:** `cd apps/api && ../../.venv/bin/python -m pytest -q`

**Commit:** `feat(integrations): preserve a still-valid selection across a reconnect`

---

## T10 — Change resource, and the error taxonomy

Implements §6, §12.

**Files:** `resource_service.py`, `google/errors.py`;
`tests/test_ga4_resources.py`, `tests/test_search_console_resources.py`

**Interfaces produced:** `ResourceChangeNotSupported` **removed**;
`USABLE_STATUSES` gains `ERROR`.

**Failing tests first**
- a **different** resource while `connected` succeeds, replacing id, label and
  metadata together, for **both** providers
- a failed verification leaves all four of id, label, metadata and both
  timestamps untouched
- same-resource re-submission stays idempotent
- a body-supplied label still has no effect
- a connection in `error` can be repointed
- `resource_change_not_supported` no longer exists

The existing 409 tests are **replaced**, each replacement carrying a comment
saying it pinned a restriction this task lifts.

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_ga4_resources.py tests/test_search_console_resources.py -q
```
**Expected failure:** the change is refused with 409.

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest -q
grep -rn "resource_change_not_supported" apps/api apps/web    # expect no hits
```

**Commit:** `feat(integrations): allow changing a selected resource`

---

## T11 — Audit event selection

Implements §8.1.

**Files:** `oauth_service.py`; `tests/test_audit.py`, `tests/test_oauth.py`

**Interfaces consumed:** the `previous_status` returned by T04's
`_finalize_credentials`, read under the lock before mutation.

**Failing tests first** — one per row of §8.1: `reauth_required`, `error`,
`connected` → `INTEGRATION_RECONNECTED`; `pending_authorization`/newly created,
`awaiting_resource_selection`, **`disconnected`** → `INTEGRATION_AUTHORIZED`;
exactly one event in every case; plus
`test_previous_status_is_read_before_stage_three_mutates_it`.

```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_audit.py tests/test_oauth.py -q
```
**Expected failure:** every case writes `INTEGRATION_AUTHORIZED`.

**Verification:** `cd apps/api && ../../.venv/bin/python -m pytest -q`

**Commit:** `feat(integrations): choose the authorization audit event from previous status`

---

## T12 — Frontend recovery model

Implements §7.1–7.3.

**Files:** `apps/web/lib/integrations/status.ts`;
`components/integrations/__tests__/status-badge.test.tsx`

**Interfaces produced**
```ts
type RecoveryClass = "credential" | "authorization" | "resource" | "transient";
const RECOVERY_CLASS: Record<string, RecoveryClass>;
function presentationFor(status: IntegrationStatus, errorCode: string): StatusPresentation;
```
`StatusPresentation` gains `canTestConnection: boolean`, `canDisconnect: boolean`.

**Failing tests first:** the §7.2 matrix row by row; a `resource`-class error
offers Change property as primary and **not** an authorization action; an
unknown code falls back safely; a `transient` error on `connected` keeps the
green badge; `presentationFor` is the only entry point components use.

```bash
cd apps/web && npm run test
```
**Expected failure:** `presentationFor` is not exported; matrix rows fail.

**Minimal implementation:** the class map, `presentationFor`, the two flags.
`statusPresentation` stays as a thin wrapper so M5 call sites compile until
T13–T16 move them.

**Verification:** `cd apps/web && npm run test && npx tsc --noEmit && npm run lint`

**Commit:** `feat(web): key integration recovery on status and error code`

---

## T13 — Test connection UI

**Files:** created `components/integrations/test-connection-button.tsx` + its
`__tests__`; modified `integration-card.tsx`, `lib/api/types.ts`

```ts
export function TestConnectionButton(props: {
  projectId: number | string;
  provider: string;
}): JSX.Element;
```

**Failing tests first:** posts to `…/health-check`; renders only when
`canTestConnection`; reports the outcome from the **returned entry**; calls
`router.refresh()`; absent when no resource is selected.

**Expected failure:** module not found.

**Verification:** `npm run test && npx tsc --noEmit && npm run lint`

**Commit:** `feat(web): test connection action`

---

## T14 — Change property UI

**Files:** `integration-card.tsx`; `__tests__/integration-card.test.tsx`,
`__tests__/resource-picker-dialog.test.tsx`

**Interfaces consumed:** the existing `ResourcePickerDialog`, **unchanged** —
only `triggerLabel` varies, already a prop.

**Failing tests first:** `connected` renders the picker with a **Change
property** trigger; it still posts `{resource_id}` only; a `resource`-class
error also offers it; no new component.

**Verification:** `npm run test && npx tsc --noEmit && npm run lint`

**Commit:** `feat(web): change the selected property from a connected card`

---

## T15 — Disconnect UI

**Files:** created `components/integrations/disconnect-dialog.tsx` + its
`__tests__`; modified `integration-card.tsx`

```ts
export function DisconnectDialog(props: {
  projectId: number | string;
  provider: string;
  providerName: string;
}): JSX.Element;
```

**Failing tests first:** confirmation required before the POST; the copy states
credentials are deleted **and that access is not revoked at Google**, with the
permissions link, and warns that revoking there affects every integration
sharing the authorization (§7.4); renders only when `canDisconnect`;
`router.refresh()` on success.

**Verification:** `npm run test && npx tsc --noEmit && npm run lint`

**Commit:** `feat(web): disconnect with an honest confirmation`

---

## T16 — Reconnect and recovery UI states

**Files:** `integration-card.tsx`, `connect-button.tsx`,
`__tests__/integration-card.test.tsx`

**Failing tests first:** `reauth_required` offers **Reconnect** and no test
button; `error` + `credential` → Reconnect; `error` + `authorization` → Try
again; a **transient** error on `connected` renders a muted note, not a
destructive alert (§7.5); the action row never branches on a provider — the
source-scan test is extended to the two new components.

**Verification**
```bash
cd apps/web && npm run test && npx tsc --noEmit && npm run lint
cd apps/web && NODE_OPTIONS=--max-old-space-size=640 npm run build
```

**Commit:** `feat(web): recovery actions for error and reauthorization states`

---

## T17 — Concurrency race suite

The final coverage audit. Most tests were written by the task that owned the
behaviour; this task adds the ones no single task owned and proves nothing is
missing.

**Files:** `apps/api/tests/test_concurrency.py`

**Technique.** Interleaving is produced by stubbing the *outbound call* to
perform the competing mutation before it returns — deterministic, no threads, no
sleeps. The one exception is the creation race (T02), which needs real
concurrency and uses two threads under `@pytest.mark.django_db(transaction=True)`.

### Coverage matrix — every race maps to a named test and its owning task

| § | Race | Test | Written in |
|---|---|---|---|
| 9.5 A | Stale provider 401 after reconnect | `test_stale_provider_401_after_reconnect_is_discarded` | **T07** |
| 9.5 B | Stale resource result after resource change | `test_stale_403_after_resource_change_is_discarded` | **T07** |
| 9.3.1 A1 | Stale refresh `invalid_grant` | `test_stale_invalid_grant_does_not_mark_reauth_required` | T05 |
| 9.3.1 A2 | Stale **successful** refresh | `test_stale_successful_refresh_does_not_overwrite_newer_credential` | T05 |
| 9.5 C | Disconnect **before** callback consumption | `test_disconnect_before_consumption_invalidates_the_request` | T08 |
| 9.5 C′ | Disconnect **after** callback consumption | `test_disconnect_after_consumption_discards_the_callback` | **T17** |
| 9.1 C″ | **Repeat** disconnect while Connect is in flight | `test_repeat_disconnect_supersedes_an_inflight_connect` | **T17** |
| 9.5 D | Older callback after a newer authorization | `test_older_callback_does_not_overwrite_newer_authorization` | **T17** |
| 9.4.2 | Disconnect between credential persistence and verification | `test_disconnect_between_stages_discards_the_verification` | T09 |
| 9.4.1 A | Two concurrent starts, connection exists | `test_two_starts_on_an_existing_connection_get_distinct_generations` | T02 |
| 9.4.1 B | Start racing disconnect | `test_start_racing_disconnect_last_commit_owns_the_generation` | **T17** |
| 9.4.1a C | Two concurrent **first** starts, no row | `test_two_first_authorizations_race` | T02 |
| 9.4.1a | Creation-race recovery branch | `test_creation_race_recovery_branch` | T02 |
| 9.5 | Legitimate Connect **after** disconnect | `test_connect_after_disconnect_succeeds` | **T17** |
| 9.3 | Two concurrent health checks | `test_two_concurrent_health_checks_later_discards` | **T17** |
| 9.3 | Two concurrent selections | `test_two_concurrent_selections_the_later_discards` | T06 |
| 9.3 | Discarded result writes nothing | `test_a_discarded_selection_writes_nothing_at_all` | T06 |

**Tie cases** (§14), with time frozen so every row carries an identical
timestamp — proving the generation and not a clock does the work:
- `test_tie_disconnect_second_discards_the_callback`
- `test_tie_disconnect_first_allows_the_callback`
- `test_tie_two_requests_only_the_newest_finalizes`

**Generation discipline**
- `test_generation_advances_only_on_authorization_start_and_disconnect` — a
  health check, a resource change, a token refresh and a completing callback
  leave it untouched; a repeat disconnect and a start from
  `pending_authorization` both advance it

**Fence convention** (§14)
- `test_every_mutating_service_changes_updated_at` — asserts the value
  **changed**, never that wall-clock time advanced (§9.6)

**Verification**
```bash
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py -q
cd apps/api && ../../.venv/bin/python -m pytest tests/test_concurrency.py --collect-only -q   # every matrix row collects
cd apps/api && ../../.venv/bin/python -m pytest -q
```

**Commit:** `test(integrations): cover every approved lifecycle race`

---

## T18 — Security, audit and leakage tests

**Files:** `tests/test_lifecycle.py`, `tests/test_audit.py`,
`tests/test_provider_boundary.py`

**Failing tests first**
- no token, refresh token or Google error text in any response, log line or
  audit row, for **every** new path — the `TestNoLeakage` pattern extended
- the health check reads its identifier from the database: a posted
  `resource_id` naming another resource is never called
- `external_resource_meta` remains unserialized on every new response
- one audit event per user-meaningful outcome; **no** event for a health check,
  including one that transitions the connection; no duplicate on a repeat
  disconnect
- the metadata allowlist is unchanged and every written key is in it
- `test_no_revoke_request_is_ever_made`
- the provider-vocabulary source scan extended to `lifecycle_service.py`,
  `concurrency.py`, `verification.py`, and the two new components
- **`test_only_authorization_start_can_create_a_connection`** — a source scan
  asserting `locked_or_create_connection_for_authorization` appears in exactly
  one call site under `apps/api/integrations/`, and that
  `IntegrationConnection.objects.create` / `get_or_create` appear nowhere in
  `lifecycle_service.py`, `resource_service.py` or `verification.py`
- **`test_apply_verification_outcome_never_writes_selection_fields`** — a
  parametrized check over every `(context, result)` pair asserting the three
  selection fields are byte-for-byte unchanged

**Verification:** `cd apps/api && ../../.venv/bin/python -m pytest -q`

**Commit:** `test(integrations): lifecycle security, audit and leakage coverage`

---

## T19 — Migration regression, mutation checks, staging handoff

**Files:** `docs/V1_BUILD_PLAN.md` (tick M6). The design document is **not**
edited unless implementation revealed a genuine contradiction.

### Migration regression

```bash
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run   # No changes detected
ls apps/api/integrations/migrations/                                               # 0001, 0002, 0003 only
```
- `test_existing_rows_default_to_generation_zero` — a connection and a request
  created **before** the migration both default to `0`, and a callback across
  that boundary completes normally (§11.2)

### Mutation checks — executable (review fix 4; 31 rows)

Each row: apply the mutation, run the **targeted** command, confirm the named
failure, then **`git checkout -- <file>` before the next mutation**. The full
suite runs once at the end, after every mutation has been restored — not 31
times.

Shorthand: `PY=../../.venv/bin/python`, run from `apps/api`.

| # | Mutation | Targeted command | Expected failure |
|---|---|---|---|
| 1 | Transient failure clears `last_successful_check_at` | `$PY -m pytest tests/test_lifecycle.py -k transient -q` | assert on `last_successful_check_at` — expected unchanged, got `None` |
| 2 | Disconnect blanks the credential instead of deleting the row | `$PY -m pytest tests/test_lifecycle.py -k disconnect -q` | `IntegrationCredential.objects.count()` expected 0, got 1 |
| 3 | A failed reconnect verification sets `connected` | `$PY -m pytest tests/test_lifecycle.py -k reconnect -q` | status expected `awaiting_resource_selection`, got `connected` |
| 4 | Add a revoke call to disconnect | `$PY -m pytest tests/test_lifecycle.py -k revoke -q` | `responses` ConnectionError: unregistered POST to `oauth2.googleapis.com/revoke` |
| 5 | Remove `Fence.connection_updated_at` | `$PY -m pytest tests/test_concurrency.py -k discarded -q` | stale result applied; assert on unchanged status |
| 6 | Remove `Fence.external_resource_id` | `$PY -m pytest tests/test_concurrency.py -k stale_403 -q` | connection marked `error` on the new resource |
| 7 | Remove `Fence.credential_updated_at` | `$PY -m pytest tests/test_concurrency.py -k stale_provider_401 -q` | connection moved to `reauth_required` |
| 8 | A discarded result still writes `last_health_check_at` | `$PY -m pytest tests/test_concurrency.py -k writes_nothing -q` | `last_health_check_at` expected `None`, got a datetime |
| 9 | Disconnect stops consuming outstanding requests | `$PY -m pytest tests/test_lifecycle.py -k consumption -q` | request still unconsumed |
| 10 | Remove `DISCONNECTED` from `_needs_forced_consent` | `$PY -m pytest tests/test_oauth.py -k consent -q` | `prompt=consent` expected in URL, absent |
| 11 | `_needs_forced_consent` returns False with no credential stored | `$PY -m pytest tests/test_oauth.py -k consent -q` | same, for the first-connection rows |
| 12 | Remove the `RefreshFence` entirely | `$PY -m pytest tests/test_ga4_resources.py -k refresh -q` | A1 and A2 both fail |
| 13 | Remove only its `invalid_grant` arm | `$PY -m pytest tests/test_ga4_resources.py -k invalid_grant -q` | status `reauth_required` |
| 14 | Remove only its success arm | `$PY -m pytest tests/test_ga4_resources.py -k stale_successful -q` | stale access token stored |
| 15 | `start_authorization` stops incrementing | `$PY -m pytest tests/test_concurrency.py -k generation -q` | generations equal, expected distinct |
| 16 | `disconnect` stops incrementing | `$PY -m pytest tests/test_concurrency.py -k repeat_disconnect -q` | callback resurrects the connection |
| 17 | Only the **already-disconnected** path stops incrementing | `$PY -m pytest tests/test_concurrency.py -k repeat_disconnect -q` | the revision-4 bug: credential created after disconnect |
| 18 | Compare generations with `>` instead of `!=` | `$PY -m pytest tests/test_concurrency.py -k tie -q` | tie case allows a superseded callback |
| 19 | Move the increment outside `select_for_update` | `$PY -m pytest tests/test_concurrency.py -k two_first_authorizations -q` | duplicate generations under threads |
| 20 | Remove the savepoint around the create | `$PY -m pytest tests/test_concurrency.py -k creation_race -q` | `TransactionManagementError` on the recovery `get()` |
| 21 | Remove the `IntegrityError` recovery | `$PY -m pytest tests/test_concurrency.py -k creation_race -q` | uncaught `IntegrityError` |
| 22 | Drop the stage-3 generation check | `$PY -m pytest tests/test_oauth.py -k generation_advanced -q` | credential row created after supersession |
| 23 | Drop the stage-5 generation check | `$PY -m pytest tests/test_concurrency.py -k between_stages -q` | `connected` written after a disconnect |
| 24 | Read `previous_status` after stage 3 mutates it | `$PY -m pytest tests/test_audit.py -k previous_status -q` | `INTEGRATION_AUTHORIZED` where `RECONNECTED` expected |
| 25 | Collapse the §5.1.1 transient outcome into `resource_not_accessible` | `$PY -m pytest tests/test_lifecycle.py -k reconnect_transient -q` | `last_error_code` expected `resource_unavailable` |
| 26 | Denial sets `error` | `$PY -m pytest tests/test_oauth.py -k denied -q` | status expected unchanged, got `error` |
| 27 | `error` always offers the authorization action | `cd apps/web && npm run test` (no targeted runner; the suite is 3s) | the §7.2 resource-class row fails |
| 28 | `disconnect` uses the creating acquirer instead of `locked_existing_connection` | `$PY -m pytest tests/test_lifecycle.py -k no_connection_row -q` | a connection row is created by a disconnect |
| 29 | Callback stage 3 uses the creating acquirer | `$PY -m pytest tests/test_oauth.py -k deleted_connection -q` | a deleted connection is recreated by a stale callback |
| 30 | `apply_verification_outcome` writes `external_resource_label` from the outcome | `$PY -m pytest tests/test_lifecycle.py -k selection_fields_unchanged -q` | stored label replaced by the provider's |
| 31 | `health_check` lets `CredentialRefreshFailed` propagate to the view | `$PY -m pytest tests/test_lifecycle.py -k reauth_required -q` | 409 where 200 with a persisted entry was expected |

### Full green gate — once, after all mutations are restored

```bash
git status --short                                          # clean: every mutation reverted
service postgresql start
cd apps/api && ../../.venv/bin/python -m pytest -q
cd apps/api && ../../.venv/bin/python manage.py makemigrations --check --dry-run
cd apps/web && npm run test && npx tsc --noEmit && npm run lint
cd apps/web && NODE_OPTIONS=--max-old-space-size=640 npm run build
```

**Staging handoff:** the §17 checklist becomes the PR body's verification
section, six phases intact, **revocation last**. Staging is run by the user;
this plan never claims it.

**Commit:** `chore(integrations): milestone 6 verification and build plan tick`

---

## Self-review (re-run after revision 3)

| Check | Result |
|---|---|
| **Every task finishes with its full-suite gate green** | **Re-verified.** The former T02 could not: it routed `start_authorization` through a `locked_connection` with no creation branch, so a first authorization would raise `DoesNotExist` until the former T03. Merged into T02, which now lands both branches together and lists `test_first_authorization_creates_the_connection_and_advances_generation` as a required pass at that commit. No other task changes a call path whose implementation arrives later |
| **No test depends on behaviour scheduled later** | **Re-verified and one defect fixed.** The former T07 owned `test_stale_provider_401_after_reconnect_is_discarded`, but `health_check` did not exist until the former T08 and T07 touched only `resource_service.py`. Races A and B moved to T07-new (the health-check task); T06 now covers only resource-selection staleness, which its own file genuinely performs. The T17 matrix names the owning task for every row, which is what made the mismatch visible |
| **Every internal interface has an exact contract** | **Fixed.** `VerificationResult`, `VerificationOutcome`, `VerificationContext`, `verify()` and `apply_verification_outcome()` are fully typed in T07, with the layer that catches provider exceptions named (`verify`, and only there), the (context, result) status table written out, and the statement that resource selection does **not** use the mapper |
| **Every mutation has a concrete red test** | **Fixed.** All 27 rows carry a targeted command and a named failure. Only #27 runs a whole suite, because the frontend has no targeted runner and the suite takes ~3s. Restore-before-next is stated, and the full suite runs once at the end |
| **Commit list matches task numbering** | **Verified.** 19 tasks, T01–T19, index and bodies agree; no gaps and no duplicate commit subjects |
| **Three fences distinct** | Unchanged: generation (T02/T04/T08/T09), `Fence` (T02 defines, T06/T07 use), `RefreshFence` (T05, inside `credentials.py`) |
| **Provider-specific logic** | No task modifies `ga4.py`, `search_console.py` or `resources.py`. `verify()` catches the project's own provider-neutral error classes, never a provider response. T18 extends the source scan to all three new modules |
| **Scope expansion** | None. Mutation 4 asserts the revoke endpoint stays unused |
| **Migration discipline** | One migration (T01, explicit A–G sequence), re-verified in T19 |
| **Creation capability is explicit** | **Fixed in revision 3.** A single `locked_connection` let any caller create a row, so `disconnect` could have created the connection it was ending and a stale callback could have resurrected a deleted one. Split into `locked_existing_connection` (never creates) and `locked_or_create_connection_for_authorization` (the only creating entry point, named so). T02 carries the caller table; T18 asserts the creating function has exactly one call site |
| **Selection fields written only by the selection path** | **Fixed in revision 3.** `apply_verification_outcome`'s table had a resource column writing the outcome's resource on success, which is broader than the design allows. The column is gone, the three fields are absent from its `update_fields`, and T07/T09 assert byte-for-byte stability — including when the provider returns a different label than the one stored |
| **Pre-verification failures have a defined path** | **Fixed in revision 3.** `access_token_for` can raise before `verify()` runs. `CredentialMissing` propagates as 409 (the check never began); `CredentialRefreshFailed` and `ResourceUnavailable` convert through the same single table and return 200 with the persisted state. The "only place errors are converted" claim is corrected to name the table rather than the function |

### Design ambiguities

Two, unchanged from revision 1, and **no genuine design contradiction was
discovered**:

1. **Which module owns the shared verification mapper.** §10 requires one copy
   of the failure semantics but does not name its home. `verify()` lives in the
   new `verification.py` (types and classification) and
   `apply_verification_outcome()` in `lifecycle_service.py` (the writer), so
   `oauth_service` imports the pair rather than carrying §4.3's taxonomy.
2. **Where `Fence` lives.** §9.3 defines it without naming a module. It sits in
   `concurrency.py` beside the generation primitives so all three mechanisms are
   visible together, while `RefreshFence` is *used* only inside
   `google/credentials.py` as §9.3.1 requires.

3. **Disconnect when no connection row exists.** §3.2 enumerates `200` and
   `404`, with `404` reserved for an unknown project or provider — which this is
   not. The plan resolves it as **200 with the synthesized `not_connected`
   entry, creating and writing nothing**, because disconnect's externally
   meaningful result is already true, which is §9.1's own definition of an
   idempotent disconnect. This fills a gap the design did not enumerate rather
   than contradicting it. If review prefers `409`, it is a one-line change to
   T08.

The first two are placement choices within the design's rules; the third is an
unenumerated case resolved by the design's own idempotency principle. **No
semantic change, and no design contradiction was found in any revision.**
