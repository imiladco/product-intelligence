"""The one way to write an audit record.

Two integrity rules are enforced here rather than left to callers:

1. When a project is supplied, the workspace is *derived* from it. An audit row
   whose workspace does not own its project would make the log untrustworthy
   for exactly the tenant questions it exists to answer.
2. Metadata is filtered against an allowlist. This table is long-lived, visible
   in Django admin, and included in database backups, so a caller must not be
   able to drop a token or an authorization code into it by accident.
"""

from __future__ import annotations

from typing import Any

from .models import AuditEvent

#: The only keys an audit event may carry. Everything else is dropped.
#: Deliberately excludes anything that could hold credential material.
ALLOWED_METADATA_KEYS = frozenset(
    {
        "provider",
        "status",
        "previous_status",
        "reason",
        "error_code",
        "resource_id",
        "resource_label",
    }
)


class AuditIntegrityError(ValueError):
    """The requested audit row would not be internally consistent."""


def filter_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only allowlisted keys, and only scalar values."""
    if not metadata:
        return {}
    return {
        key: value
        for key, value in metadata.items()
        if key in ALLOWED_METADATA_KEYS and isinstance(value, (str, int, bool, type(None)))
    }


def record_event(
    *,
    action: str,
    workspace=None,
    actor=None,
    project=None,
    provider: str = "",
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Record that something security-relevant happened.

    Supply ``project`` and the workspace is taken from it. Supplying both is
    allowed but they must agree; a mismatch raises rather than writing a row
    that misattributes an action to the wrong tenant.
    """
    if project is not None:
        if workspace is not None and workspace.pk != project.workspace_id:
            raise AuditIntegrityError(
                "workspace does not own project; refusing to write an audit event "
                "that would attribute this action to the wrong tenant."
            )
        workspace = project.workspace
    if workspace is None:
        raise AuditIntegrityError("An audit event needs a workspace or a project.")

    return AuditEvent.objects.create(
        workspace=workspace,
        project=project,
        actor=actor,
        action=action,
        provider=provider,
        metadata=filter_metadata(metadata),
    )
