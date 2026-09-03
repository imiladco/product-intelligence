"""Discovering and selecting the external resource a connection points at.

Sits between the views and the Google boundary: views do tenancy and HTTP,
``google/ga4.py`` does HTTP to Google, and the decisions in between live here.

The rule that matters most is in ``select_resource``. A client submits an
identifier and nothing else; the label, the account and the property type are
read from Google's own verification response. No request body can make a
connection ``connected``, and none can influence what is stored under it.

Only GA4 has resource selection today. That is expressed as one explicit check
rather than a provider protocol: with a single implementation, an abstraction
would be a guess about the second one. Milestone 5 adds Search Console — whose
identifiers are URLs needing path encoding — and decides the shape then, from
two real cases.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_event

from .google import ga4
from .google.credentials import access_token_for, mark_reauth_required
from .google.errors import (
    CredentialMissing,
    CredentialRefreshFailed,
    InvalidResourceId,
    ResourceChangeNotSupported,
    ResourceSelectionUnsupported,
)
from .models import IntegrationConnection
from .providers import ProviderKey
from .status import ConnectionStatus

#: Providers whose resources this milestone can list and verify.
SUPPORTED_RESOURCE_PROVIDERS = frozenset({ProviderKey.GA4})

#: The states in which a connection holds a credential worth using. Anything
#: else has nothing to reach Google with, and says so rather than trying.
USABLE_STATUSES = frozenset(
    {ConnectionStatus.AWAITING_RESOURCE_SELECTION, ConnectionStatus.CONNECTED}
)


@dataclass(frozen=True)
class DiscoveredResources:
    resources: tuple[ga4.Ga4Property, ...]
    truncated: bool


def _require_supported(provider_key: str) -> None:
    if provider_key not in SUPPORTED_RESOURCE_PROVIDERS:
        raise ResourceSelectionUnsupported


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


def discover_resources(*, project, provider_key: str) -> DiscoveredResources:
    """List the GA4 properties the connection's Google account can read."""
    _require_supported(provider_key)
    connection = _usable_connection(project, provider_key)

    access_token = access_token_for(connection)
    try:
        page = ga4.list_properties(access_token)
    except CredentialRefreshFailed:
        mark_reauth_required(connection)
        raise
    return DiscoveredResources(resources=page.properties, truncated=page.truncated)


def select_resource(
    *, user, project, provider_key: str, resource_id: str
) -> IntegrationConnection:
    """Verify a property against Google and, only then, store it.

    The verifying call is also the connection's first health check: the same
    200 that proves the property is readable is what stamps the health
    timestamps, so there is no second code path that could disagree with it.
    """
    _require_supported(provider_key)

    if not ga4.is_valid_property_id(resource_id):
        # Before anything else: a malformed identifier never becomes an
        # outbound request, and never reaches a URL.
        raise InvalidResourceId

    connection = _usable_connection(project, provider_key)

    # Changing an existing selection is a later milestone. Re-submitting the
    # same one is not a change, so a retried or double-submitted request stays
    # harmless instead of becoming an error the user has to interpret.
    if (
        connection.status == ConnectionStatus.CONNECTED
        and connection.external_resource_id != resource_id
    ):
        raise ResourceChangeNotSupported

    access_token = access_token_for(connection)
    try:
        selected = ga4.get_property(access_token, resource_id)
    except CredentialRefreshFailed:
        # Google rejected the token we just refreshed: the grant is gone.
        mark_reauth_required(connection)
        raise

    return _persist_selection(connection=connection, selected=selected, user=user)


def _persist_selection(*, connection, selected, user) -> IntegrationConnection:
    """Write the verified selection, or nothing at all.

    Everything lands in one save inside one transaction, so a connection is
    never left holding a property id without the label and status that go with
    it.
    """
    with transaction.atomic():
        locked = IntegrationConnection.objects.select_for_update().get(pk=connection.pk)
        previous_status = locked.status

        # Re-checked under the lock: two concurrent selections must not slip a
        # change past the guard above by racing it.
        if (
            previous_status == ConnectionStatus.CONNECTED
            and locked.external_resource_id
            and locked.external_resource_id != selected.id
        ):
            raise ResourceChangeNotSupported

        now = timezone.now()
        locked.external_resource_id = selected.id
        locked.external_resource_label = selected.label
        locked.external_resource_meta = selected.as_metadata()
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
