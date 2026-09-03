"""The provider boundary.

A provider is a value object describing a supported integration, plus — since
Milestone 5 — an optional resource catalog. The catalog arrived only once two
real implementations existed to generalize from; before that it would have been
a guess about the second one.

No plugin loader, no entry points, no dynamic import.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import models

from ..resources import ResourceCatalog


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
    #: How this provider lists and verifies the resource a connection points
    #: at. None means it has no resource selection, and those endpoints answer
    #: 404 for it — the honest answer, rather than a half-working feature.
    resources: ResourceCatalog | None = None
