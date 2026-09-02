from __future__ import annotations

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from common.fields import EncryptedTextField
from projects.models import Project

from .providers import ProviderKey
from .status import ConnectionStatus


class IntegrationConnection(models.Model):
    """A project's connection to one external provider.

    Holds connection *configuration and state* only. Credentials are a separate
    concern with a separate table, added in Milestone 3 — no token, secret, or
    authorization-code field belongs on this model, so that no serializer or
    admin page over it can leak one.

    Most fields below are filled by later milestones (OAuth in 3, resource
    selection and health in 4 and 5) and are blank until then.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="integrations"
    )
    provider = models.CharField(max_length=32, choices=ProviderKey.choices)
    status = models.CharField(
        max_length=32,
        choices=ConnectionStatus.choices,
        default=ConnectionStatus.PENDING_AUTHORIZATION,
    )

    # The selected GA4 property or Search Console site.
    external_resource_id = models.CharField(max_length=255, blank=True, default="")
    external_resource_label = models.CharField(max_length=255, blank=True, default="")
    external_resource_meta = models.JSONField(default=dict, blank=True)

    # Which Google account authorized the connection — shown in the UI and
    # needed for support. Not a credential.
    google_account_email = models.EmailField(blank=True, default="")
    granted_scopes = ArrayField(
        models.CharField(max_length=255), default=list, blank=True
    )

    last_health_check_at = models.DateTimeField(null=True, blank=True)
    # Never cleared by a failure: the UI shows "last worked at" beside an error.
    last_successful_check_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True, default="")
    last_error_message = models.TextField(blank=True, default="")

    connected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="connected_integrations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["provider", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "provider"], name="unique_integration_per_project"
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_provider_display()} for {self.project} ({self.status})"


class IntegrationCredential(models.Model):
    """OAuth credential material for one connection.

    A separate table from IntegrationConnection on purpose: the connection
    serializer physically cannot reach these fields, and any query that lists
    connections does not load them. Tokens are encrypted at rest.

    Deliberately absent: the Google client secret (environment configuration,
    the same for every row), the authorization code (single-use, discarded
    after exchange), the OAuth state (only its hash is stored, elsewhere), and
    the token URI (a constant).
    """

    connection = models.OneToOneField(
        IntegrationConnection, on_delete=models.CASCADE, related_name="credential"
    )
    access_token = EncryptedTextField(blank=True, default="")
    refresh_token = EncryptedTextField(blank=True, default="")
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:  # pragma: no cover - admin/debug convenience
        return f"Credential for {self.connection_id}"

    @property
    def has_refresh_token(self) -> bool:
        return bool(self.refresh_token)


class OAuthAuthorizationRequest(models.Model):
    """One in-flight authorization attempt.

    The OAuth ``state`` is never stored: only its SHA-256 hash is, so a
    database read cannot forge a callback. The row binds the callback to a
    single user, project and provider, and is single-use — the callback
    consumes it under a row lock so two concurrent callbacks cannot both
    succeed.
    """

    state_hash = models.CharField(max_length=64, unique=True, db_index=True)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="oauth_authorization_requests"
    )
    provider = models.CharField(max_length=32, choices=ProviderKey.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_authorization_requests",
    )
    # PKCE verifier. google-auth-oauthlib generates one by default for this
    # flow; it must survive the redirect to Google and back, and it is secret
    # until used, so it is encrypted like any other credential material.
    code_verifier = EncryptedTextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:  # pragma: no cover - admin/debug convenience
        return f"{self.provider} authorization for project {self.project_id}"

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def is_expired(self, now=None) -> bool:
        return (now or timezone.now()) >= self.expires_at
