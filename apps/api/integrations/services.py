"""Building the Integrations page payload."""

from __future__ import annotations

from dataclasses import dataclass

from .models import IntegrationConnection
from .providers import CATALOG, IntegrationProvider, get_provider
from .status import NOT_CONNECTED


@dataclass(frozen=True)
class IntegrationEntry:
    """A catalog provider merged with this project's stored connection, if any."""

    provider: str
    display_name: str
    description: str
    status: str
    connection: IntegrationConnection | None
    #: Whether this provider can list and verify resources at all.
    #:
    #: Connection status says where a connection is in its lifecycle; it says
    #: nothing about what the provider can do. The two are independent, and the
    #: UI needs both: a provider with no catalog has no resource selection no
    #: matter how healthy its connection is, and offering the action anyway
    #: produces a button that cannot work.
    supports_resource_selection: bool


def _entry(
    provider: IntegrationProvider, connection: IntegrationConnection | None
) -> IntegrationEntry:
    return IntegrationEntry(
        provider=provider.key,
        display_name=provider.display_name,
        description=provider.description,
        # A provider with no stored row is not connected. This value is
        # synthesized here and never written to the database.
        status=connection.status if connection is not None else NOT_CONNECTED,
        connection=connection,
        # Read from the catalog itself, so the frontend never keeps its own
        # idea of which providers support what.
        supports_resource_selection=provider.resources is not None,
    )


def integrations_for_project(project) -> list[IntegrationEntry]:
    """Every supported provider for a project, connected or not.

    Read-only: listing a project's integrations must never create rows, so a
    provider the user has not touched simply has no connection.
    """
    stored = {
        connection.provider: connection
        for connection in IntegrationConnection.objects.filter(project=project)
    }
    return [_entry(provider, stored.get(provider.key)) for provider in CATALOG]


def integration_entry_for_provider(project, provider_key: str) -> IntegrationEntry:
    """One provider's entry for a project, in the same shape as the list.

    Used after a change, so a client re-renders from the same payload it
    already knows how to read instead of a second, subtly different one.
    """
    provider = get_provider(provider_key)
    if provider is None:  # pragma: no cover - views validate the key first
        raise ValueError(f"Unknown provider: {provider_key}")
    connection = IntegrationConnection.objects.filter(
        project=project, provider=provider.key
    ).first()
    return _entry(provider, connection)
