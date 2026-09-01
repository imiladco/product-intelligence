from __future__ import annotations

from rest_framework.permissions import BasePermission


class IsWorkspaceOwner(BasePermission):
    """Object-level check for actions restricted to workspace owners.

    Not used by any V1 endpoint (owner and member have the same project rights
    in V1) but defined here so that the concept has one home when the first
    owner-only action appears.
    """

    message = "Only a workspace owner can perform this action."

    def has_object_permission(self, request, view, obj) -> bool:
        workspace = getattr(obj, "workspace", obj)
        return workspace.memberships.filter(
            user=request.user, role="owner"
        ).exists()
