"""Integration lifecycle: health check, disconnect, reconnect terminal behaviour.

Every case runs for both providers where the behaviour is provider-neutral,
which is the parity the M5 abstraction earns. No test contacts Google.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import responses
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from integrations.models import IntegrationConnection, IntegrationCredential
from integrations.providers import ProviderKey
from integrations.status import ConnectionStatus

pytestmark = pytest.mark.django_db

GA4_BASE = "https://analyticsadmin.googleapis.com/v1beta"
GSC_BASE = "https://www.googleapis.com/webmasters/v3"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GA4_RESOURCE = "properties/111"


@pytest.fixture(autouse=True)
def google_settings(settings):
    settings.GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    settings.GOOGLE_CLIENT_SECRET = "test-client-secret"
    settings.GA4_ADMIN_BASE_URL = GA4_BASE
    settings.SEARCH_CONSOLE_BASE_URL = GSC_BASE
    return settings


def health_check_url(project_id, provider="ga4") -> str:
    return f"/api/projects/{project_id}/integrations/{provider}/health-check"


def stub_property(status=200, display_name="poolino"):
    body = (
        {
            "name": GA4_RESOURCE,
            "displayName": display_name,
            "propertyType": "PROPERTY_TYPE_ORDINARY",
            "parent": "accounts/1",
        }
        if status == 200
        else {"error": {"message": "google detail that must not leak"}}
    )
    responses.add(responses.GET, f"{GA4_BASE}/{GA4_RESOURCE}", json=body, status=status)


@pytest.fixture
def connected(signed_in_client, make_project):
    """A GA4 integration in `connected`, with a selected property."""
    client, user, workspace = signed_in_client
    project = make_project(workspace)
    connection = IntegrationConnection.objects.create(
        project=project,
        provider=ProviderKey.GA4,
        status=ConnectionStatus.CONNECTED,
        external_resource_id=GA4_RESOURCE,
        external_resource_label="poolino",
        external_resource_meta={"account": "accounts/1", "property_type": "X"},
        granted_scopes=[GA4_SCOPE],
        connected_by=user,
        last_health_check_at=timezone.now() - timedelta(days=1),
        last_successful_check_at=timezone.now() - timedelta(days=1),
    )
    IntegrationCredential.objects.create(
        connection=connection,
        access_token="access-token-1",
        refresh_token="refresh-token-1",
        access_token_expires_at=timezone.now() + timedelta(hours=1),
    )
    return client, user, project, connection


class TestHealthCheckOutcomes:
    @responses.activate
    def test_success_sets_both_timestamps_and_clears_errors(self, connected):
        client, _user, project, connection = connected
        connection.last_error_code = "stale"
        connection.last_error_message = "stale"
        connection.save(update_fields=["last_error_code", "last_error_message"])
        before = connection.last_successful_check_at
        stub_property()

        response = client.post(health_check_url(project.pk), {}, format="json")

        assert response.status_code == 200
        assert response.data["status"] == "connected"
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.CONNECTED
        assert connection.last_successful_check_at > before
        assert connection.last_health_check_at > before
        assert connection.last_error_code == ""

    @responses.activate
    def test_success_leaves_selection_fields_unchanged(self, connected):
        """A health check is not a selection (§T07)."""
        client, _user, project, connection = connected
        before = (
            connection.external_resource_id,
            connection.external_resource_label,
            dict(connection.external_resource_meta),
        )
        # The provider even returns a different label; it must not be stored.
        stub_property(display_name="A DIFFERENT NAME")

        client.post(health_check_url(project.pk), {}, format="json")

        connection.refresh_from_db()
        assert (
            connection.external_resource_id,
            connection.external_resource_label,
            connection.external_resource_meta,
        ) == before

    @responses.activate
    @pytest.mark.parametrize("status", [403, 404])
    def test_resource_gone_is_an_error_state(self, connected, status):
        client, _user, project, connection = connected
        before = connection.last_successful_check_at
        stub_property(status=status)

        response = client.post(health_check_url(project.pk), {}, format="json")

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.ERROR
        assert connection.last_error_code == "resource_not_accessible"
        assert connection.last_successful_check_at == before
        assert connection.last_health_check_at > before

    @responses.activate
    def test_credential_rejected_requires_reauthorization(self, connected):
        client, _user, project, connection = connected
        stub_property(status=401)

        response = client.post(health_check_url(project.pk), {}, format="json")

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.REAUTH_REQUIRED
        assert connection.last_error_code == "credential_refresh_failed"

    @responses.activate
    @pytest.mark.parametrize("status", [429, 500, 503])
    def test_transient_failure_leaves_status_unchanged(self, connected, status):
        client, _user, project, connection = connected
        before_status = connection.status
        before_success = connection.last_successful_check_at
        stub_property(status=status)

        response = client.post(health_check_url(project.pk), {}, format="json")

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == before_status
        assert connection.last_error_code == "resource_unavailable"
        assert connection.last_successful_check_at == before_success
        assert connection.last_health_check_at > before_success


class TestHealthCheckPreconditions:
    @responses.activate
    def test_nothing_selected_is_a_conflict_with_no_outbound_call(
        self, signed_in_client, make_project
    ):
        client, user, workspace = signed_in_client
        project = make_project(workspace)
        connection = IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.AWAITING_RESOURCE_SELECTION,
            granted_scopes=[GA4_SCOPE],
        )
        IntegrationCredential.objects.create(
            connection=connection,
            access_token="access-token-1",
            refresh_token="refresh-token-1",
            access_token_expires_at=timezone.now() + timedelta(hours=1),
        )

        response = client.post(health_check_url(project.pk), {}, format="json")

        assert response.status_code == 409
        assert response.data["error"]["code"] == "resource_missing"
        assert len(responses.calls) == 0

    @responses.activate
    def test_disconnected_has_no_credential_to_check(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.DISCONNECTED,
            external_resource_id=GA4_RESOURCE,
        )

        response = client.post(health_check_url(project.pk), {}, format="json")

        assert response.status_code == 409
        assert response.data["error"]["code"] == "credential_missing"
        assert len(responses.calls) == 0

    @responses.activate
    def test_the_identifier_comes_from_the_database_not_the_request(self, connected):
        client, _user, project, _connection = connected
        stub_property()

        client.post(
            health_check_url(project.pk),
            {"resource_id": "properties/999999"},
            format="json",
        )

        # Only the stored resource was ever asked about.
        assert responses.calls[0].request.url == f"{GA4_BASE}/{GA4_RESOURCE}"


class TestHealthCheckTokenAcquisitionFailures:
    """access_token_for can fail before verification begins (§T07 fix 3)."""

    @responses.activate
    def test_credential_refresh_failure_returns_200_reauth_required(self, connected):
        client, _user, project, connection = connected
        credential = connection.credential
        credential.access_token_expires_at = timezone.now() - timedelta(minutes=5)
        credential.save(update_fields=["access_token_expires_at"])
        responses.add(
            responses.POST,
            "https://oauth2.googleapis.com/token",
            json={"error": "invalid_grant"},
            status=400,
        )

        response = client.post(health_check_url(project.pk), {}, format="json")

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.REAUTH_REQUIRED
        assert connection.last_error_code == "credential_refresh_failed"
        assert connection.last_health_check_at is not None
        # No provider call was made.
        assert all("analyticsadmin" not in c.request.url for c in responses.calls)

    @responses.activate
    def test_transient_token_failure_returns_200_status_unchanged(self, connected):
        import requests as requests_lib

        client, _user, project, connection = connected
        before_status = connection.status
        before_success = connection.last_successful_check_at
        credential = connection.credential
        credential.access_token_expires_at = timezone.now() - timedelta(minutes=5)
        credential.save(update_fields=["access_token_expires_at"])
        responses.add(
            responses.POST,
            "https://oauth2.googleapis.com/token",
            body=requests_lib.ConnectionError("unreachable"),
        )

        response = client.post(health_check_url(project.pk), {}, format="json")

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == before_status
        assert connection.last_error_code == "resource_unavailable"
        assert connection.last_successful_check_at == before_success


class TestHealthCheckTenancy:
    def test_foreign_project_is_not_found(
        self, signed_in_client, make_user_with_workspace, make_project
    ):
        client, _user, _workspace = signed_in_client
        _other, other_workspace = make_user_with_workspace(email="other@example.com")
        foreign = make_project(other_workspace)

        response = client.post(health_check_url(foreign.pk), {}, format="json")
        assert response.status_code == 404

    def test_unknown_provider_is_not_found(self, connected):
        client, _user, project, _connection = connected
        response = client.post(
            health_check_url(project.pk, "not_a_provider"), {}, format="json"
        )
        assert response.status_code == 404

    def test_authentication_is_required(self, connected):
        _client, _user, project, _connection = connected
        anonymous = APIClient(enforce_csrf_checks=True)
        assert anonymous.post(health_check_url(project.pk)).status_code == 403
