"""The Google Search Console Sites API boundary.

Everything this project knows about Search Console's HTTP surface lives here.
Callers pass an access token and receive ``RemoteResource`` values; a response
dict, a status code, or a Google error string never crosses this module's edge.

Contract verified against current Google documentation on 2026-09-03:

* ``GET {base}/sites`` — no parameters and **no pagination**: the method
  documents no pageSize, pageToken or nextPageToken, so one call returns
  everything. Response is ``{"siteEntry": [...]}``.
* ``GET {base}/sites/{siteUrl}`` — the identifier is a path parameter and must
  be percent-encoded whole.
* A Sites resource has exactly two fields: ``siteUrl`` and ``permissionLevel``.
* ``webmasters.readonly`` is sufficient.

Two things make this provider different from GA4, and both matter:

1. The identifier is a URL, not a tidy path segment (see ``_site_path``).
2. A 200 is **not** proof of access. Search Console answers 200 for a site the
   account merely knows about, and says so in ``permissionLevel``. The
   permission check below is an authorization decision, not a formality.

This module performs no database work, so it can be tested with nothing but a
stubbed HTTP layer.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import requests
from django.conf import settings

from ..resources import RemoteResource, ResourceListing
from .errors import (
    CredentialRefreshFailed,
    GoogleApiError,
    InvalidResourceId,
    ResourceNotAccessible,
    ResourceUnavailable,
)

logger = logging.getLogger(__name__)

#: The two documented identifier forms, and nothing else.
#:
#: A Domain property: ``sc-domain:example.com``.
DOMAIN_PROPERTY_RE = re.compile(
    r"^sc-domain:[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$",
    re.IGNORECASE,
)
#: A URL-prefix property: a scheme, a host, and a path that starts at the root.
URL_PREFIX_PROPERTY_RE = re.compile(r"^https?://[A-Za-z0-9.\-:\[\]]+/[^\s?#]*$")

#: Matches the column that stores the identifier. A longer value could not be
#: persisted, so it is refused before it is fetched rather than after.
MAX_RESOURCE_ID_LENGTH = 255

#: The permission levels that mean "this credential may read this site".
#:
#: An allowlist, deliberately. A denylist naming only siteUnverifiedUser would
#: silently accept any level Google adds in future; this refuses what it does
#: not recognize, and adding a level is then a decision someone makes on
#: purpose, with a test.
#:
#: siteRestrictedUser is included: Google's permissions table grants the
#: Performance report to owners, full users and restricted users alike — the
#: restrictions are on editing, not on reading. Verified 2026-09-03 against
#: https://support.google.com/webmasters/answer/7687615
ACCEPTED_PERMISSION_LEVELS = frozenset(
    {"siteOwner", "siteFullUser", "siteRestrictedUser"}
)

DOMAIN_PROPERTY_PREFIX = "sc-domain:"
DOMAIN_PROPERTY_TYPE = "Domain property"
URL_PREFIX_PROPERTY_TYPE = "URL-prefix property"


def is_valid_site_url(value: str) -> bool:
    """True when this is one of the two documented identifier forms."""
    if not value or len(value) > MAX_RESOURCE_ID_LENGTH:
        return False
    return bool(DOMAIN_PROPERTY_RE.match(value) or URL_PREFIX_PROPERTY_RE.match(value))


def normalize_resource_id(resource_id: str) -> str:
    """The canonical form of a submitted site identifier.

    Search Console identifiers are already canonical — the value Google
    returned is the value it expects back, trailing slash and all — so this
    validates and returns the input unchanged.
    """
    if not is_valid_site_url(resource_id):
        raise InvalidResourceId
    return resource_id


def _resource_type(site_url: str) -> str:
    """Which kind of property this identifier names, for display only."""
    if site_url.startswith(DOMAIN_PROPERTY_PREFIX):
        return DOMAIN_PROPERTY_TYPE
    return URL_PREFIX_PROPERTY_TYPE


def _site(site_url: str, permission_level: str) -> RemoteResource:
    """A Search Console site as a provider-neutral resource.

    The label is the identifier itself: Search Console has no display name, and
    inventing a prettier one would show the user something other than what
    Google's own interface shows them.

    ``group_label`` is empty — sites have no parent to group by — which is what
    makes the picker render them as a flat list.
    """
    return RemoteResource(
        id=site_url,
        label=site_url,
        resource_type=_resource_type(site_url),
        group_label="",
        metadata={"permission_level": permission_level},
    )


def _url(path: str) -> str:
    return f"{settings.SEARCH_CONSOLE_BASE_URL.rstrip('/')}/{path}"


def _site_path(site_url: str) -> str:
    """The path for one site, percent-encoded into a single segment.

    ``safe=""`` is the entire point. The default would leave ``/`` and ``:``
    bare, so ``https://example.com/`` would address ``/sites/https://example.com/``
    — a different, and almost certainly nonexistent, resource. Encoding the
    whole identifier is also what keeps a crafted value from adding path
    segments of its own.
    """
    return f"sites/{quote(site_url, safe='')}"


def _get(url: str, *, access_token: str) -> dict:
    """One authenticated GET, with every failure mapped to our own error.

    Never logs the response body or a Google error message: they can echo the
    request, and the request carries a bearer token.
    """
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=settings.GOOGLE_API_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Search Console API unreachable (%s)", type(exc).__name__)
        raise ResourceUnavailable from exc

    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("Search Console API returned a non-JSON body")
            raise ResourceUnavailable from exc
        return payload if isinstance(payload, dict) else {}

    # Status is logged; the body never is.
    logger.info("Search Console API returned %s", response.status_code)

    if response.status_code == 401:
        raise CredentialRefreshFailed
    if response.status_code in (403, 404):
        # One outcome for both, as with GA4: distinguishing them would reveal
        # whether a site the caller cannot reach exists.
        raise ResourceNotAccessible
    if response.status_code == 429 or response.status_code >= 500:
        raise ResourceUnavailable
    raise GoogleApiError


def list_resources(access_token: str) -> ResourceListing:
    """Every verified Search Console site this token can use.

    One request: the API documents no pagination, and inventing a paging loop
    would be inventing a contract. ``truncated`` is therefore always false —
    there is nothing this can stop short of.

    Sites the account is not verified for are left out. That is not the
    security control (verification is, in ``verify_resource``); it is simply
    not offering a choice that would be refused.
    """
    payload = _get(_url("sites"), access_token=access_token)

    sites: list[RemoteResource] = []
    for entry in payload.get("siteEntry") or []:
        if not isinstance(entry, dict):
            continue
        site_url = entry.get("siteUrl") or ""
        if not is_valid_site_url(site_url):
            # One malformed entry must not cost the user the whole list.
            continue
        permission_level = entry.get("permissionLevel") or ""
        if permission_level not in ACCEPTED_PERMISSION_LEVELS:
            continue
        sites.append(_site(site_url, permission_level))

    # A stable order Google does not promise.
    sites.sort(key=lambda item: item.id)
    return ResourceListing(resources=tuple(sites), truncated=False)


def verify_resource(access_token: str, site_url: str) -> RemoteResource:
    """Read one site, proving this token may actually use it.

    Unlike GA4, a 200 is not sufficient. Search Console returns 200 for a site
    the account merely knows about, marking it ``siteUnverifiedUser`` — so the
    body decides, against an allowlist. Without this check, posting a guessed
    site identifier would reach ``connected`` on a property from which nothing
    can be read.

    Refusal is deliberately indistinguishable from 403 and 404: telling a
    caller "this exists but you are not verified for it" is the same existence
    oracle the rest of this code closes.
    """
    if not is_valid_site_url(site_url):
        # Defence in depth: callers validate too, but the module that owns the
        # format is the one that must never build a URL from a bad value.
        raise ResourceNotAccessible

    payload = _get(_url(_site_path(site_url)), access_token=access_token)

    permission_level = payload.get("permissionLevel") or ""
    if permission_level not in ACCEPTED_PERMISSION_LEVELS:
        logger.info(
            "Search Console site refused: permission level not accepted"
        )
        raise ResourceNotAccessible

    # Google's own value for the identifier wins, as with GA4, falling back to
    # the requested one when the response omits it.
    returned = payload.get("siteUrl") or ""
    resolved = returned if is_valid_site_url(returned) else site_url
    return _site(resolved, permission_level)


class _SearchConsoleCatalog:
    """The three operations, bound together as this provider's catalog."""

    normalize_resource_id = staticmethod(normalize_resource_id)
    list_resources = staticmethod(list_resources)
    verify_resource = staticmethod(verify_resource)


CATALOG = _SearchConsoleCatalog()
