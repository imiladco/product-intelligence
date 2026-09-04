"""The provider-neutral resource contract.

An integration points at exactly one external resource — a GA4 property, a
Search Console site — and the work of finding, verifying and storing one is the
same shape whichever provider it is. This module is that shape, and nothing
more.

Deliberately small. The protocol has three methods because ``resource_service``
has three call sites, each of which exists in two implementations. A fourth
method arrives when a fourth call site does, not in anticipation of one. There
is no plugin loader, no registry decorator, no capability flags, and no dynamic
import: a provider supports resource selection when it supplies a catalog, and
does not when it supplies ``None``.

Nothing here knows what a GA4 account is or what a Search Console permission
level means. Provider vocabulary stays inside the provider's own module; what
crosses this boundary is an id, a label, two optional display strings, and an
opaque metadata mapping this layer never reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class RemoteResource:
    """One selectable external resource, in provider-neutral terms.

    ``id`` and ``label`` are required and always come from the provider — the
    id is what gets stored and sent back to the provider verbatim, and the
    label is what the provider calls it. Neither is ever taken from a client
    request.

    The rest are optional because not every provider has them:

    * ``resource_type`` — a display string the UI shows beside the label. The
      shared layer knows a resource may have a type; only the provider module
      knows what its types are called.
    * ``group_label`` — how the picker groups the list, or ``""`` when the
      provider has no grouping. An empty value everywhere means a flat list.
    * ``metadata`` — small, non-sensitive, provider-issued facts worth storing
      on the connection. Opaque here: this layer copies it and never reads a
      key. It must never carry credential material, and never a timestamp the
      health fields already record.
    """

    id: str
    label: str
    resource_type: str = ""
    group_label: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceListing:
    """What a provider can offer, and whether that is all of it.

    ``truncated`` is true only when a provider stopped early rather than
    reaching the end of its own pagination. A provider whose API returns
    everything in one call reports false, always.
    """

    resources: tuple[RemoteResource, ...]
    truncated: bool = False


@runtime_checkable
class ResourceCatalog(Protocol):
    """What a provider must do to support resource selection.

    Implementations are stateless and touch no database: they take an access
    token, speak to the provider, and return the types above or raise one of
    the errors in ``google/errors.py``. Keeping them free of persistence is
    what lets them be tested with nothing but a stubbed HTTP layer.
    """

    def normalize_resource_id(self, resource_id: str) -> str:
        """The canonical form of a client-supplied identifier.

        Raises ``InvalidResourceId`` when it is not well formed for this
        provider. This runs before any outbound call, so a malformed value
        never reaches a URL.

        Both current providers return the input unchanged, but this is the one
        place a provider could trim or case-fold, and having the call site
        means no caller ever needs to know whether one does.
        """
        ...

    def list_resources(self, access_token: str) -> ResourceListing:
        """Everything this credential can see. A convenience, not an authority."""
        ...

    def verify_resource(self, access_token: str, resource_id: str) -> RemoteResource:
        """Prove this credential can actually use this exact resource.

        The authority: a connection becomes ``connected`` on the strength of
        this call and nothing else. Raises ``ResourceNotAccessible`` when the
        provider says no — including a provider that answers 200 while
        withholding permission.
        """
        ...
