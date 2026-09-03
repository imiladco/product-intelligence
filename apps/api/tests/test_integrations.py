"""The integrations surface: catalog, catalog/connection merge, and isolation.

Milestone 2 has no OAuth and no Google calls. These tests exist to pin down the
domain and the read endpoint, including the invariant that listing a project's
integrations never writes to the database.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError
from django.utils import timezone

from integrations.models import IntegrationConnection
from integrations.providers import CATALOG, ProviderKey, get_provider, provider_keys
from integrations.serializers import IntegrationConnectionSerializer
from integrations.services import integrations_for_project
from integrations.status import ALL_STATUSES, NOT_CONNECTED, ConnectionStatus
from projects.models import Project
from tests.conftest import PASSWORD
from workspaces.services import create_initial_workspace

pytestmark = pytest.mark.django_db


def integrations_url(project_id) -> str:
    return f"/api/projects/{project_id}/integrations"


class TestProviderCatalog:
    def test_contains_exactly_ga4_and_search_console(self):
        assert [provider.key for provider in CATALOG] == ["ga4", "search_console"]

    def test_provider_keys_are_unique(self):
        keys = [provider.key for provider in CATALOG]
        assert len(keys) == len(set(keys))
        assert set(provider_keys()) == set(keys)

    def test_display_names(self):
        assert get_provider("ga4").display_name == "Google Analytics 4"
        assert get_provider("search_console").display_name == "Google Search Console"

    def test_every_provider_has_product_metadata(self):
        for provider in CATALOG:
            assert provider.display_name.strip()
            assert provider.description.strip()

    def test_unknown_key_returns_none(self):
        assert get_provider("google_ads") is None
        assert get_provider("") is None

    def test_catalog_keys_match_the_stored_provider_choices(self):
        """The model's choices and the catalog cannot drift apart."""
        assert set(provider_keys()) == set(ProviderKey.values)


class TestStatusModel:
    def test_not_connected_is_never_a_stored_status(self):
        assert NOT_CONNECTED not in ConnectionStatus.values

    def test_stored_statuses_are_the_approved_set(self):
        assert set(ConnectionStatus.values) == {
            "pending_authorization",
            "awaiting_resource_selection",
            "connected",
            "error",
            "reauth_required",
            "disconnected",
        }

    def test_all_statuses_includes_the_synthesized_one(self):
        assert ALL_STATUSES[0] == NOT_CONNECTED
        assert len(ALL_STATUSES) == len(ConnectionStatus.values) + 1


class TestIntegrationConnectionModel:
    def test_unique_per_project_and_provider(self, make_user_with_workspace, make_project):
        _user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        IntegrationConnection.objects.create(project=project, provider=ProviderKey.GA4)

        with pytest.raises(IntegrityError):
            IntegrationConnection.objects.create(project=project, provider=ProviderKey.GA4)

    def test_same_provider_allowed_on_a_different_project(
        self, make_user_with_workspace, make_project
    ):
        _user, workspace = make_user_with_workspace()
        first = make_project(workspace, name="One", website_url="https://one.example")
        second = make_project(workspace, name="Two", website_url="https://two.example")

        IntegrationConnection.objects.create(project=first, provider=ProviderKey.GA4)
        IntegrationConnection.objects.create(project=second, provider=ProviderKey.GA4)
        assert IntegrationConnection.objects.count() == 2

    def test_defaults_are_empty_not_null(self, make_user_with_workspace, make_project):
        """Later milestones fill these in; nothing is populated speculatively."""
        _user, workspace = make_user_with_workspace()
        connection = IntegrationConnection.objects.create(
            project=make_project(workspace), provider=ProviderKey.GA4
        )
        assert connection.status == ConnectionStatus.PENDING_AUTHORIZATION
        assert connection.external_resource_id == ""
        assert connection.external_resource_label == ""
        assert connection.external_resource_meta == {}
        assert connection.google_account_email == ""
        assert connection.granted_scopes == []
        assert connection.last_health_check_at is None
        assert connection.last_successful_check_at is None
        assert connection.connected_by is None

    def test_model_has_no_credential_fields(self):
        """Credentials live in their own table, never as columns here.

        Only concrete local fields are checked: the ``credential`` reverse
        accessor to IntegrationCredential is the separation working, not a
        token stored on this model.
        """
        field_names = {field.name for field in IntegrationConnection._meta.local_fields}
        forbidden = {
            "access_token",
            "refresh_token",
            "token",
            "client_secret",
            "credential",
            "credentials",
            "authorization_code",
            "code_verifier",
            "state",
        }
        assert not (field_names & forbidden)


class TestIntegrationsEndpoint:
    def test_empty_project_returns_both_providers_not_connected(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        response = client.get(integrations_url(project.id))
        assert response.status_code == 200
        assert [entry["provider"] for entry in response.data] == ["ga4", "search_console"]
        for entry in response.data:
            assert entry["status"] == NOT_CONNECTED
            assert entry["connection"] is None
            assert entry["display_name"]
            assert entry["description"]

    def test_listing_creates_no_rows(self, signed_in_client, make_project):
        """The catalog is merged in memory; not_connected is never persisted."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        assert IntegrationConnection.objects.count() == 0
        for _ in range(3):
            assert client.get(integrations_url(project.id)).status_code == 200
        assert IntegrationConnection.objects.count() == 0

    def test_stored_connection_is_merged_into_the_right_provider(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        checked_at = timezone.now()
        IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.CONNECTED,
            external_resource_id="properties/123456",
            external_resource_label="Acme Store — Web",
            google_account_email="owner@example.com",
            last_health_check_at=checked_at,
            last_successful_check_at=checked_at,
        )

        response = client.get(integrations_url(project.id))
        entries = {entry["provider"]: entry for entry in response.data}

        ga4 = entries["ga4"]
        assert ga4["status"] == "connected"
        assert ga4["connection"]["external_resource_id"] == "properties/123456"
        assert ga4["connection"]["external_resource_label"] == "Acme Store — Web"
        assert ga4["connection"]["google_account_email"] == "owner@example.com"
        assert ga4["connection"]["last_successful_check_at"] is not None

        # The other provider is untouched.
        assert entries["search_console"]["status"] == NOT_CONNECTED
        assert entries["search_console"]["connection"] is None

    @pytest.mark.parametrize("status", ConnectionStatus.values)
    def test_every_stored_status_serializes(self, signed_in_client, make_project, status):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        IntegrationConnection.objects.create(
            project=project, provider=ProviderKey.SEARCH_CONSOLE, status=status
        )

        response = client.get(integrations_url(project.id))
        entries = {entry["provider"]: entry for entry in response.data}
        assert entries["search_console"]["status"] == status
        assert entries["search_console"]["connection"]["status"] == status

    def test_another_projects_connection_does_not_leak(
        self, signed_in_client, make_project
    ):
        """Two projects in the same workspace keep their integrations separate."""
        client, _user, workspace = signed_in_client
        mine = make_project(workspace, name="Mine", website_url="https://mine.example")
        other = make_project(workspace, name="Other", website_url="https://other.example")
        IntegrationConnection.objects.create(
            project=other,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.CONNECTED,
            external_resource_label="Other project property",
        )

        response = client.get(integrations_url(mine.id))
        assert all(entry["connection"] is None for entry in response.data)
        assert "Other project property" not in response.content.decode()

    def test_response_exposes_no_unexpected_fields(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        IntegrationConnection.objects.create(
            project=project, provider=ProviderKey.GA4, granted_scopes=["scope-a"]
        )

        response = client.get(integrations_url(project.id))
        entry = response.data[0]
        assert set(entry) == {
            "provider",
            "display_name",
            "description",
            "status",
            "connection",
        }
        assert set(entry["connection"]) == {
            "id",
            "status",
            "external_resource_id",
            "external_resource_label",
            "google_account_email",
            "last_health_check_at",
            "last_successful_check_at",
            "last_error_code",
            "last_error_message",
            "created_at",
            "updated_at",
        }
        # Internal detail is not published. (The exact key sets asserted above
        # are the precise check; this catches a value leaking anywhere else.)
        body = response.content.decode()
        for absent in ("granted_scopes", "scope-a", "external_resource_meta"):
            assert absent not in body

    def test_serializer_declares_no_credential_field(self):
        assert not {
            field for field in IntegrationConnectionSerializer().fields
            if "token" in field or "secret" in field or "credential" in field
        }


class TestIntegrationsIsolation:
    @pytest.fixture
    def other_users_project(self, make_user):
        stranger = make_user(email="stranger@example.com")
        workspace = create_initial_workspace(stranger)
        project = Project.objects.create(
            workspace=workspace, name="Theirs", website_url="https://theirs.example"
        )
        IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.CONNECTED,
            external_resource_label="Private property name",
        )
        return project

    def test_foreign_project_returns_404(self, signed_in_client, other_users_project):
        client, _user, _workspace = signed_in_client
        response = client.get(integrations_url(other_users_project.id))
        assert response.status_code == 404
        assert response.data["error"]["code"] == "not_found"
        assert "Private property name" not in response.content.decode()

    def test_nonexistent_project_returns_404_identically(
        self, signed_in_client, other_users_project
    ):
        """A foreign project and a missing one are indistinguishable."""
        client, _user, _workspace = signed_in_client
        foreign = client.get(integrations_url(other_users_project.id))
        missing = client.get(integrations_url(9_999_999))
        assert foreign.status_code == missing.status_code == 404
        assert foreign.data == missing.data

    def test_unauthenticated_access_is_rejected(self, csrf_client, make_user_with_workspace):
        _user, workspace = make_user_with_workspace()
        project = Project.objects.create(
            workspace=workspace, name="Guarded", website_url="https://guarded.example"
        )
        response = csrf_client.get(integrations_url(project.id))
        assert response.status_code == 403
        assert response.data["error"]["code"] in {"not_authenticated", "permission_denied"}


class TestMergeService:
    def test_returns_catalog_order_regardless_of_stored_rows(
        self, make_user_with_workspace, make_project
    ):
        _user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        IntegrationConnection.objects.create(
            project=project, provider=ProviderKey.SEARCH_CONSOLE
        )

        entries = integrations_for_project(project)
        assert [entry.provider for entry in entries] == ["ga4", "search_console"]
        assert entries[0].connection is None
        assert entries[1].connection is not None


class TestCredentialNonExposure:
    """No token may reach an API response, a serializer, or the admin."""

    @pytest.fixture
    def connected(self, signed_in_client, make_project):
        from integrations.models import IntegrationCredential

        _client, _user, workspace = signed_in_client
        project = make_project(workspace)
        connection = IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.AWAITING_RESOURCE_SELECTION,
        )
        IntegrationCredential.objects.create(
            connection=connection,
            access_token="ya29.SECRET-ACCESS",
            refresh_token="1//SECRET-REFRESH",
        )
        return project

    def test_connection_model_still_has_no_token_field(self):
        field_names = {field.name for field in IntegrationConnection._meta.get_fields()}
        assert "access_token" not in field_names
        assert "refresh_token" not in field_names

    def test_integrations_response_contains_no_token(self, signed_in_client, connected):
        """Regression: the serializer must never learn to reach the credential."""
        client, _user, _workspace = signed_in_client
        response = client.get(integrations_url(connected.id))
        body = response.content.decode()
        for secret in ("ya29.SECRET-ACCESS", "1//SECRET-REFRESH", "access_token", "refresh_token"):
            assert secret not in body

    def test_credential_is_reachable_only_through_the_reverse_relation(self, connected):
        connection = IntegrationConnection.objects.get(project=connected)
        assert connection.credential.access_token == "ya29.SECRET-ACCESS"
        # But it is not a field on the connection itself.
        with pytest.raises(Exception):
            IntegrationConnection.objects.values("access_token").first()

    def test_credential_is_not_registered_in_the_admin(self):
        from django.contrib import admin

        from integrations.models import IntegrationCredential

        assert IntegrationCredential not in admin.site._registry
