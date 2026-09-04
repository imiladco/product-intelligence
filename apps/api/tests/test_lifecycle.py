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
from integrations.models import (
    IntegrationConnection,
    IntegrationCredential,
    OAuthAuthorizationRequest,
)
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


def disconnect_url(project_id, provider="ga4") -> str:
    return f"/api/projects/{project_id}/integrations/{provider}/disconnect"


class TestDisconnect:
    """§3.2. Ends the connection here without touching the Google grant.

    The grant belongs to the user's Google account, not to this application:
    revoking it would also break any other authorization the same consent
    covers. Disconnecting is local, and the revoke endpoint is never called.
    """

    @responses.activate
    def test_the_credential_row_is_deleted_and_the_selection_remembered(
        self, connected
    ):
        client, _user, project, connection = connected
        before_success = connection.last_successful_check_at

        response = client.post(disconnect_url(project.pk), {}, format="json")

        assert response.status_code == 200
        assert response.data["status"] == "disconnected"
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.DISCONNECTED
        # Deleted, not blanked: an empty token row still looks like a credential.
        assert not IntegrationCredential.objects.filter(connection=connection).exists()
        # The selection is remembered so reconnecting can restore it.
        assert connection.external_resource_id == GA4_RESOURCE
        assert connection.external_resource_label == "poolino"
        assert connection.last_successful_check_at == before_success
        assert len(responses.calls) == 0

    def test_one_audit_row_records_the_transition(self, connected):
        client, user, project, connection = connected

        client.post(disconnect_url(project.pk), {}, format="json")

        events = AuditEvent.objects.filter(
            action=AuditEvent.Action.INTEGRATION_DISCONNECTED
        )
        assert events.count() == 1
        event = events.get()
        assert event.actor == user
        assert event.metadata["previous_status"] == ConnectionStatus.CONNECTED
        assert event.metadata["status"] == ConnectionStatus.DISCONNECTED

    def test_the_lifecycle_generation_advances(self, connected):
        client, _user, project, connection = connected
        before = connection.lifecycle_generation

        client.post(disconnect_url(project.pk), {}, format="json")

        connection.refresh_from_db()
        assert connection.lifecycle_generation == before + 1

    def test_outstanding_authorization_requests_are_consumed(self, connected):
        client, user, project, connection = connected
        outstanding = OAuthAuthorizationRequest.objects.create(
            state_hash="a" * 64,
            project=project,
            provider=ProviderKey.GA4,
            user=user,
            code_verifier="verifier",
            connection_generation=connection.lifecycle_generation,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        client.post(disconnect_url(project.pk), {}, format="json")

        outstanding.refresh_from_db()
        assert outstanding.consumed_at is not None

    def test_a_second_disconnect_is_inert_but_still_consumes(self, connected):
        """§9.1. Idempotent in what it means, not in what it touches."""
        client, user, project, connection = connected
        client.post(disconnect_url(project.pk), {}, format="json")
        connection.refresh_from_db()
        before_generation = connection.lifecycle_generation
        before_updated = connection.updated_at
        outstanding = OAuthAuthorizationRequest.objects.create(
            state_hash="b" * 64,
            project=project,
            provider=ProviderKey.GA4,
            user=user,
            code_verifier="verifier",
            connection_generation=before_generation,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        response = client.post(disconnect_url(project.pk), {}, format="json")

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.DISCONNECTED
        assert not IntegrationCredential.objects.filter(connection=connection).exists()
        # No second event: nothing transitioned.
        assert (
            AuditEvent.objects.filter(
                action=AuditEvent.Action.INTEGRATION_DISCONNECTED
            ).count()
            == 1
        )
        # But the generation still advances and the request is still consumed:
        # a repeat disconnect must invalidate a callback started in between.
        assert connection.lifecycle_generation == before_generation + 1
        assert connection.updated_at > before_updated
        outstanding.refresh_from_db()
        assert outstanding.consumed_at is not None

    @responses.activate
    def test_the_google_revoke_endpoint_is_never_called(self, connected):
        client, _user, project, _connection = connected
        responses.add(responses.POST, "https://oauth2.googleapis.com/revoke", json={})

        client.post(disconnect_url(project.pk), {}, format="json")

        assert all("revoke" not in call.request.url for call in responses.calls)

    def test_with_no_connection_row_it_creates_nothing(
        self, signed_in_client, make_project
    ):
        """Already not connected: the meaningful result is true, so 200."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        response = client.post(disconnect_url(project.pk), {}, format="json")

        assert response.status_code == 200
        assert response.data["status"] == "not_connected"
        assert not IntegrationConnection.objects.filter(project=project).exists()
        assert not AuditEvent.objects.filter(
            action=AuditEvent.Action.INTEGRATION_DISCONNECTED
        ).exists()

    def test_foreign_project_is_not_found(
        self, signed_in_client, make_user_with_workspace, make_project
    ):
        client, _user, _workspace = signed_in_client
        _other, other_workspace = make_user_with_workspace(email="other@example.com")
        foreign = make_project(other_workspace)

        assert (
            client.post(disconnect_url(foreign.pk), {}, format="json").status_code
            == 404
        )

    def test_unknown_provider_is_not_found(self, connected):
        client, _user, project, _connection = connected
        assert (
            client.post(
                disconnect_url(project.pk, "not_a_provider"), {}, format="json"
            ).status_code
            == 404
        )


# --- Reconnect terminal behaviour (§5.1.1) -----------------------------------

CALLBACK = "/api/integrations/oauth/google/callback"
TOKEN_URI = "https://oauth2.googleapis.com/token"


@pytest.fixture(autouse=True)
def oauth_settings(settings):
    settings.GOOGLE_OAUTH_REDIRECT_URI = (
        "http://localhost:3000/api/integrations/oauth/google/callback"
    )
    return settings


def stub_token(*, scope=GA4_SCOPE, refresh_token="refresh-token-2"):
    body = {
        "access_token": "access-token-2",
        "expires_in": 3599,
        "scope": scope,
        "token_type": "Bearer",
    }
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    responses.add(responses.POST, TOKEN_URI, json=body)


def start_flow(client, project, provider="ga4") -> str:
    from urllib.parse import parse_qs, urlparse

    response = client.post(
        f"/api/projects/{project.pk}/integrations/{provider}/authorize",
        {},
        format="json",
    )
    assert response.status_code == 200, response.data
    url = response.data["authorization_url"]
    return parse_qs(urlparse(url).query)["state"][0]


class TestReconnectTerminalState:
    """§5.1.1. A reconnect ends where the stored selection actually stands.

    M3 ended every callback in `awaiting_resource_selection`, which threw away
    a selection the user had made and still wanted. The selection is now
    re-verified with the new credential, and the four outcome classes are told
    apart rather than collapsed.
    """

    @responses.activate
    def test_a_still_valid_selection_returns_straight_to_connected(self, connected):
        client, _user, project, connection = connected
        connection.status = ConnectionStatus.REAUTH_REQUIRED
        connection.last_error_code = "credential_refresh_failed"
        connection.save(update_fields=["status", "last_error_code"])
        before_success = connection.last_successful_check_at
        state = start_flow(client, project)
        stub_token()
        stub_property()

        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.CONNECTED
        assert connection.external_resource_id == GA4_RESOURCE
        assert connection.last_error_code == ""
        assert connection.last_successful_check_at > before_success

    @responses.activate
    def test_success_leaves_selection_fields_unchanged(self, connected):
        client, _user, project, connection = connected
        before = (
            connection.external_resource_id,
            connection.external_resource_label,
            dict(connection.external_resource_meta),
        )
        state = start_flow(client, project)
        stub_token()
        stub_property(display_name="A DIFFERENT NAME")

        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        connection.refresh_from_db()
        assert (
            connection.external_resource_id,
            connection.external_resource_label,
            connection.external_resource_meta,
        ) == before
        assert connection.status == ConnectionStatus.CONNECTED

    @responses.activate
    @pytest.mark.parametrize("status", [403, 404])
    def test_an_inaccessible_resource_is_retained_for_re_picking(
        self, connected, status
    ):
        client, _user, project, connection = connected
        before_success = connection.last_successful_check_at
        state = start_flow(client, project)
        stub_token()
        stub_property(status=status)

        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        # Retained (§5.2): the card can say what stopped working.
        assert connection.external_resource_id == GA4_RESOURCE
        assert connection.external_resource_label == "poolino"
        assert connection.last_error_code == "resource_not_accessible"
        assert connection.last_successful_check_at == before_success

    @responses.activate
    def test_a_credential_rejected_after_the_exchange_is_reauth_required(
        self, connected
    ):
        """Access revoked between the token exchange and the verify."""
        client, _user, project, connection = connected
        state = start_flow(client, project)
        stub_token()
        stub_property(status=401)

        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.REAUTH_REQUIRED
        assert connection.external_resource_id == GA4_RESOURCE
        assert connection.last_error_code == "credential_refresh_failed"

    @responses.activate
    @pytest.mark.parametrize("status", [429, 503])
    def test_a_transient_failure_never_claims_the_resource_is_gone(
        self, connected, status
    ):
        client, _user, project, connection = connected
        state = start_flow(client, project)
        stub_token()
        stub_property(status=status)

        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert connection.external_resource_id == GA4_RESOURCE
        # The transient class, so the card offers Test connection rather than
        # sending the user to re-pick a property that is probably fine.
        assert connection.last_error_code == "resource_unavailable"

    @responses.activate
    def test_with_nothing_selected_it_still_awaits_selection(
        self, signed_in_client, make_project
    ):
        """M3 behaviour, intact: there is no selection to preserve."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()

        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        connection = IntegrationConnection.objects.get(project=project)
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert connection.last_error_code == ""
        # No verification call is possible with nothing selected.
        assert all("analyticsadmin" not in c.request.url for c in responses.calls)


class TestReconnectCancellation:
    """§5.4. Backing out of a consent screen damages nothing."""

    @responses.activate
    def test_denial_on_an_existing_integration_changes_nothing(self, connected):
        client, _user, project, connection = connected
        before = (
            connection.status,
            connection.external_resource_id,
            connection.external_resource_label,
        )
        credential_before = connection.credential.refresh_token
        state = start_flow(client, project)

        client.get(CALLBACK, {"state": state, "error": "access_denied"})

        connection.refresh_from_db()
        assert (
            connection.status,
            connection.external_resource_id,
            connection.external_resource_label,
        ) == before
        assert connection.credential.refresh_token == credential_before
        assert connection.last_error_code != "access_denied"
        assert AuditEvent.objects.filter(
            action=AuditEvent.Action.INTEGRATION_AUTHORIZATION_FAILED
        ).exists()

    @responses.activate
    def test_denial_of_a_first_authorization_removes_the_row(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        client.get(CALLBACK, {"state": state, "error": "access_denied"})

        assert not IntegrationConnection.objects.filter(project=project).exists()

    @responses.activate
    def test_a_withheld_scope_is_still_an_error(self, connected):
        """Denial means 'I did not do this'; a missing scope is different."""
        client, _user, project, connection = connected
        state = start_flow(client, project)
        stub_token(scope="https://www.googleapis.com/auth/userinfo.email")

        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.ERROR
        assert connection.last_error_code == "scope_not_granted"


class TestReconnectStageFiveDiscard:
    """§9.4.2 stage 5: credentials committed do not license a terminal write."""

    @responses.activate
    def test_a_disconnect_between_persistence_and_verification_discards_it(
        self, connected, monkeypatch
    ):
        from integrations import lifecycle_service
        from integrations.providers import get_provider

        client, user, project, connection = connected
        state = start_flow(client, project)
        stub_token()

        catalog = get_provider(ProviderKey.GA4).resources
        original = catalog.verify_resource

        def disconnect_then_verify(access_token, resource_id):
            lifecycle_service.disconnect(
                user=user, project=project, provider_key=ProviderKey.GA4
            )
            return original(access_token, resource_id)

        stub_property()
        monkeypatch.setattr(catalog, "verify_resource", disconnect_then_verify)

        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        connection.refresh_from_db()
        # The disconnect stands; the callback wrote no terminal state over it.
        assert connection.status == ConnectionStatus.DISCONNECTED
        assert connection.last_error_code == ""
