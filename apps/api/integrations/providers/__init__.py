"""The provider catalog.

The backend is the source of truth for which integrations exist; the frontend
renders whatever this returns rather than keeping its own list.
"""

from __future__ import annotations

from .base import IntegrationProvider, ProviderKey
from .google_ga4 import PROVIDER as GA4_PROVIDER
from .google_search_console import PROVIDER as SEARCH_CONSOLE_PROVIDER

#: Ordered, so the Integrations page renders the same way every time.
CATALOG: tuple[IntegrationProvider, ...] = (GA4_PROVIDER, SEARCH_CONSOLE_PROVIDER)

_BY_KEY: dict[str, IntegrationProvider] = {provider.key: provider for provider in CATALOG}

if len(_BY_KEY) != len(CATALOG):  # pragma: no cover - guards a typo at import time
    raise RuntimeError("Duplicate provider key in the integration catalog.")


def get_provider(key: str) -> IntegrationProvider | None:
    return _BY_KEY.get(key)


def provider_keys() -> tuple[str, ...]:
    return tuple(_BY_KEY)


__all__ = [
    "CATALOG",
    "IntegrationProvider",
    "ProviderKey",
    "get_provider",
    "provider_keys",
]
