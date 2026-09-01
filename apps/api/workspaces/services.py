"""Workspace lifecycle helpers.

V1 has no invitation flow: a user's first workspace is created for them at
signup and further members are added through Django admin.
"""

from __future__ import annotations

from django.db import transaction
from django.utils.text import slugify

from .models import Membership, Workspace

MAX_SLUG_ATTEMPTS = 50


def _unique_slug(name: str) -> str:
    base = slugify(name)[:100] or "workspace"
    candidate = base
    for suffix in range(1, MAX_SLUG_ATTEMPTS):
        if not Workspace.objects.filter(slug=candidate).exists():
            return candidate
        candidate = f"{base}-{suffix}"
    # Deterministic attempts exhausted (heavily contended name); fall back to a
    # random suffix rather than looping forever.
    import secrets

    return f"{base}-{secrets.token_hex(4)}"


def default_workspace_name(user) -> str:
    label = (user.name or "").strip() or user.email.split("@")[0]
    return f"{label}'s workspace"


@transaction.atomic
def create_workspace(*, name: str, owner) -> Workspace:
    """Create a workspace and make ``owner`` its owner in one transaction."""
    workspace = Workspace.objects.create(name=name.strip(), slug=_unique_slug(name))
    Membership.objects.create(
        workspace=workspace, user=owner, role=Membership.Role.OWNER
    )
    return workspace


def create_initial_workspace(user) -> Workspace:
    """The workspace every new user gets at signup."""
    return create_workspace(name=default_workspace_name(user), owner=user)
