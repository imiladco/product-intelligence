"""The one way to write an audit record."""

from __future__ import annotations

from typing import Any

from .models import AuditEvent


def record_event(
    *,
    workspace,
    action: str,
    actor=None,
    project=None,
    provider: str = "",
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Record that something security-relevant happened.

    ``metadata`` must contain only small, non-sensitive context. Never pass a
    token, authorization code, OAuth state, or client secret.
    """
    return AuditEvent.objects.create(
        workspace=workspace,
        project=project,
        actor=actor,
        action=action,
        provider=provider,
        metadata=metadata or {},
    )
