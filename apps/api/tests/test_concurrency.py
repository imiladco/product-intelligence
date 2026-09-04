"""Concurrency: the three fences, and every race the design names.

Three separate mechanisms are exercised here and never merged (design §9.6):

* ``lifecycle_generation`` — is this authorization still the current intent?
* ``Fence`` — is this provider result about the state it was computed from?
* ``RefreshFence`` — is this refresh result about the credential it came from?

Interleaving is produced by stubbing the *outbound call* to perform the
competing mutation before it returns: deterministic, no threads, no sleeps. The
one exception is the connection-creation race, which needs real concurrency and
uses threads under ``transaction=True``.
"""

from __future__ import annotations

import pytest

from integrations.models import IntegrationConnection, OAuthAuthorizationRequest
from integrations.providers import ProviderKey

pytestmark = pytest.mark.django_db


class TestGenerationFields:
    """The counter exists and starts where the migration says it does."""

    def test_new_connection_starts_at_generation_zero(self, make_user_with_workspace, make_project):
        _user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        connection = IntegrationConnection.objects.create(
            project=project, provider=ProviderKey.GA4
        )

        assert connection.lifecycle_generation == 0

    def test_new_authorization_request_defaults_to_generation_zero(
        self, make_user_with_workspace, make_project
    ):
        """Rows predating the migration default to 0 on both sides (§11.2)."""
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        request = OAuthAuthorizationRequest.objects.create(
            state_hash="a" * 64,
            project=project,
            provider=ProviderKey.GA4,
            user=user,
            expires_at="2030-01-01T00:00:00Z",
        )

        assert request.connection_generation == 0
