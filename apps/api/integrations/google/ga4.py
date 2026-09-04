"""The Google Analytics Admin API boundary.

Everything this project knows about GA4's HTTP surface lives here, and nothing
Google-shaped leaves it. Callers pass an access token and receive
``Ga4Property`` values; a response dict, a status code, or a Google error
string never crosses this module's edge.

Contract verified against current Google documentation on 2026-09-03:

* ``GET {base}/accountSummaries`` — ``pageSize`` (max 200), ``pageToken``;
  returns ``accountSummaries[]`` each with ``account``, ``displayName`` and
  ``propertySummaries[]`` (``property``, ``displayName``, ``propertyType``),
  plus ``nextPageToken`` until the last page.
* ``GET {base}/properties/{id}`` — the property resource, with ``name``,
  ``displayName``, ``propertyType`` and ``parent``.

Both need only the read-only ``analytics.readonly`` scope.

This module performs no database work at all, so it can be tested with nothing
but a stubbed HTTP layer.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

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

#: The canonical GA4 property identifier. Also the guard that keeps a caller's
#: string from becoming a path segment it should not be: no traversal, no
#: absolute URL, no query string can match.
PROPERTY_ID_RE = re.compile(r"^properties/[0-9]{1,32}$")

#: Google's documented maximum. Fewer round trips for the same result.
PAGE_SIZE = 200

#: Stop after this many pages. An account list large enough to exceed it is
#: pathological, and looping forever on one is worse than reporting a truncated
#: list the user can still choose from.
MAX_PAGES = 10


def is_valid_property_id(value: str) -> bool:
    """True when this is a well-formed GA4 property identifier."""
    return bool(PROPERTY_ID_RE.match(value or ""))


def normalize_resource_id(resource_id: str) -> str:
    """The canonical form of a submitted property identifier.

    GA4 identifiers are already canonical, so this validates and returns the
    input unchanged. It raises rather than returning a boolean because the
    caller has nothing useful to do with False except raise this.
    """
    if not is_valid_property_id(resource_id):
        raise InvalidResourceId
    return resource_id


def _property(
    *, property_id: str, label: str, account_id: str, account_label: str, property_type: str
) -> RemoteResource:
    """A GA4 property as a provider-neutral resource.

    The stored metadata is built here and only here: a stable account
    identifier and one display field, both read straight from Google. No
    timestamp — the health fields already record when this was verified, and
    two records of one fact can disagree. No raw response object.

    ``account`` and ``property_type`` are GA4's words, so they stay GA4's
    problem: they are *values* inside an opaque mapping and a neutral
    ``resource_type``, never names the shared layer has to know.
    """
    metadata = {"account": account_id}
    if property_type:
        metadata["property_type"] = property_type
    return RemoteResource(
        id=property_id,
        label=label,
        resource_type=property_type,
        group_label=account_label,
        metadata=metadata,
    )


def _url(path: str) -> str:
    return f"{settings.GA4_ADMIN_BASE_URL.rstrip('/')}/{path}"


def _get(url: str, *, access_token: str, params: dict | None = None) -> dict:
    """One authenticated GET, with every failure mapped to our own error.

    Never logs the response body or a Google error message: they can echo the
    request, and the request carries a bearer token.
    """
    try:
        response = requests.get(
            url,
            params=params or {},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=settings.GOOGLE_API_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("GA4 Admin API unreachable (%s)", type(exc).__name__)
        raise ResourceUnavailable from exc

    if response.status_code == 200:
        try:
            payload = response.json()
        except ValueError as exc:
            logger.warning("GA4 Admin API returned a non-JSON body")
            raise ResourceUnavailable from exc
        return payload if isinstance(payload, dict) else {}

    # Status is logged; the body never is.
    logger.info("GA4 Admin API returned %s", response.status_code)

    if response.status_code == 401:
        # The credential is no longer accepted. The caller marks the connection
        # for reauthorization; this module holds no connection to mark.
        raise CredentialRefreshFailed
    if response.status_code in (403, 404):
        # Forbidden and missing are one outcome on purpose: distinguishing them
        # would reveal whether a property the caller cannot reach exists.
        raise ResourceNotAccessible
    if response.status_code == 429 or response.status_code >= 500:
        raise ResourceUnavailable
    raise GoogleApiError


def _property_from_summary(summary: dict, *, account_id: str, account_label: str):
    """Build a resource from one propertySummaries[] entry, or None.

    A summary missing its identifier is skipped rather than raised on: one
    malformed entry must not cost the user the whole list.
    """
    property_id = summary.get("property") or ""
    if not is_valid_property_id(property_id):
        return None
    return _property(
        property_id=property_id,
        label=summary.get("displayName") or property_id,
        account_id=account_id,
        account_label=account_label,
        property_type=summary.get("propertyType") or "",
    )


def list_resources(access_token: str) -> ResourceListing:
    """Every GA4 property this token can see, across all pages of summaries."""
    properties: list[RemoteResource] = []
    page_token = ""
    truncated = False

    for _page in range(MAX_PAGES):
        params = {"pageSize": PAGE_SIZE}
        if page_token:
            params["pageToken"] = page_token
        payload = _get(_url("accountSummaries"), access_token=access_token, params=params)

        for account in payload.get("accountSummaries") or []:
            if not isinstance(account, dict):
                continue
            account_id = account.get("account") or ""
            account_label = account.get("displayName") or account_id
            for summary in account.get("propertySummaries") or []:
                if not isinstance(summary, dict):
                    continue
                found = _property_from_summary(
                    summary, account_id=account_id, account_label=account_label
                )
                if found is not None:
                    properties.append(found)

        page_token = payload.get("nextPageToken") or ""
        if not page_token:
            break
    else:
        # The loop ran out of pages with a token still outstanding.
        truncated = bool(page_token)

    # The same ordering as before the shared type existed: account, then
    # display name, then id.
    properties.sort(key=lambda item: (item.group_label, item.label, item.id))
    return ResourceListing(resources=tuple(properties), truncated=truncated)


def verify_resource(access_token: str, property_id: str) -> RemoteResource:
    """Read one property, proving this token can actually access it.

    The returned label comes from Google's response. Nothing a client submitted
    is echoed back into it.
    """
    if not is_valid_property_id(property_id):
        # Defence in depth: callers validate too, but the module that owns the
        # format is the one that must never build a URL from a bad value.
        raise ResourceNotAccessible

    payload = _get(_url(property_id), access_token=access_token)

    name = payload.get("name") or ""
    resolved_id = name if is_valid_property_id(name) else property_id
    return _property(
        property_id=resolved_id,
        label=payload.get("displayName") or resolved_id,
        account_id=payload.get("parent") or "",
        # properties.get does not carry the account's display name; the picker
        # is where grouping happens, and it has the summaries.
        account_label="",
        property_type=payload.get("propertyType") or "",
    )


class _Ga4Catalog:
    """The three operations, bound together as this provider's catalog."""

    normalize_resource_id = staticmethod(normalize_resource_id)
    list_resources = staticmethod(list_resources)
    verify_resource = staticmethod(verify_resource)


CATALOG = _Ga4Catalog()
