from __future__ import annotations

from django.conf import settings
from django.db import models

from workspaces.models import Workspace

from .normalization import normalize_website_url


class Project(models.Model):
    """A single digital product inside a workspace."""

    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="projects"
    )
    name = models.CharField(max_length=120)
    website_url = models.URLField(max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_projects",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"], name="unique_project_name_per_workspace"
            )
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.website_url:
            self.website_url = normalize_website_url(self.website_url)

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.strip()
        if self.website_url:
            self.website_url = normalize_website_url(self.website_url)
        return super().save(*args, **kwargs)
