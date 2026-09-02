"""Building the Integrations page payload."""

from __future__ import annotations

from dataclasses import dataclass

from .models import IntegrationConnection
from .providers import CATALOG, IntegrationProvider
from .status import NOT_CONNECTED


@dataclass(frozen=True)
class IntegrationEntry:
    """A catalog provider merged with this project's stored connection, if any."""

    provider: str
    display_name: str
    description: str
    status: str
    connection: IntegrationConnection | None


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
