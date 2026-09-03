"""The provider boundary.

Deliberately the smallest thing that works today: a provider is a value object
describing a supported integration. There is no behaviour on it yet because
there is nothing real for it to do — OAuth arrives in Milestone 3 and resource
discovery in Milestones 4 and 5. Methods that only raise or return placeholders
would be worse than no methods, so the interface grows when the first real
implementation needs it.

No plugin loader, no entry points, no dynamic import.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import models


class ProviderKey(models.TextChoices):
    """The supported providers. Also the stored value on IntegrationConnection."""

    GA4 = "ga4", "Google Analytics 4"
    SEARCH_CONSOLE = "search_console", "Google Search Console"


@dataclass(frozen=True)
class IntegrationProvider:
    """Product metadata and the OAuth scopes for one supported integration."""

    key: str
    display_name: str
    description: str
    #: The minimum read-only scopes this provider needs. Each provider is
    #: authorized on its own, so connecting GA4 never asks for Search Console
    #: access and vice versa.
    oauth_scopes: tuple[str, ...]
