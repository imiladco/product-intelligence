from __future__ import annotations

from django.conf import settings
from django.db import models


class Workspace(models.Model):
    """The tenant boundary. Everything a user can reach hangs off a Workspace."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.name

    def has_member(self, user) -> bool:
        return self.memberships.filter(user=user).exists()


class Membership(models.Model):
    """Links a user to a workspace. This is the only route to tenant data."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MEMBER = "member", "Member"

    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="memberships"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"], name="unique_membership_per_workspace"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.workspace} ({self.role})"

    @property
    def is_owner(self) -> bool:
        return self.role == self.Role.OWNER
