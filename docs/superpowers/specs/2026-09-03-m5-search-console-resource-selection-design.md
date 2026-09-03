# Milestone 5 — Search Console site discovery, selection, verification

Design draft. 2026-09-03. Revised after review; not implemented; not approved.

**Revision — what the review asked for and where it lives:** a concrete
refactor migration strategy with the final interfaces written out (§4.1–§4.4);
the shared resource contract stated field by field, with a leak audit (§4.5);
explicit accept/reject rules per `permissionLevel`, each with its reason (§7.3);
the provider-neutral UI model and component API defined before any component
changes (§12); exact endpoints and payloads with a compatibility statement
(§11); audit, credential and token-leakage confirmations (§14, §15); and the
M5 staging acceptance checklist (§23).

Does for Search Console what Milestone 4 did for GA4: discover the sites the
granted Google account can actually use, let the user pick one, verify that
exact site server-side, persist it, and move the connection to `connected`.

It is also where the build plan says the provider abstraction gets decided —
M4 deliberately left the boundary GA4-specific, and this milestone is the first
moment there are two real implementations to generalize from rather than one.

---

## 0. Verified against current Google documentation (2026-09-03)

Re-read at the start of this milestone, as `docs/V1_BUILD_PLAN.md` requires.

| Fact | Value |
|---|---|
| Base URL | `https://www.googleapis.com/webmasters/v3` — **not** `searchconsole.googleapis.com`, which serves URL Inspection (`/v1`) only |
| List | `GET /sites`, no request body |
| List response | `{"siteEntry": [ …Sites resource… ]}` |
| **No pagination** | `sites.list` documents **no** `pageSize`, `pageToken`, or `nextPageToken` — one call returns everything |
| Get | `GET /sites/{siteUrl}` — the identifier is a **path parameter** |
| Sites resource | exactly two fields: `siteUrl` (string), `permissionLevel` (string) |
| `siteUrl` forms | `http://www.example.com/` (URL-prefix property) or `sc-domain:example.com` (Domain property) |
| `permissionLevel` values | `siteOwner`, `siteFullUser`, `siteRestrictedUser`, `siteUnverifiedUser` |
| Scope | `https://www.googleapis.com/auth/webmasters.readonly` is sufficient — already what the provider requests and what M3 obtains |

Sources: [sites.list](https://developers.google.com/webmaster-tools/v1/sites/list),
[sites.get](https://developers.google.com/webmaster-tools/v1/sites/get),
[Sites resource](https://developers.google.com/webmaster-tools/v1/sites),
[API reference index](https://developers.google.com/webmaster-tools/v1/api_reference_index).

### Four differences from GA4 that drive this whole design

1. **No pagination.** M4's paging loop, `MAX_PAGES` cap and `truncated` flag have
   no counterpart here. `truncated` is always false for this provider.
2. **The identifier is a URL, not a path segment.** `properties/123456` drops
   into a URL untouched; `https://example.com/` must be percent-encoded into a
   single path segment (§7).
3. **No grouping.** GA4 properties belong to accounts, which the picker groups
   by. A Search Console site has no parent, so `group_label` is empty (§12).
4. **The list carries an authorization signal.** `permissionLevel` can say
   `siteUnverifiedUser` — a site the account knows about but is not verified
   for. GA4 has no equivalent, and this one has real security weight (§7).

---

## 1. Inspection of `main` (`ae33372`) — what materially shapes this

| # | Finding | Consequence |
|---|---|---|
| 1 | `IntegrationConnection` already stores everything needed; M4 needed no migration. | **No migration again.** `external_resource_id` is `CharField(255)`, which bounds the accepted `siteUrl` length (§7). |
| 2 | `google/credentials.py` is provider-agnostic — it reads `connection.granted_scopes` and refreshes. | **Reused unchanged.** M5 adds no credential code. |
| 3 | `google/errors.py` codes are already provider-neutral (`resource_not_accessible`, `invalid_resource_id`, `resource_unavailable`, …). | **Reused unchanged**, except the two GA4-worded user messages (§13). |
| 4 | `resource_service.py` names `ga4` in **four** places: the `DiscoveredResources` type, `is_valid_property_id`, `list_properties`, `get_property`, behind one `SUPPORTED_RESOURCE_PROVIDERS` check. | These four are exactly the seam. §4 is about what replaces them. |
| 5 | `IntegrationProvider` is still metadata-only (`key`, `display_name`, `description`, `oauth_scopes`). | The natural home for a catalog reference, if §4 chooses one. |
| 6 | The Search Console provider entry exists with the right scope and copy, and its connection reaches `awaiting_resource_selection` today — staging confirmed it sitting there. | M5 adds no OAuth work. The connection is already waiting for exactly this feature. |
| 7 | **The picker is not reusable as written.** `resource-picker-dialog.tsx` hard-codes "No Google **Analytics** properties are available…", and the serializer field is `property_type`. | The build plan says: if the frontend is not reused unchanged, the boundary is wrong and gets fixed here. §12 fixes it — and the fix is small, because both providers call these things "properties". |
| 8 | `DiscoveredResourceSerializer` exposes `id`, `label`, `account_label`, `property_type`. | `account_label` and `property_type` are GA4 vocabulary in a shared payload. §11 renames them once, now, rather than adding a second shape. |
| 9 | M4's rule — the stored label comes from the verification response, never the request body — is pinned by mutation-tested regressions. | M5 inherits the rule and the test pattern verbatim. |
| 10 | `requests` is already a direct dependency; `responses` already stubs it. | **No dependency change at all** in M5. |

---

## 2. Scope

**In:** Search Console site discovery; selection; independent server-side
verification of the selected site, which doubles as its initial health
verification; `siteUrl` path encoding for both documented forms; exclusion of
unverified sites at **both** discovery and verification; the
`awaiting_resource_selection → connected` transition for this provider; the
provider-boundary decision (§4); the frontend copy fix that makes one picker
serve both providers; tests.

**Out:** Changing an existing selection, the on-demand health check, **Test
connection**, reconnect, disconnect and revocation — all still Milestone 6.
`sites.add` and `sites.delete` — this product never modifies a user's Search
Console account, only reads it. Search analytics data, queries, impressions,
clicks — V1 stores connection configuration, never historical data. URL
Inspection API. Any third provider.

---

## 3. User flow

Identical to GA4's, which is the point.

1. The Search Console card reads **Select a property** with a **Choose
   property** button.
2. The dialog fetches `GET …/search_console/resources`.
3. A flat list of sites: the site URL as the label, and whether it is a Domain
   or URL-prefix property. Sites the account is not verified for never appear.
4. The user picks one and confirms; the browser posts
   `{"resource_id": "sc-domain:example.com"}` and nothing else.
5. The backend refreshes the token if needed, calls `sites.get` on that exact
   site, checks the returned `permissionLevel`, and only then persists.
6. The card reads **Connected** with the site and "Last successful access: just
   now".

---

## 4. The provider boundary — the decision M4 deferred

M4 wrote: *"with one implementation, an abstraction would be a guess about the
second one… Milestone 5 adds Search Console — whose identifiers are URLs
needing path encoding — and decides the shape then, from two real cases."*
Here are the two real cases. They share exactly three operations — validate an
identifier, list resources, verify one — and differ in everything else.

### Option A — extract a `ResourceCatalog` protocol (recommended)

`integrations/resources.py` defines the shared value type and protocol; each
provider module implements it; `IntegrationProvider` gains
`resources: ResourceCatalog | None`; `resource_service.py` stops naming any
provider at all.

```python
@dataclass(frozen=True)
class RemoteResource:
    id: str            # provider-issued, stored verbatim
    label: str         # from the provider's verification response
    group_label: str   # "" when the provider has no grouping
    resource_type: str # display only: "PROPERTY_TYPE_ORDINARY", "Domain property"
    meta: dict         # small, non-sensitive; what gets stored

class ResourceCatalog(Protocol):
    def is_valid_resource_id(self, resource_id: str) -> bool: ...
    def list_resources(self, access_token: str) -> ResourceListing: ...
    def verify_resource(self, access_token: str, resource_id: str) -> RemoteResource: ...
```

- **+** `resource_service.py` becomes genuinely provider-agnostic: one lookup,
  no branches, and adding a provider later touches no shared file.
- **+** The three methods are not a guess — each is a call site that exists in
  M4's code today and will exist twice after M5.
- **+** `ResourceListing.truncated` stays in the shared type and is simply
  always false for Search Console, which is honest rather than special-cased.
- **−** Touches M4 code: `Ga4Property` becomes `RemoteResource`,
  `property_type` becomes `resource_type`, and four call sites move. Real
  churn in a milestone that is otherwise additive.
- **−** A `Protocol` for two implementations is the smallest amount of
  ceremony that still counts as ceremony.

### Option B — a second explicit branch

Add `elif provider_key == ProviderKey.SEARCH_CONSOLE: …` at each of the four
dispatch points in `resource_service.py`.

- **+** Zero churn in M4 code; the smallest possible diff.
- **+** Nothing to learn: the branching is visible at every call site.
- **−** Four parallel two-armed branches that must be kept in step, in the file
  that holds the security-critical ordering (validate → guard → refresh →
  verify → persist). A branch added to three of four places and forgotten in
  the fourth is a plausible bug, and it is the kind that fails open.
- **−** The build plan's own criterion — "if the frontend is not reused
  unchanged, the provider boundary is wrong and gets fixed here" — points the
  other way: the payload field names (`account_label`, `property_type`) are
  already leaking GA4 vocabulary into shared code, and B leaves that leak in.

### Option C — a module registry, no protocol

`_CATALOGS = {ProviderKey.GA4: ga4, ProviderKey.SEARCH_CONSOLE: search_console}`,
relying on both modules exposing the same three function names.

- **+** Nearly as clean as A at the call site, with no `Protocol` and no
  dataclass rename if the two modules keep their own return types.
- **−** The interface exists but is unwritten, so nothing states it and nothing
  checks it; a typo in a function name is an `AttributeError` at request time
  rather than something a reader or a type checker sees.
- **−** Still needs one shared return type for the serializer, which is most of
  A's churn without A's explicitness.

### Recommendation: **A**

The build plan reserved this decision for the moment there were two
implementations, and the two turn out to share precisely three operations and
one payload shape. That is the evidence A was waiting for. The churn is bounded
and mechanical — a rename plus four moved call sites, all covered by M4's
existing tests, which is exactly the safety net that makes doing it now cheaper
than doing it later with a third provider in the way.

**Explicitly still not built:** a plugin loader, entry points, dynamic import,
per-provider settings, capability flags, a registry decorator, or any method
beyond the three below. The protocol has three methods because three call sites
exist. A fourth method is added when a fourth call site exists in two
implementations, not before.

---

## 4.1 The final interfaces, in full

Written out here so the refactor is reviewable before a line is changed.

`integrations/resources.py` — new, and the only new shared module:

```python
@dataclass(frozen=True)
class RemoteResource:
    """One selectable external resource, in provider-neutral terms."""
    id: str
    label: str
    resource_type: str = ""
    group_label: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceListing:
    resources: tuple[RemoteResource, ...]
    truncated: bool = False


class ResourceCatalog(Protocol):
    """What a provider must do to support resource selection.

    Three methods, one per call site that exists in resource_service today.
    Implementations are stateless modules or simple objects; none touches the
    database, and none may return a provider response object.
    """

    def normalize_resource_id(self, resource_id: str) -> str:
        """The canonical form of a client-supplied identifier.

        Raises InvalidResourceId if it is not a well-formed identifier for this
        provider. Returns the value to use from then on — for both providers
        today that is the input unchanged, but the call site is the one place a
        provider could trim or case-fold, and having it means no caller ever
        needs to know whether one does.
        """

    def list_resources(self, access_token: str) -> ResourceListing: ...

    def verify_resource(self, access_token: str, resource_id: str) -> RemoteResource:
        """Prove this credential can use this exact resource, or raise.

        Raises ResourceNotAccessible when the provider says no — including a
        provider that answers 200 while withholding permission (§7.3).
        """
```

`integrations/providers/base.py` — one added field, defaulted so nothing else
changes:

```python
@dataclass(frozen=True)
class IntegrationProvider:
    key: str
    display_name: str
    description: str
    oauth_scopes: tuple[str, ...]
    #: None means this provider has no resource selection; the endpoints 404.
    resources: ResourceCatalog | None = None
```

`resource_service.py` — the dispatch, replacing `SUPPORTED_RESOURCE_PROVIDERS`:

```python
def _catalog(provider_key: str) -> ResourceCatalog:
    provider = get_provider(provider_key)
    if provider is None or provider.resources is None:
        raise ResourceSelectionUnsupported
    return provider.resources
```

After this, `resource_service.py` contains no provider name, no `import ga4`,
and no branch on `provider_key`.

## 4.2 Exact M4 files that change, and how

| File | Change | Risk |
|---|---|---|
| `google/ga4.py` | `Ga4Property` → returns shared `RemoteResource`; `property_type`/`account_id`/`account_label` fold into the shared fields per §4.5; `as_metadata()` becomes the `metadata` field, built identically; `is_valid_property_id` → `normalize_resource_id` (same regex, now raising instead of returning False); module gains a `CATALOG` object exposing the three methods. **No change to any URL, parameter, page size, sort order, status mapping or stored value.** | Medium — most of the diff |
| `providers/google_ga4.py` | `resources=ga4.CATALOG` | None |
| `providers/google_search_console.py` | `resources=search_console.CATALOG` | None |
| `providers/base.py` | one optional field | None |
| `resource_service.py` | four `ga4.*` call sites → `catalog.*`; `SUPPORTED_RESOURCE_PROVIDERS` → `_catalog()`; `DiscoveredResources` → the shared `ResourceListing` | Medium — security-ordered code |
| `serializers.py` | `account_label` → `group_label`, `property_type` → `resource_type` | Low |
| `tests/test_ga4_resources.py` | **renames only** (§4.3) | This is the control |
| `apps/web/lib/api/types.ts` | two field renames | Low |

Nothing else in M4 is opened. `google/credentials.py`, `google/errors.py`,
`models.py`, `views.py`, `urls.py`, `audit/` and every deployment file are
untouched.

## 4.3 How the M4 acceptance tests are preserved

The rule for this milestone: **`tests/test_ga4_resources.py` may change only
where a symbol was renamed.** Concretely, the permitted edits are

* `Ga4Property` → `RemoteResource` in the one place a test constructs one,
* `property_type` → `resource_type` and `account_label` → `group_label` in
  response assertions,
* `ga4.is_valid_property_id` → the catalog's `normalize_resource_id` in the
  one test that calls it directly.

Every assertion value stays as it is. Specifically these must pass **unedited**,
because they are the M4 acceptance criteria this refactor could silently break:

- `test_a_label_in_the_request_body_has_no_effect`
- `test_forbidden_and_missing_are_indistinguishable`
- `test_malformed_identifiers_never_reach_google` (all six parameters)
- `test_metadata_is_minimal_and_carries_no_timestamp`
- `test_selection_does_not_reassign_connected_by`
- `test_one_audit_event_records_the_whole_transition`
- `test_persisting_never_blanks_a_stored_refresh_token`
- the whole `TestConnectedComesOnlyFromVerification` class
- the whole `TestNoLeakage` class

If any of those needs a changed **value** rather than a changed **name**, the
refactor altered behaviour and is wrong — stop and bring it back to review
rather than updating the expectation. That is the entire safety argument for
Option A, so it is a rule, not a hope.

Sequencing that makes it enforceable: the refactor is **commit 1**, containing
no Search Console code at all, and the GA4 suite runs green at that commit
before `search_console.py` is written. A refactor validated only after a second
provider exists cannot tell which change broke what.

## 4.4 GA4 behaviours that must remain byte-for-byte identical

Asserted by the unedited tests above, and re-checked by hand in review:

| Behaviour | Value that must not move |
|---|---|
| Discovery request | `GET {base}/accountSummaries`, `pageSize=200`, `pageToken` on later pages |
| Page cap | `MAX_PAGES = 10`, then `truncated: true` |
| Sort order | `(group_label, label, id)` — the same tuple, under the new field names |
| Identifier format | `^properties/[0-9]{1,32}$`, unchanged |
| Verification request | `GET {base}/properties/{id}`, no query parameters |
| Status mapping | 401 → `CredentialRefreshFailed`; 403/404 → `ResourceNotAccessible`; 429/5xx → `ResourceUnavailable`; other → `GoogleApiError` |
| Stored id | Google's `name` when well-formed, else the requested id |
| Stored label | `displayName`, falling back to the id |
| Stored metadata | exactly `{"account": …, "property_type": …}` — see §4.5 for how that survives a neutral field name |
| Timeout | `GOOGLE_API_TIMEOUT_SECONDS` |
| Health writes | both timestamps on success; neither on failure |

## 4.5 GA4 concepts must not leak into the shared layer

The review named `account`, `property_type` and `account_display_name`. Here is
where each one ends up.

| GA4 concept | Shared layer? | Where it lives |
|---|---|---|
| `property_type` (`PROPERTY_TYPE_ORDINARY`) | **No** | A *value* of the neutral `resource_type` field. Search Console puts `"Domain property"` there. The shared layer knows a resource has a type; only `ga4.py` knows what GA4's types are called |
| `account` (`accounts/123`) | **No** | Inside `metadata`, an opaque `Mapping[str, str]` the shared layer never reads or names |
| `account_display_name` | **No** | Not stored at all — dropped in M4 because `properties.get` does not return it. Nothing reintroduces it |
| Account grouping | **No** | The neutral `group_label`. GA4 fills it with an account name; Search Console leaves it `""` |
| `Ga4Property` type | **No** | Deleted; `RemoteResource` replaces it |
| `accountSummaries`, `properties/`, page size, the GA4 base URL | **No** | Confined to `google/ga4.py`, as in M4 |

The test that keeps this honest: a grep-style assertion in the shared test
module that the strings `properties/`, `accountSummaries`, `analyticsadmin`,
`sc-domain`, `webmasters` and `siteEntry` appear in **no** file under
`integrations/` other than `google/ga4.py` and `google/search_console.py`. A
leak becomes a failing test rather than a review catch.

---

## 5. Backend architecture

```
integrations/
  resources.py          NEW  RemoteResource, ResourceListing, ResourceCatalog
  google/
    credentials.py      (unchanged)
    ga4.py              MODIFIED  returns RemoteResource; unchanged behaviour
    search_console.py   NEW  sites.list / sites.get, siteUrl encoding
    errors.py           MODIFIED  two user-facing messages made provider-neutral
  providers/
    base.py             MODIFIED  + resources: ResourceCatalog | None
    google_ga4.py       MODIFIED  supplies the GA4 catalog
    google_search_console.py  MODIFIED  supplies the Search Console catalog
  resource_service.py   MODIFIED  dispatches through the provider; names neither
  serializers.py        MODIFIED  account_label → group_label, property_type → resource_type
```

Boundaries kept: nothing outside `google/search_console.py` knows the word
"Search Console" or the shape of a `siteUrl`; `resource_service.py` names no
provider; `projects` still imports nothing from `integrations`.

---

## 6. Discovery

`search_console.list_sites(access_token)`:

- `GET {BASE}/sites`. One call. **No paging loop** — the API documents none, so
  inventing one would be inventing a contract.
- Read `siteEntry[]`. For each entry:
  - skip anything without a `siteUrl`;
  - **skip `permissionLevel == "siteUnverifiedUser"`** (§7);
  - build a `RemoteResource` with `id = siteUrl`, `label = siteUrl`,
    `group_label = ""`, `resource_type` derived from the id itself —
    `"Domain property"` for `sc-domain:`, `"URL-prefix property"` otherwise.
- Sort by `(label,)` for a stable order Google does not promise.
- `ResourceListing(resources=…, truncated=False)` — always false, and the
  reason is documented in the code rather than left to be guessed.
- Same 10 s timeout and the same status→error mapping as GA4, which now live in
  a small shared helper since both modules need identical behaviour.

`label = siteUrl` is deliberate: Search Console has no display name, and
inventing a prettier one (stripping the scheme, say) would mean showing the
user something that is not the identifier they will see in Google's own UI.

---

## 7. Selection, encoding, and the permission rule

## 7.1 Identifier validation, before any outbound call

Two documented forms, and nothing else:

```
^sc-domain:[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$
^https?://[A-Za-z0-9.\-:\[\]]+/[^\s?#]*$
```

Plus a hard length cap of 255, matching the column that stores it. Anything
else is `invalid_resource_id` with no request made — the M4 rule, unchanged.

## 7.2 Path encoding

`sites.get` takes the identifier as a **path parameter**, so it is percent-
encoded whole, into one segment:

```python
quote(site_url, safe="")   # https://example.com/ → https%3A%2F%2Fexample.com%2F
```

`safe=""` is the entire point: the default would leave `/` bare and address a
different, probably nonexistent, resource. There is a second hazard worth a
test of its own — `requests` re-quotes URLs it is given, and only *unreserved*
characters are unquoted on the way through, so `%2F` survives. That is
behaviour this design depends on, so a test asserts the outbound URL contains
`%2F` and no bare slash after `/sites/` rather than trusting it (§17).

## 7.3 The permission rule — the security point of this milestone

`sites.get` returns **200** for a site the account is merely aware of but not
verified for, with `permissionLevel: "siteUnverifiedUser"`. A 200 is therefore
**not** sufficient evidence here, which is a genuine difference from GA4 where
any 200 means readable. The body decides.

The criterion is not "how much power does this level have" but **"can this
level read the data this integration exists to read"** — Search Console's
Performance report, via `webmasters.readonly`, which is the only scope we hold.

| `permissionLevel` | Decision | Why |
|---|---|---|
| `siteOwner` | **Accept** | Verified ownership; full read access to Performance data. The strongest possible answer to "can this credential use this site". |
| `siteFullUser` | **Accept** | Verified delegated access with full view rights on all reports. Reads exactly what an owner reads; the differences are administrative, and this integration administers nothing. |
| `siteRestrictedUser` | **Accept** | Verified user with, in Google's words, "simple view rights on most data". The permissions table grants Performance to owners, full users **and** restricted users alike — the restrictions are on editing (Change of Address, property settings), never on reading the report we need. Rejecting it would lock out a real and common case — agencies and contractors are routinely granted restricted access — while protecting nothing, since a restricted user can already read this data in the UI and through the API. Verified 2026-09-03 against Google's [user permissions documentation](https://support.google.com/webmasters/answer/7687615). |
| `siteUnverifiedUser` | **Reject** | Not verified for the site. Google returns 200 because the *account* knows the site exists, not because it may read it. Accepting this is precisely the hole: any client could post a guessed site URL and reach `connected` on a property from which it can read nothing. |
| missing / empty / unrecognized | **Reject** | Absence of a permission signal is not permission. An unrecognized future value is rejected for the same reason: the safe reading of "I do not know what this means" is "not proven". A new Google level that should be accepted is a one-line, deliberate change with a test — never something that starts working silently. |

So the check is an allowlist, never a denylist:

```python
ACCEPTED_PERMISSION_LEVELS = frozenset(
    {"siteOwner", "siteFullUser", "siteRestrictedUser"}
)

if payload.get("permissionLevel") not in ACCEPTED_PERMISSION_LEVELS:
    raise ResourceNotAccessible
```

An allowlist is the whole point: a denylist that names only `siteUnverifiedUser`
would accept any value Google adds later, sight unseen.

Filtering the *list* is not enough on its own, because M4 established that the
list is a convenience and the verification call is the authority — so the
authority carries the check. The list filters too, for a different reason: not
security, but not offering the user a choice that is going to be refused.

Rejection is deliberately indistinguishable from 403/404: the same
`resource_not_accessible` code and the same message. Telling a caller "this
exists but you are unverified" is the same existence oracle M4 closed.

Everything else follows M4 exactly: 403/404 collapse into one
`resource_not_accessible`; 401 means the credential is done and the connection
moves to `reauth_required`; 429/5xx/timeout are `resource_unavailable` with no
state change; the stored label comes from the verification response.

---

## 8. Persistence

Unchanged from M4 — same locked write, same fields, same single save, same
audit event. Only the metadata differs, and stays as minimal:

```
external_resource_id     = "sc-domain:example.com"   (or "https://example.com/")
external_resource_label  = the same identifier, from the verification response
external_resource_meta   = {"permission_level": "siteOwner"}
```

One key. `permission_level` is stable, non-sensitive, and is the one fact worth
keeping — it is why the site was accepted. No timestamp (the health fields own
that), no raw response object, and `connected_by` is not touched.

---

## 9. State transitions

Identical to M4's table, per provider, with one addition:

| From | Event | To | Notes |
|---|---|---|---|
| `awaiting_resource_selection` | `sites.get` 200 **and** verified permission | `connected` | one `INTEGRATION_RESOURCE_SELECTED` event |
| `awaiting_resource_selection` | 200 with `siteUnverifiedUser` | *unchanged* | **400** `resource_not_accessible` — a 200 that is not permission |
| `awaiting_resource_selection` | 403/404 | *unchanged* | 400, indistinguishable from the above |
| `connected` | same id re-submitted | `connected` | idempotent |
| `connected` | different id | *unchanged* | 409 `resource_change_not_supported` |
| any | `invalid_grant` | `reauth_required` | shared credential path |
| any | 5xx / timeout | *unchanged* | 503 |

The two providers are independent: connecting Search Console neither reads nor
writes the GA4 connection, and each has its own row, credential and state.

---

## 10. Health semantics

Unchanged from M4. The verifying `sites.get` is the initial health check and
stamps both timestamps; discovery touches neither; on-demand re-checking is
still Milestone 6.

---

## 11. API contracts

### 11.1 The exact endpoints — both already exist

M5 adds **no endpoint**. The two M4 routes simply start answering for a second
provider, because `{provider}` was always a path variable:

```
GET  /api/projects/{project_id}/integrations/{provider}/resources
POST /api/projects/{project_id}/integrations/{provider}/resource
```

`{provider}` ∈ `ga4` | `search_console`. No new route, no version prefix, no
per-provider path.

### 11.2 Discovery response

```json
{
  "resources": [
    { "id": "sc-domain:example.com",
      "label": "sc-domain:example.com",
      "resource_type": "Domain property",
      "group_label": "" },
    { "id": "https://shop.example.com/",
      "label": "https://shop.example.com/",
      "resource_type": "URL-prefix property",
      "group_label": "" }
  ],
  "truncated": false
}
```

The same endpoint for GA4, unchanged in shape, only in field names:

```json
{
  "resources": [
    { "id": "properties/549483499",
      "label": "poolino",
      "resource_type": "PROPERTY_TYPE_ORDINARY",
      "group_label": "Acme Ltd" }
  ],
  "truncated": false
}
```

### 11.3 Selection request and response

```
POST /api/projects/7/integrations/search_console/resource
{ "resource_id": "sc-domain:example.com" }
```

Response 200 is the provider's `IntegrationEntry`, exactly the shape the
Integrations page already renders — unchanged from M4, for both providers.
Errors are unchanged: 400 `invalid_resource_id` / `resource_not_accessible`,
409 `credential_missing` / `credential_refresh_failed` /
`resource_change_not_supported`, 503 `resource_unavailable`, 404 for an unknown
project, provider, or a provider with no catalog.

### 11.4 Compatibility statement

| Question | Answer |
|---|---|
| Does any endpoint break an M4 client? | **No route, method, request body, status code or error code changes.** The one breaking-shaped change is two field renames in the discovery response — `account_label` → `group_label`, `property_type` → `resource_type` — consumed by exactly one client, `resource-picker-dialog.tsx`, in this repository, updated in the same commit. |
| Is that rename safe? | The API is pre-release, unversioned, has no external consumers, and its TypeScript types are a hand-maintained mirror in the same monorepo. The alternative — keeping GA4 field names in a payload that serves both providers — is a permanent cost to avoid a one-commit one. |
| Does the **stored** shape change? | No. `external_resource_id`, `external_resource_label` and `external_resource_meta` keep their meanings and, for GA4, their exact values (§4.4). |
| Migration required? | **No.** No model field is added, removed, altered or renamed; `makemigrations --check` must stay clean, and that is an acceptance criterion (§21). |
| New dependency? | **None**, and no justification is therefore needed. `requests` is already direct, `responses` already stubs it, no frontend package is added, and both lock files must come back byte-identical. |

---

## 12. Frontend — the provider-neutral UI model

Defined before the component is touched, as the review asked.

### 12.1 What the UI is allowed to know

The picker knows it is choosing **one resource from a list, for some provider**.
That is the whole model. It is given everything else.

| The UI may know | The UI must never know |
|---|---|
| There is a list of resources, each with an id and a label | That GA4 exists, or Search Console |
| A resource *may* have a type and a group, both optional strings | What any type or group value means, or that GA4 has accounts |
| The provider has a display name, supplied to it | Any provider key, `properties/`, `sc-domain:`, or any identifier syntax |
| The list may be truncated | Why, or which providers can truncate |

Three rules follow, and they are the acceptance test for "provider-neutral":

1. **No GA4 terminology.** The strings "Analytics", "GA4", "account" and
   "property type" do not appear in the component. This is asserted by a test,
   not by review (§17).
2. **No provider-specific empty state.** The empty state is composed from the
   `providerName` it was given: *"No properties are available to this Google
   account in {providerName}."* — plus the same neutral guidance about asking
   for access or connecting a different account.
3. **No grouping assumption.** Grouping is driven by data, not by provider:
   if **every** resource has an empty `group_label`, the list renders flat with
   no legend; otherwise it renders grouped. Neither branch names a provider.
   (Today's code would render a spurious "Other" heading for Search Console.)

"Property" survives as the user-facing noun because both Google products use it
— GA4 has properties, Search Console has Domain and URL-prefix properties. It
is the products' shared word, not GA4 vocabulary.

### 12.2 Proposed component API

```ts
interface ResourcePickerDialogProps {
  /** Route parameters. The component builds no identifier of its own. */
  projectId: number | string;
  provider: string;

  /** Human name of the provider, for copy only — never compared or branched on.
   *  Supplied by the card from entry.display_name. */
  providerName: string;

  /** Optional trigger label; defaults to "Choose property". */
  triggerLabel?: string;
}
```

One added prop — `providerName` — and it is the only difference from M4's
component signature. The card supplies it from data it already holds:

```tsx
{resourceAction === "select" ? (
  <ResourcePickerDialog
    projectId={projectId}
    provider={entry.provider}
    providerName={entry.display_name}
  />
) : null}
```

`entry.provider` continues to be passed straight through into the request path
and never inspected. `providerName` is interpolated into copy and never
compared. Neither is used in a conditional anywhere in the component — that is
what makes the neutrality claim checkable rather than aspirational.

### 12.3 What does not change

`status.ts` needs no edit: "Select a property" is already accurate for both
providers, and `resourceAction` is provider-independent. No new component, no
new shadcn primitive, no second dialog, no route changes. `types.ts` follows
the two field renames. `resource_type` is rendered beside the label when
present, so "Domain property" and "URL-prefix property" distinguish two similar
entries — one conditional on a value being non-empty, not on which provider
supplied it.

---

## 13. Error taxonomy

No new classes. Two existing messages are GA4-worded and become neutral:

| Code | Now | Becomes |
|---|---|---|
| `resource_not_accessible` | "That **property** is not available to the connected Google account. Choose one from the list." | unchanged wording, already neutral — both products say "property" |
| `invalid_resource_id` | "That is not a valid **property** identifier." | unchanged — still accurate |

On inspection the messages are already provider-neutral, because both Google
products use the word "property". The taxonomy needs no edit at all; this
section records that it was checked rather than assumed.

---

## 14. Audit — confirmations

| Question | Confirmation |
|---|---|
| Same `INTEGRATION_RESOURCE_SELECTED` event reused? | **Yes.** The identical `record_event` call in `_persist_selection`, unchanged, for both providers. `provider` in the metadata is what distinguishes them. |
| Any new audit action? | **No.** `AuditEvent.Action` gains nothing. `INTEGRATION_CONNECTED` stays declared-but-unwritten, as decided in M4. |
| Any metadata allowlist change? | **No.** The five keys written are `provider`, `resource_id`, `resource_label`, `status`, `previous_status` — all already in `ALLOWED_METADATA_KEYS`. |
| How many rows per selection? | Exactly one, asserted by test, as in M4. None on a rejected selection. |
| Anything new in a row? | The values of `resource_id` and `resource_label` are now a site URL rather than `properties/N`. Both keys were already allowlisted and the value is non-sensitive — a site the user administers, not credential material. A note, not a change. |

## 15. Security — confirmations

| Question | Confirmation |
|---|---|
| Credential handling changes? | **None.** `google/credentials.py` is not in the modified-files list. No change to refresh, storage, encryption, `MultiFernet`, expiry handling, or the never-blank-a-refresh-token rule. M5 calls `access_token_for()` and nothing more. |
| New OAuth scope? | **No.** `webmasters.readonly` is what the provider has requested since M2 and what M3 already obtains. Read-only, and the minimum. |
| New token leakage path? | **No.** The one new outbound-call site (`search_console.py`) reuses M4's rules: the bearer token is a local variable, only status codes are logged and never bodies, and no Google error text enters an exception, a response or a log. Asserted by the same `TestNoLeakage` pattern for the new provider (§17). |
| New data stored? | One metadata key, `permission_level`. Non-sensitive, provider-issued, and the reason the site was accepted. `external_resource_meta` remains unserialized. |
| Tenancy? | Unchanged. Project resolved via `get_project_for_user` first; connection found by `(project, provider)` from the resolved project, so no client-supplied id is ever trusted; cross-tenant is 404. |
| New attack surface? | Two, both handled: the identifier is attacker-influenced text entering a URL **path**, validated against the two documented forms and then encoded with `safe=""` so no traversal segment or injected query string can redirect the call (§7.2); and a 200 response that withholds permission, refused by allowlist (§7.3). |
| Existence oracle? | Closed the same way as M4: unverified, forbidden and missing all return one `resource_not_accessible` code and message. |

---

## 16. Concurrency, idempotency, rate limiting

Unchanged: the same row lock, the same idempotent re-submission, the same
`integrations` throttle scope covering both providers. No caching.

---

## 17. Test strategy

`apps/api/tests/test_search_console_resources.py`, mirroring
`test_ga4_resources.py` so the two read the same way. All HTTP stubbed with
`responses`; no test reaches Google.

**Discovery** — a normal list; `siteUnverifiedUser` entries excluded; entries
without `siteUrl` skipped; an empty `siteEntry` is an empty list, not an error;
`truncated` is always false; **exactly one** outbound call (asserting the
absence of a paging loop).

**Encoding** — for `https://example.com/`, the request URL contains
`%3A%2F%2F` and has no bare `/` after `/sites/`; for `sc-domain:example.com`,
the `:` is encoded; both round-trip to the right stored value. This is the test
that catches a `requests` re-quoting change.

**The permission rule (§7.3)** — parametrized over every level: `siteOwner`,
`siteFullUser` and `siteRestrictedUser` each reach `connected`;
`siteUnverifiedUser`, a missing key, an empty string and an invented future
value (`siteSomethingNew`) each leave the connection untouched and return
`resource_not_accessible` — the allowlist, asserted as an allowlist. Unverified
rejection is byte-identical in code and message to the 403 and 404 responses.

**Identifier validation** — `javascript:`, a bare hostname, a scheme-less
value, `sc-domain:` with a path, traversal segments, a 300-character URL: all
400 with `len(responses.calls) == 0`.

**Parity with GA4** — the same table of behaviours asserted for this provider:
a label in the body has no effect; 403 and 404 are indistinguishable; selection
from `pending_authorization` is 409; a different id while connected is 409 with
no HTTP call; connected comes only from a verified response.

**Independence** — connecting Search Console leaves the GA4 connection's
status, resource and timestamps untouched, and vice versa.

**Regression on the shared refactor** — the whole existing GA4 suite must pass
unchanged apart from the two field renames. That suite is the safety net for
Option A, and if it needs edits beyond renames, the refactor changed behaviour
and is wrong.

**Frontend** — the picker renders flat with no legend when every
`group_label` is empty, grouped when they are not, with neither branch keyed to
a provider; the empty state names the provider it was given; the same dialog
drives both providers in the card tests. Plus the neutrality assertion: the
component source contains none of "Analytics", "GA4", "account", "ga4" or
"sc-domain" (§12.1).

**Leak containment (§4.5)** — a test asserts that `properties/`,
`accountSummaries`, `analyticsadmin`, `sc-domain`, `webmasters` and `siteEntry`
appear in no file under `integrations/` except `google/ga4.py` and
`google/search_console.py`. A provider concept reaching shared code becomes a
failing test rather than something review has to catch.

**Mutation checks before hand-off** — drop the `siteUnverifiedUser` check in
verification; change `quote(..., safe="")` to the default; return
`truncated=True`. Each must turn the suite red.

---

## 18. Files likely to change

Delivered as **two commits**: commit 1 is the §4 refactor with no Search
Console code, and the GA4 suite must be green at that commit before commit 2
adds the provider (§4.3).

New: `integrations/resources.py`, `integrations/google/search_console.py`,
`apps/api/tests/test_search_console_resources.py`,
`apps/api/tests/test_provider_boundary.py` (the §4.5 containment assertions).

Modified: `integrations/google/ga4.py` (return type), `integrations/providers/`
(all three files), `integrations/resource_service.py` (dispatch),
`integrations/serializers.py` (two renames), `apps/api/tests/test_ga4_resources.py`
(renames only), `apps/web/lib/api/types.ts`,
`apps/web/components/integrations/resource-picker-dialog.tsx`,
`apps/web/components/integrations/integration-card.tsx` (one prop), the
frontend tests, `docs/V1_BUILD_PLAN.md`.

Not touched: `integrations/models.py`, any migration, `integrations/google/credentials.py`,
`integrations/google/errors.py`, `audit/`, `projects/`, `accounts/`,
`workspaces/`, `apps/web/lib/integrations/status.ts`, all deployment files.

---

## 19. Migration

**None.** Same reasoning as M4, and now with a milestone of evidence behind it:
`external_resource_id` is `CharField(255)`, which is why §7 caps the accepted
identifier at 255 rather than letting the database decide.

## 20. Dependencies

**None.** `requests` is already direct, `responses` already stubs it, and no
frontend package is added. The locks should come back byte-identical; if they
do not, something unintended was added and it stops for review.

---

## 21. Acceptance criteria

1. A Search Console connection in `awaiting_resource_selection` lists the
   account's verified sites, and only those.
2. Selecting one yields `connected`, with the identifier and both health
   timestamps written by that same verification call, and one audit row.
3. A site the account is not verified for cannot be selected, even by posting
   its identifier directly and even though Google answers 200.
4. Both `https://…/` and `sc-domain:…` forms round-trip: listed, selected,
   verified, stored, and displayed unchanged.
5. A label submitted in the request body has no effect on anything stored.
6. GA4 and Search Console connect independently; neither touches the other.
7. The picker component serves both providers with no per-provider branch
   beyond the props described in §12.
8. The entire M4 test suite passes with **only** symbol renames, no changed
   expectation values (§4.3), and it is green at the refactor commit before any
   Search Console code exists.
9. `siteRestrictedUser` connects; `siteUnverifiedUser`, a missing level and an
   unrecognized level all refuse, indistinguishably from 403 and 404.
10. The picker component contains no provider name, provider key or Analytics
   terminology, and branches on data rather than on provider.
11. No endpoint, method, status code or error code changed from M4; the only
   response change is the two documented field renames.
12. No token, refresh token or Google error string in any response, log line
    or audit row; no credential-handling code changed at all.
13. `pytest`, `vitest`, `tsc`, `eslint`, `next build` green; `makemigrations
    --check` clean; both lock files byte-identical.

---

## 22. Rollback and hand-off

**Rollback:** additive apart from the §4 refactor and behind no schema change,
so `git revert` of the merge plus a redeploy is the whole procedure. Rows that
reached `connected` keep their identifier; reverted code stops offering the
Search Console picker and GA4 returns to its M4 behaviour, because the refactor
changes structure and not semantics — which is what §17's parity requirement
exists to prove before merge.

**Hand-off to Milestone 6:** M5 leaves both providers connectable and neither
disconnectable. M6 owns reconnect, disconnect with revocation and credential
deletion, changing an existing selection, the on-demand health check and **Test
connection** — all of which now have two providers to work across, and all of
which go through the `ResourceCatalog` this milestone extracts. If M6 needs a
fourth method on it, that is a fair extension: it will have two implementations
to satisfy, which is the standard this milestone was held to.

---

## 23. Staging acceptance checklist

Written before implementation so the bar is fixed in advance, and shaped like
the M4 checklist that worked. Run on `staging.arkav.lol` after deploying the
merged branch; the Search Console connection is already sitting in
`awaiting_resource_selection` from M3.

### 23.1 Preconditions

- The staging clone is on merged `main`, both containers healthy.
- **No `.env` change** — M5 introduces no setting.
- The Google account has at least one verified Search Console property, and
  ideally an unverified one, so the exclusion can be observed rather than
  assumed.

### 23.2 Regression: M4 must be untouched

Run **first**. The refactor is the risk this milestone carries.

1. The GA4 card still reads **Connected** with `poolino`.
2. Its stored row is unchanged: `external_resource_id = properties/549483499`,
   label `poolino`, metadata exactly
   `{"account": "accounts/404306605", "property_type": "PROPERTY_TYPE_ORDINARY"}`,
   both timestamps as they were, `connected_by_id` unchanged.
3. No new audit row was written by the deployment.

### 23.3 Search Console discovery

4. The Search Console card reads **Select a property** with a working
   **Choose property** button.
5. The dialog lists the account's verified sites, **flat, with no group
   heading** — the neutral-grouping rule, visible.
6. Each entry shows "Domain property" or "URL-prefix property" correctly for
   its identifier form.
7. Any site the account is **not** verified for does **not** appear.
8. The empty state, if it appears, names Search Console and not Analytics.

### 23.4 Selection and verification

9. Selecting a site moves the card to **Connected** with the site identifier
   and a fresh "Last successful access".
10. If both identifier forms are available, verify one of each; `https://…/`
    round-trips with its scheme and trailing slash intact.
11. The API container log shows the outbound path percent-encoded — a
    `%3A%2F%2F` and no bare slash after `/sites/`.

### 23.5 The negative case — the security check

12. With the picker closed, post an identifier for a site the account is **not**
    verified for, directly:
    ```bash
    curl -sS -X POST https://staging.arkav.lol/api/projects/<id>/integrations/search_console/resource \
      -H 'Content-Type: application/json' -H "X-CSRFToken: $CSRF" -b cookies.txt \
      -d '{"resource_id":"sc-domain:some-site-you-are-not-verified-for.example"}'
    ```
    Expect **400 `resource_not_accessible`**, and the connection unchanged.
13. Post a malformed identifier (`javascript:alert(1)`, `sc-domain:../../x`) —
    expect 400 `invalid_resource_id` with no outbound call in the log.

### 23.6 Database, encryption, audit, logs

14. Connection row: `status = connected`, identifier and label identical to
    each other and to what Google returned, metadata exactly
    `{"permission_level": "siteOwner"|"siteFullUser"|"siteRestrictedUser"}` —
    one key, no timestamp, no raw response.
15. `connected_by_id` unchanged by the selection.
16. Credentials still Fernet-encrypted for **both** connections; no plaintext
    `ya29.` or `1//` anywhere in the database.
17. Exactly **one** new `integration.resource_selected` row, `provider =
    search_console`, metadata keys exactly the allowlisted five.
18. Log leak check over the session returns **0** for `ya29.`, `1//`,
    `client_secret`, `"access_token"`, `"refresh_token"`.
19. The two connections are independent: GA4's row is byte-identical to
    step 2 after all of the above.

The SQL and log commands are the M4 checklist's, unchanged apart from expecting
two connected rows instead of one.

---

## 24. Implementation invariants

The things that must still be true when M5 is done. Stated before any code is
written, so the refactor is measured against a fixed bar rather than against
whatever it turns out to do.

### 24.1 Existing GA4 connections

| Invariant | How it is guaranteed |
|---|---|
| A connected GA4 integration **stays connected** | No code path writes `status` outside `_persist_selection` (verified selection) and `mark_reauth_required` (rejected refresh). The refactor moves call sites; it adds no write. |
| Existing rows are **untouched** | M5 executes no data-access code at deploy time. There is no backfill, no management command, no migration, no `save()` outside a user-initiated selection. A connected row is read, never written, until its owner selects again. |
| **No migration** | No model field is added, removed, renamed or altered. `makemigrations --check` must report "No changes detected" — an acceptance criterion (§21) and a staging check (§23). |
| **No data rewrite** | `external_resource_id`, `external_resource_label` and `external_resource_meta` keep their stored values byte for byte. The `RemoteResource` rename changes the *Python attribute* a value passes through, never the *database key*: GA4's metadata stays `{"account": …, "property_type": …}` because `ga4.py` still builds that dict (§4.4, §4.5). |
| Verified on staging | §23.2 checks the live `poolino` row against its recorded values **before** any Search Console step. |

The distinction that makes this safe: the refactor renames things in the shared
layer. It renames nothing that has ever been written to the database.

### 24.2 API response compatibility

**Confirmed: the discovery field renames affect exactly one consumer, and it is
in this repository.**

- `grep` over the monorepo finds `account_label` and `property_type` in three
  files only: `apps/api/integrations/serializers.py` (producer),
  `apps/web/lib/api/types.ts` (the hand-maintained mirror) and
  `resource-picker-dialog.tsx` (the sole reader). All three change in the same
  commit.
- **No external client depends on the old fields.** The endpoint requires an
  authenticated same-origin session, has existed only since M4 (merged today),
  is deployed only to staging, is not versioned or published, has no API
  documentation, no API keys, no third-party integration, and no consumer
  outside `apps/web`. The scope of the claim is exactly that: no client exists
  outside this repository that *could* depend on them.
- **Nothing else in the response changes** — not the route, method, request
  body, status codes, error codes, `IntegrationEntry` shape, or the selection
  endpoint's contract. `GET /integrations` is untouched.

### 24.3 Rollback: can commit 2 be reverted alone?

**Yes for the backend, with one frontend caveat that is a pre-existing defect.**

Reverting **commit 2** (the Search Console provider) leaves commits 1, 3 and 4
in place. GA4 keeps working, because commit 1 is behaviour-preserving by
construction and was proven green on the GA4 suite before commit 2 existed
(§4.3). `google_search_console.PROVIDER` returns to `resources=None`, and both
endpoints answer 404 for it — exactly M4's behaviour.

**The caveat, and it is a defect that already exists on `main` today:** the
Integrations card renders the property picker from **status alone**
(`resourceAction === "select"`), with no check that the provider actually
supports resource selection. So a Search Console connection sitting in
`awaiting_resource_selection` — which is exactly where staging's has been since
M3 — already shows a **Choose property** button whose dialog fails with a 404.
M5 makes the symptom disappear by giving the provider a catalog, but it does
not fix the cause, and a revert of commit 2 would bring the broken button back.

The minimal fix is a `supports_resource_selection` boolean on the integration
entry, derived from `provider.resources is not None`, with the card gating on
`resourceAction === "select" && entry.supports_resource_selection`. That is
roughly ten lines across the serializer, the service, the type mirror and the
card. **It is not in the approved M5 scope**, so it is not being implemented
unasked — it is recorded here, and raised for a decision. Until it is fixed,
"revert commit 2 alone" means "revert commit 2 and accept a broken button on an
unconfigured provider", which is a real but cosmetic regression to a state that
already shipped.

Reverting **the whole branch** is unconditionally safe and is the recommended
path if anything larger goes wrong: no schema change, no data rewrite, and GA4
returns to its M4 behaviour exactly.

### 24.4 Commit sequence

Four commits, in this order, each independently green:

| # | Contents | Gate before moving on |
|---|---|---|
| 1 | `resources.py`, `ResourceCatalog` extraction, `ga4.py` returns `RemoteResource`, `resource_service.py` dispatches through the provider, serializer renames. **No Search Console code whatsoever.** | The full M4 GA4 suite passes with symbol renames only and no changed expectation values (§4.3). This is the moment the refactor is judged. |
| 2 | `google/search_console.py`, the provider catalog wiring, `test_search_console_resources.py`, `test_provider_boundary.py`. Backend only. | New provider tests and the whole backend suite green. |
| 3 | The neutral picker: `providerName` prop, conditional grouping, `types.ts` renames, card wiring, frontend tests. | `vitest`, `tsc`, `eslint`, `next build` green. |
| 4 | `docs/V1_BUILD_PLAN.md` tick, design-document reconciliation, any test cleanup. | Everything green; no production code in this commit. |

Splitting this way is not bookkeeping: it is what makes §24.3 possible. A
single squashed commit would make "revert the provider but keep the refactor"
impossible, and would remove the checkpoint at which the refactor is provably
behaviour-preserving.

