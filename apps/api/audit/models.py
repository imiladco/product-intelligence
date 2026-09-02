from __future__ import annotations

from django.conf import settings
from django.db import models

from projects.models import Project
from workspaces.models import Workspace


class AuditEvent(models.Model):
    """A record that something security-relevant happened.

    The foundation exists now so that Milestone 3's connect, reconnect and
    disconnect actions have somewhere to write. Milestone 2 has no real
    integration action to record, so nothing writes to this table yet — events
    are not manufactured just to populate it.

    ``metadata`` is for small non-sensitive context (a provider key, a status
    transition). It must never hold tokens, authorization codes, OAuth state,
    or any other secret: this table is long-lived, widely readable in Django
    admin, and included in database backups. ``services.record_event`` enforces
    this with an allowlist rather than trusting callers.
    """

    class Action(models.TextChoices):
        INTEGRATION_AUTHORIZATION_STARTED = (
            "integration.authorization_started",
            "Integration authorization started",
        )
        INTEGRATION_AUTHORIZED = "integration.authorized", "Integration authorized"
        INTEGRATION_AUTHORIZATION_FAILED = (
            "integration.authorization_failed",
            "Integration authorization failed",
        )
        INTEGRATION_CONNECTED = "integration.connected", "Integration connected"
        INTEGRATION_RECONNECTED = "integration.reconnected", "Integration reconnected"
        INTEGRATION_DISCONNECTED = "integration.disconnected", "Integration disconnected"
        INTEGRATION_RESOURCE_SELECTED = (
            "integration.resource_selected",
            "Integration resource selected",
        )

    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="audit_events"
    )
    project = models.ForeignKey(
        Project,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=64, choices=Action.choices)
    provider = models.CharField(max_length=32, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["workspace", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.action} at {self.created_at:%Y-%m-%d %H:%M:%S}"
