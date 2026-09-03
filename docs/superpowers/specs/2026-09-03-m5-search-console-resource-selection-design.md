# Milestone 5 — Search Console site discovery, selection, verification

Design draft. 2026-09-03. Not implemented; not approved.

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
per-provider settings, or any capability flag beyond `resources is None`. The
protocol has three methods because three call sites exist.

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

## 7. Selection, encoding, and the unverified rule

### Identifier validation, before any outbound call

Two documented forms, and nothing else:

```
^sc-domain:[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$
^https?://[A-Za-z0-9.\-:\[\]]+/[^\s?#]*$
```

Plus a hard length cap of 255, matching the column that stores it. Anything
else is `invalid_resource_id` with no request made — the M4 rule, unchanged.

### Path encoding

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

### The unverified rule — the security point of this milestone

`sites.get` returns **200** for a site the account is merely aware of but not
verified for, with `permissionLevel: "siteUnverifiedUser"`. A 200 is therefore
**not** sufficient evidence here, which is a genuine difference from GA4 where
any 200 means readable.

So verification checks the body:

```python
if payload.get("permissionLevel") in ("", None, "siteUnverifiedUser"):
    raise ResourceNotAccessible
```

Missing is treated as unverified: absence of the signal is not permission.
Without this, a client could post any site URL — including one it merely
guessed — and reach `connected` on a property it cannot read a single row from.
Filtering the *list* is not enough, because M4 established that the list is a
convenience and the verification call is the authority; the authority has to
carry the check.

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

The same two endpoints, now answering for both providers. The only change is
vocabulary in the discovery payload:

```json
{
  "resources": [
    { "id": "sc-domain:example.com", "label": "sc-domain:example.com",
      "group_label": "", "resource_type": "Domain property" }
  ],
  "truncated": false
}
```

`account_label` → `group_label` and `property_type` → `resource_type`: one
rename, done once, rather than a payload whose field names only make sense for
one of the two providers using it. This is a pre-release API with a
hand-maintained TypeScript mirror and no external consumers, so the rename
costs one edit on each side.

`POST …/resource` is unchanged: `{"resource_id": …}`, nothing else readable.

---

## 12. Frontend

The build plan's test is that the picker is reused unchanged. It is **almost**
true, and the parts that are not are exactly where GA4 vocabulary leaked in.

- `resource-picker-dialog.tsx` takes a `providerName` prop (the entry's
  `display_name`, which the card already has). The empty state becomes "No
  properties are available to this Google account in {providerName}." Both
  Google products call these "properties", so every other string stands.
- Grouping becomes conditional: when every `group_label` is empty — always, for
  Search Console — the list renders flat, with no legend. The current code
  would render a spurious "Other" heading.
- `resource_type` is rendered beside the label where present, so a user sees
  "Domain property" / "URL-prefix property" and can tell two similar entries
  apart.
- `status.ts` needs **no change**: "Select a property" is already accurate for
  both, and `resourceAction` is provider-independent.
- `DiscoveredResource` in `types.ts` follows the two renames.

No new component, no new shadcn primitive, no second dialog.

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

## 14. Audit

Unchanged. One `INTEGRATION_RESOURCE_SELECTED` per successful selection,
carrying `{provider, resource_id, resource_label, status, previous_status}` —
`provider` is what distinguishes the two. No new action, no allowlist change.

One thing to watch: `resource_id` and `resource_label` are now a **URL**. They
are already allowlisted and already non-sensitive — a site the user
administers, not credential material — so this is a note, not a change.

---

## 15. Tenancy and security

Every M4 guarantee carries over unchanged: the project is resolved through
`get_project_for_user` first, the connection is looked up by
`(project, provider)` from the resolved project so no id is ever accepted from
a client, tokens live in local variables, `external_resource_meta` stays
unserialized, CSRF applies to the POST.

Two additions specific to this provider:

- **Percent-encoding is a security control, not just correctness.** The
  identifier is attacker-influenced text going into a URL path. It is validated
  against the two documented forms first and encoded with `safe=""` second, so
  neither a traversal segment nor an injected query string can change which
  resource is addressed.
- **`siteUnverifiedUser` is an authorization decision** and is enforced on the
  verification response, not merely by filtering the list (§7).

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

**The unverified rule** — `sites.get` returning 200 with
`permissionLevel: "siteUnverifiedUser"` leaves the connection untouched and
returns `resource_not_accessible`; a missing `permissionLevel` does the same;
`siteOwner`, `siteFullUser` and `siteRestrictedUser` all succeed.

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

**Frontend** — the picker renders flat with no legend when `group_label` is
empty; grouped when it is not; the empty state names the provider; the same
dialog drives both providers in the card tests.

**Mutation checks before hand-off** — drop the `siteUnverifiedUser` check in
verification; change `quote(..., safe="")` to the default; return
`truncated=True`. Each must turn the suite red.

---

## 18. Files likely to change

New: `integrations/resources.py`, `integrations/google/search_console.py`,
`apps/api/tests/test_search_console_resources.py`.

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
8. The entire M4 test suite passes with only the two field renames.
9. No token, refresh token or Google error string in any response, log line or
   audit row.
10. `pytest`, `vitest`, `tsc`, `eslint`, `next build` green; no new migration;
    locks unchanged.

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
