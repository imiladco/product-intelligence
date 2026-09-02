"""Connection states.

Stored states live on ``IntegrationConnection.status``. ``NOT_CONNECTED`` is
deliberately *not* one of them: it means no row exists for that
(project, provider) pair. Listing the integrations of a project must never
create rows just because a provider appears in the catalog, so the API
synthesizes this value when merging the catalog with stored connections.
"""

from __future__ import annotations

from django.db import models


class ConnectionStatus(models.TextChoices):
    """States an IntegrationConnection row can actually hold."""

    PENDING_AUTHORIZATION = "pending_authorization", "Pending authorization"
    AWAITING_RESOURCE_SELECTION = "awaiting_resource_selection", "Awaiting resource selection"
    CONNECTED = "connected", "Connected"
    ERROR = "error", "Error"
    REAUTH_REQUIRED = "reauth_required", "Reauthorization required"
    DISCONNECTED = "disconnected", "Disconnected"


#: Synthesized by the API for a (project, provider) pair with no stored row.
NOT_CONNECTED = "not_connected"

#: Every value the API can report, stored or synthesized.
ALL_STATUSES: tuple[str, ...] = (NOT_CONNECTED, *ConnectionStatus.values)
