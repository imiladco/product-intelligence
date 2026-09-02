"""Google Analytics 4 provider definition.

Milestone 2 is the catalog entry only. OAuth (Milestone 3) and property
discovery plus health checks (Milestone 4) are added here later.
"""

from __future__ import annotations

from .base import IntegrationProvider, ProviderKey

PROVIDER = IntegrationProvider(
    key=ProviderKey.GA4,
    display_name="Google Analytics 4",
    description=(
        "Connect a GA4 property to bring this project's traffic and behaviour "
        "data into the platform."
    ),
)
