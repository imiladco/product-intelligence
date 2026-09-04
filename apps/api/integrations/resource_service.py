"""Discovering and selecting the external resource a connection points at.

Sits between the views and the provider boundary: views do tenancy and HTTP,
a provider's catalog does HTTP to Google, and the decisions in between live
here.

The rule that matters most is in ``select_resource``. A client submits an
identifier and nothing else; the label, the account and the property type are
read from Google's own verification response. No request body can make a
connection ``connected``, and none can influence what is stored under it.

This module names no provider. It asks the catalog on the provider the URL
identified, and a provider without one has no resource selection — 404, rather
than a half-working feature. Milestone 4 dispatched with an explicit check
because there was one implementation and an abstraction would have been a guess
about the second; Milestone 5 has the second, so the guessing is over.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_event

from .concurrency import Fence, locked_existing_connection
from .google.credentials import access_token_for, mark_reauth_required
from .google.errors import (
    CredentialMissing,
    CredentialRefreshFailed,
    ResourceSelectionUnsupported,
)
from .models import IntegrationConnection
from .providers import get_provider
from .resources import ResourceCatalog, ResourceListing
from .status import ConnectionStatus

#: The states in which a connection holds a credential worth using. Anything
#: else has nothing to reach Google with, and says so rather than trying.
#:
#: ``ERROR`` is included: a connection whose property was deleted sits there
#: holding a perfectly good credential, and repointing it at another property
#: is precisely the repair (§6).
USABLE_STATUSES = frozenset(
    {
        ConnectionStatus.AWAITING_RESOURCE_SELECTION,
        ConnectionStatus.CONNECTED,
        ConnectionStatus.ERROR,
    }
)


def _catalog(provider_key: str) -> ResourceCatalog:
    """This provider's resource catalog, or the reason there is none."""
    provider = get_provider(provider_key)
    if provider is None or provider.resources is None:
        raise ResourceSelectionUnsupported
    return provider.resources


def _usable_connection(project, provider_key: str) -> IntegrationConnection:
    """The connection to work with, or the reason it cannot be used.

    Never creates a row: listing or selecting resources for an integration
    nobody has authorized is a conflict, not a reason to bring one into
    existence.
    """
    connection = IntegrationConnection.objects.filter(
        project=project, provider=provider_key
    ).first()
    if connection is None:
        raise CredentialMissing
    if connection.status == ConnectionStatus.REAUTH_REQUIRED:
        # A more useful answer than "not authorized": the user authorized once
        # and needs to do it again.
        raise CredentialRefreshFailed
    if connection.status not in USABLE_STATUSES:
        raise CredentialMissing
    return connection


def discover_resources(*, project, provider_key: str) -> ResourceListing:
    """List the resources the connection's Google account can reach."""
    catalog = _catalog(provider_key)
    connection = _usable_connection(project, provider_key)

    access_token = access_token_for(connection)
    try:
        return catalog.list_resources(access_token)
    except CredentialRefreshFailed:
        mark_reauth_required(connection)
        raise


def select_resource(
    *, user, project, provider_key: str, resource_id: str
) -> IntegrationConnection:
    """Verify a resource against the provider and, only then, store it.

    Selecting and *changing* a selection are one operation (§6). What makes
    changing safe is not a guard but the order: the identifier is normalized
    before any outbound call, the resource is verified against the provider
    before anything is written, the label and metadata come from that
    verification rather than the body, and the write happens once under the row
    lock. Nothing is written on any failure path, so a failed change leaves the
    previous selection exactly as it was.

    The verifying call is also the connection's first health check: the same
    success that proves the resource is usable is what stamps the health
    timestamps, so there is no second code path that could disagree with it.
    """
    catalog = _catalog(provider_key)

    # Before anything else: a malformed identifier never becomes an outbound
    # request, and never reaches a URL. normalize_resource_id raises.
    resource_id = catalog.normalize_resource_id(resource_id)

    connection = _usable_connection(project, provider_key)

    access_token = access_token_for(connection)

    # Captured after any refresh has committed and immediately before the
    # provider call, so the result can be checked against the state it was
    # actually computed from (§9.3).
    connection.refresh_from_db()
    fence = Fence.capture(connection)

    # Deliberately no except clause. A failed verification writes **nothing**,
    # including when Google answers 401 (§4.1, §6).
    #
    # Where the 401 came from is the whole distinction. Acquiring the token
    # above proves something about the stored grant, and ``access_token_for``
    # owns that verdict — a dead refresh token still moves the connection to
    # reauth_required, under its own fence. A 401 while verifying a *candidate*
    # resource proves only that this change attempt failed: the connection
    # still holds the credential it had a moment ago and the selection it had
    # before, and writing reauth_required over them would turn a rejected
    # change into a broken integration the user then has to repair.
    selected = catalog.verify_resource(access_token, resource_id)

    return _persist_selection(
        connection=connection, selected=selected, user=user, fence=fence
    )


def _persist_selection(*, connection, selected, user, fence) -> IntegrationConnection:
    """Write the verified selection, or nothing at all.

    Everything lands in one save inside one transaction, so a connection is
    never left holding a property id without the label and status that go with
    it.
    """
    with transaction.atomic():
        locked = locked_existing_connection(connection.project, connection.provider)

        if not fence.matches(locked):
            # The world moved while the provider was answering: this result
            # describes a state that no longer exists. Discard it entirely —
            # not even a timestamp, since a stale result has no claim on any
            # field — and report what the connection actually is now.
            return locked

        previous_status = locked.status

        now = timezone.now()
        locked.external_resource_id = selected.id
        locked.external_resource_label = selected.label
        locked.external_resource_meta = dict(selected.metadata)
        locked.status = ConnectionStatus.CONNECTED
        locked.last_health_check_at = now
        locked.last_successful_check_at = now
        locked.last_error_code = ""
        locked.last_error_message = ""
        # connected_by is deliberately not touched: it records who completed
        # the authorization, and selecting a property is not authorizing.
        locked.save(
            update_fields=[
                "external_resource_id",
                "external_resource_label",
                "external_resource_meta",
                "status",
                "last_health_check_at",
                "last_successful_check_at",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )

        # One event for one user action. The successful transition to connected
        # is what this event *is*, so it carries the resulting status rather
        # than being paired with a second row saying the same thing.
        record_event(
            action=AuditEvent.Action.INTEGRATION_RESOURCE_SELECTED,
            actor=user,
            project=locked.project,
            provider=locked.provider,
            metadata={
                "provider": locked.provider,
                "resource_id": locked.external_resource_id,
                "resource_label": locked.external_resource_label,
                "status": locked.status,
                "previous_status": previous_status,
            },
        )
    return locked
