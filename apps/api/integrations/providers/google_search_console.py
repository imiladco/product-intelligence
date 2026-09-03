"""Google Search Console provider definition.

Milestone 2 is the catalog entry only. OAuth (Milestone 3) and site discovery
plus health checks (Milestone 5) are added here later.
"""

from __future__ import annotations

from .base import IntegrationProvider, ProviderKey

PROVIDER = IntegrationProvider(
    key=ProviderKey.SEARCH_CONSOLE,
    display_name="Google Search Console",
    description=(
        "Connect a Search Console property to bring this project's search "
        "impressions, clicks and queries into the platform."
    ),
    # Read-only. Verified 2026-09-02 against
    # https://developers.google.com/webmaster-tools/v1/sites/list
    oauth_scopes=("https://www.googleapis.com/auth/webmasters.readonly",),
)
