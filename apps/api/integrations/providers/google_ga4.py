"""Google Analytics 4 provider definition."""

from __future__ import annotations

from ..google import ga4
from .base import IntegrationProvider, ProviderKey

PROVIDER = IntegrationProvider(
    key=ProviderKey.GA4,
    display_name="Google Analytics 4",
    description=(
        "Connect a GA4 property to bring this project's traffic and behaviour "
        "data into the platform."
    ),
    # Read-only. Verified 2026-09-02 against
    # https://developers.google.com/identity/protocols/oauth2/scopes
    oauth_scopes=("https://www.googleapis.com/auth/analytics.readonly",),
    resources=ga4.CATALOG,
)
