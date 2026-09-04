"""GA4 property discovery, selection, and the credential refresh they need.

No test contacts Google. Every outbound call is stubbed with `responses`, and
several tests assert that *no* call was made at all — a guard that only works
because rejection happens before the request is built.
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests
import responses
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from integrations.google import ga4
from integrations.google.credentials import EXPIRY_SKEW, _persist, access_token_for
from integrations.google.errors import (
    CredentialMissing,
    CredentialRefreshFailed,
    ResourceUnavailable,
)
from integrations.google.oauth import TOKEN_URI
from integrations.models import IntegrationConnection, IntegrationCredential
from integrations.providers import ProviderKey
from integrations.status import ConnectionStatus
from projects.models import Project
from tests.conftest import PASSWORD
from workspaces.models import Membership
from workspaces.services import create_initial_workspace

pytestmark = pytest.mark.django_db

ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"
SUMMARIES_URL = f"{ADMIN_BASE}/accountSummaries"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


@pytest.fixture(autouse=True)
def google_settings(settings):
    settings.GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    settings.GOOGLE_CLIENT_SECRET = "test-client-secret"
    settings.GA4_ADMIN_BASE_URL = ADMIN_BASE
    return settings


def resources_url(project_id, provider="ga4") -> str:
    return f"/api/projects/{project_id}/integrations/{provider}/resources"


def selection_url(project_id, provider="ga4") -> str:
    return f"/api/projects/{project_id}/integrations/{provider}/resource"


@pytest.fixture
def connected_project(signed_in_client, make_project):
    """A project whose GA4 integration is authorized and awaiting a property."""
    client, user, workspace = signed_in_client
    project = make_project(workspace)
    connection = IntegrationConnection.objects.create(
        project=project,
        provider=ProviderKey.GA4,
        status=ConnectionStatus.AWAITING_RESOURCE_SELECTION,
        granted_scopes=[GA4_SCOPE],
        connected_by=user,
    )
    IntegrationCredential.objects.create(
        connection=connection,
        access_token="access-token-1",
        refresh_token="refresh-token-1",
        access_token_expires_at=timezone.now() + timedelta(hours=1),
    )
    return client, user, project, connection


def summaries_body(*, accounts, next_page_token=None):
    body = {"accountSummaries": accounts}
    if next_page_token:
        body["nextPageToken"] = next_page_token
    return body


def account(display_name="Example Ltd", account_id="accounts/1", properties=()):
    return {
        "account": account_id,
        "displayName": display_name,
        "propertySummaries": list(properties),
    }


def summary(property_id="properties/111", display_name="example.com"):
    return {
        "property": property_id,
        "displayName": display_name,
        "propertyType": "PROPERTY_TYPE_ORDINARY",
    }


def stub_summaries(*pages):
    for page in pages:
        responses.add(responses.GET, SUMMARIES_URL, json=page, status=200)


def stub_property(
    property_id="properties/111",
    display_name="example.com",
    status=200,
    parent="accounts/1",
):
    body = (
        {
            "name": property_id,
            "displayName": display_name,
            "propertyType": "PROPERTY_TYPE_ORDINARY",
            "parent": parent,
        }
        if status == 200
        else {"error": {"message": "some Google detail that must not leak"}}
    )
    responses.add(responses.GET, f"{ADMIN_BASE}/{property_id}", json=body, status=status)


# --- Tenancy ----------------------------------------------------------------


class TestTenancy:
    def test_other_workspace_project_is_not_found(
        self, signed_in_client, make_user_with_workspace, make_project
    ):
        client, _user, _workspace = signed_in_client
        _other_user, other_workspace = make_user_with_workspace(email="other@example.com")
        foreign = make_project(other_workspace)

        assert client.get(resources_url(foreign.pk)).status_code == 404
        response = client.post(
            selection_url(foreign.pk), {"resource_id": "properties/111"}, format="json"
        )
        assert response.status_code == 404

    def test_unknown_provider_is_not_found(self, connected_project):
        client, _user, project, _connection = connected_project
        assert client.get(resources_url(project.pk, "not_a_provider")).status_code == 404

    def test_authentication_is_required(self, connected_project):
        # A fresh client on purpose: the signed_in_client fixture is built from
        # api_client, so reusing that fixture here would test the signed-in
        # client a second time rather than an anonymous one.
        _client, _user, project, _connection = connected_project
        anonymous = APIClient(enforce_csrf_checks=True)

        # 403, not 401: session auth sends no WWW-Authenticate challenge, which
        # is what DRF keys 401 off. Matches every other endpoint in the suite.
        assert anonymous.get(resources_url(project.pk)).status_code == 403


# --- Discovery --------------------------------------------------------------


class TestDiscovery:
    @responses.activate
    def test_lists_properties_grouped_by_account(self, connected_project):
        client, _user, project, _connection = connected_project
        stub_summaries(
            summaries_body(
                accounts=[
                    account(
                        properties=[
                            summary("properties/111", "b-site"),
                            summary("properties/222", "a-site"),
                        ]
                    )
                ]
            )
        )

        response = client.get(resources_url(project.pk))

        assert response.status_code == 200
        assert response.data["truncated"] is False
        assert [item["id"] for item in response.data["resources"]] == [
            "properties/222",
            "properties/111",
        ]
        assert response.data["resources"][0]["group_label"] == "Example Ltd"
        assert response.data["resources"][0]["resource_type"] == "PROPERTY_TYPE_ORDINARY"

    @responses.activate
    def test_sends_the_documented_maximum_page_size(self, connected_project):
        client, _user, project, _connection = connected_project
        stub_summaries(summaries_body(accounts=[]))

        client.get(resources_url(project.pk))

        assert "pageSize=200" in responses.calls[0].request.url

    @responses.activate
    def test_follows_next_page_token(self, connected_project):
        client, _user, project, _connection = connected_project
        stub_summaries(
            summaries_body(
                accounts=[account(properties=[summary("properties/111", "one")])],
                next_page_token="page-2",
            ),
            summaries_body(
                accounts=[account(properties=[summary("properties/222", "two")])]
            ),
        )

        response = client.get(resources_url(project.pk))

        assert len(responses.calls) == 2
        assert "pageToken=page-2" in responses.calls[1].request.url
        assert len(response.data["resources"]) == 2
        assert response.data["truncated"] is False

    @responses.activate
    def test_stops_at_the_page_cap_and_reports_truncation(self, connected_project):
        client, _user, project, _connection = connected_project
        for index in range(ga4.MAX_PAGES + 2):
            responses.add(
                responses.GET,
                SUMMARIES_URL,
                json=summaries_body(
                    accounts=[account(properties=[summary(f"properties/{index}")])],
                    next_page_token=f"page-{index}",
                ),
                status=200,
            )

        response = client.get(resources_url(project.pk))

        assert len(responses.calls) == ga4.MAX_PAGES
        assert response.data["truncated"] is True

    @responses.activate
    def test_no_properties_is_an_empty_list_not_an_error(self, connected_project):
        client, _user, project, _connection = connected_project
        stub_summaries(summaries_body(accounts=[account(properties=[])]))

        response = client.get(resources_url(project.pk))

        assert response.status_code == 200
        assert response.data["resources"] == []

    @responses.activate
    def test_malformed_summaries_are_skipped_not_fatal(self, connected_project):
        client, _user, project, _connection = connected_project
        stub_summaries(
            summaries_body(
                accounts=[
                    account(
                        properties=[
                            {"displayName": "no identifier"},
                            {"property": "not-a-property-name", "displayName": "bad id"},
                            summary("properties/111", "good"),
                        ]
                    )
                ]
            )
        )

        response = client.get(resources_url(project.pk))

        assert [item["id"] for item in response.data["resources"]] == ["properties/111"]

    @responses.activate
    def test_google_failure_is_a_service_error_not_a_state_change(
        self, connected_project
    ):
        client, _user, project, connection = connected_project
        responses.add(responses.GET, SUMMARIES_URL, json={}, status=503)

        response = client.get(resources_url(project.pk))

        assert response.status_code == 503
        assert response.data["error"]["code"] == "resource_unavailable"
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION


# --- Selection and verification ---------------------------------------------


class TestSelection:
    @responses.activate
    def test_verified_selection_connects_the_integration(self, connected_project):
        client, _user, project, connection = connected_project
        stub_property(display_name="Google's own name")

        response = client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.CONNECTED
        assert connection.external_resource_id == "properties/111"
        assert connection.external_resource_label == "Google's own name"
        assert connection.last_successful_check_at is not None
        assert connection.last_health_check_at is not None
        assert response.data["status"] == "connected"

    @responses.activate
    def test_a_label_in_the_request_body_has_no_effect(self, connected_project):
        """The browser is not a source of truth for what a property is called."""
        client, _user, project, connection = connected_project
        stub_property(display_name="Google's own name")

        client.post(
            selection_url(project.pk),
            {
                "resource_id": "properties/111",
                "external_resource_label": "Attacker's label",
                "label": "Attacker's label",
                "status": "connected",
            },
            format="json",
        )

        connection.refresh_from_db()
        assert connection.external_resource_label == "Google's own name"

    @responses.activate
    def test_forbidden_and_missing_are_indistinguishable(self, connected_project):
        client, _user, project, _connection = connected_project
        stub_property(status=403)
        forbidden = client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        responses.reset()
        stub_property(property_id="properties/222", status=404)
        missing = client.post(
            selection_url(project.pk), {"resource_id": "properties/222"}, format="json"
        )

        assert forbidden.status_code == missing.status_code == 400
        assert forbidden.data["error"] == missing.data["error"]
        assert forbidden.data["error"]["code"] == "resource_not_accessible"

    @responses.activate
    def test_failed_verification_changes_nothing(self, connected_project):
        client, _user, project, connection = connected_project
        stub_property(status=403)

        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert connection.external_resource_id == ""
        assert connection.last_successful_check_at is None
        assert connection.last_health_check_at is None

    @responses.activate
    @pytest.mark.parametrize(
        "resource_id",
        [
            "properties/../../accounts/1",
            "https://analyticsadmin.googleapis.com/v1beta/properties/111",
            "properties/111?alt=media",
            "accounts/111",
            "properties/",
            "properties/abc",
        ],
    )
    def test_malformed_identifiers_never_reach_google(
        self, connected_project, resource_id
    ):
        client, _user, project, _connection = connected_project

        response = client.post(
            selection_url(project.pk), {"resource_id": resource_id}, format="json"
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "invalid_resource_id"
        assert len(responses.calls) == 0

    @responses.activate
    def test_selection_requires_an_authorized_connection(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.PENDING_AUTHORIZATION,
        )

        response = client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "credential_missing"
        assert len(responses.calls) == 0

    @responses.activate
    def test_reauth_required_says_so_rather_than_not_authorized(
        self, connected_project
    ):
        client, _user, project, connection = connected_project
        connection.status = ConnectionStatus.REAUTH_REQUIRED
        connection.save(update_fields=["status"])

        response = client.get(resources_url(project.pk))

        assert response.status_code == 409
        assert response.data["error"]["code"] == "credential_refresh_failed"


# --- The reductions this milestone agreed to ---------------------------------


class TestScopeReductions:
    @responses.activate
    def test_reselecting_the_same_property_is_idempotent(self, connected_project):
        """A retried or double-clicked confirm must not become an error."""
        client, _user, project, connection = connected_project
        stub_property()
        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )
        connection.refresh_from_db()
        first_check = connection.last_successful_check_at

        stub_property()
        response = client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.CONNECTED
        assert connection.external_resource_id == "properties/111"
        assert connection.last_successful_check_at >= first_check

    @responses.activate
    def test_changing_to_a_different_property_is_refused(self, connected_project):
        client, _user, project, connection = connected_project
        stub_property()
        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )
        connection.refresh_from_db()
        before = (
            connection.external_resource_id,
            connection.external_resource_label,
            connection.last_health_check_at,
            connection.last_successful_check_at,
        )
        responses.reset()

        response = client.post(
            selection_url(project.pk), {"resource_id": "properties/999"}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "resource_change_not_supported"
        assert len(responses.calls) == 0
        connection.refresh_from_db()
        assert (
            connection.external_resource_id,
            connection.external_resource_label,
            connection.last_health_check_at,
            connection.last_successful_check_at,
        ) == before


# --- Credential refresh ------------------------------------------------------


def stub_refresh(*, access_token="access-token-2", refresh_token=None, status=200):
    body = {"access_token": access_token, "expires_in": 3599, "token_type": "Bearer"}
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    if status != 200:
        body = {"error": "invalid_grant", "error_description": "Token has been revoked."}
    responses.add(responses.POST, TOKEN_URI, json=body, status=status)


class TestCredentialRefresh:
    @responses.activate
    def test_a_valid_token_is_used_as_is(self, connected_project):
        _client, _user, _project, connection = connected_project

        token = access_token_for(connection)

        assert token == "access-token-1"
        assert len(responses.calls) == 0

    @responses.activate
    def test_an_expired_token_is_refreshed_and_persisted(self, connected_project):
        _client, _user, _project, connection = connected_project
        credential = connection.credential
        credential.access_token_expires_at = timezone.now() + EXPIRY_SKEW / 2
        credential.save(update_fields=["access_token_expires_at"])
        stub_refresh()

        token = access_token_for(connection)

        assert token == "access-token-2"
        credential.refresh_from_db()
        assert credential.access_token == "access-token-2"
        assert credential.access_token_expires_at > timezone.now()

    @responses.activate
    def test_an_unknown_expiry_is_treated_as_expired(self, connected_project):
        _client, _user, _project, connection = connected_project
        credential = connection.credential
        credential.access_token_expires_at = None
        credential.save(update_fields=["access_token_expires_at"])
        stub_refresh()

        assert access_token_for(connection) == "access-token-2"

    @responses.activate
    def test_a_response_without_a_refresh_token_keeps_the_stored_one(
        self, connected_project
    ):
        """The standing rule: never blank a working refresh token.

        Google returns a refresh token only when it issues a new one, so the
        common case is a response carrying a fresh access token and expiry and
        no refresh token at all. The whole refresh must apply, and the stored
        refresh token must come through it untouched.

        The access-token and expiry assertions are what make this a real
        regression: without them the test would still pass if the refresh
        silently did nothing, which is the other way to "preserve" a token.
        """
        _client, _user, _project, connection = connected_project
        credential = connection.credential
        expired_at = timezone.now() - timedelta(minutes=5)
        credential.access_token_expires_at = expired_at
        credential.save(update_fields=["access_token_expires_at"])
        assert credential.refresh_token == "refresh-token-1"

        # A response with a new access token and expiry, and no refresh token.
        stub_refresh(access_token="access-token-2", refresh_token=None)
        assert "refresh_token" not in json.loads(responses.registered()[0].body)

        returned = access_token_for(connection)

        credential.refresh_from_db()
        # The refresh happened...
        assert returned == "access-token-2"
        assert credential.access_token == "access-token-2"
        assert credential.access_token_expires_at > timezone.now()
        assert credential.access_token_expires_at != expired_at
        # ...and it did not cost the stored refresh token.
        assert credential.refresh_token == "refresh-token-1"

    def test_persisting_never_blanks_a_stored_refresh_token(self, connected_project):
        """The guard in _persist, tested without google-auth in the way.

        google-auth already defaults a missing refresh_token to the current one
        (google/oauth2/_client.py: `response_data.get("refresh_token",
        refresh_token)`), so the end-to-end test above passes even if our own
        guard is removed. That makes the guard the second of two layers, and
        an untested layer is not a layer — this pins it directly, so a library
        change cannot silently leave nothing standing between an empty value
        and the stored token.
        """
        _client, _user, _project, connection = connected_project
        credential = connection.credential
        blank = SimpleNamespace(
            token="access-token-2",
            refresh_token="",
            expiry=timezone.now() + timedelta(hours=1),
        )

        _persist(credential, blank)

        credential.refresh_from_db()
        assert credential.refresh_token == "refresh-token-1"
        assert credential.access_token == "access-token-2"

    @responses.activate
    def test_a_new_refresh_token_replaces_the_stored_one(self, connected_project):
        _client, _user, _project, connection = connected_project
        credential = connection.credential
        credential.access_token_expires_at = None
        credential.save(update_fields=["access_token_expires_at"])
        stub_refresh(refresh_token="refresh-token-2")

        access_token_for(connection)

        credential.refresh_from_db()
        assert credential.refresh_token == "refresh-token-2"

    @responses.activate
    def test_invalid_grant_requires_reauthorization(self, connected_project):
        _client, _user, _project, connection = connected_project
        credential = connection.credential
        credential.access_token_expires_at = None
        credential.save(update_fields=["access_token_expires_at"])
        stub_refresh(status=400)

        with pytest.raises(CredentialRefreshFailed):
            access_token_for(connection)

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.REAUTH_REQUIRED
        assert connection.last_error_code == "credential_refresh_failed"

    @responses.activate
    def test_a_transport_failure_does_not_change_state(self, connected_project):
        _client, _user, _project, connection = connected_project
        credential = connection.credential
        credential.access_token_expires_at = None
        credential.save(update_fields=["access_token_expires_at"])
        responses.add(
            responses.POST, TOKEN_URI, body=requests.ConnectionError("boom")
        )

        with pytest.raises(ResourceUnavailable):
            access_token_for(connection)

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION

    def test_a_connection_without_a_credential_is_missing_not_broken(
        self, signed_in_client, make_project
    ):
        _client, _user, workspace = signed_in_client
        project = make_project(workspace)
        connection = IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.AWAITING_RESOURCE_SELECTION,
        )

        with pytest.raises(CredentialMissing):
            access_token_for(connection)

    @responses.activate
    def test_a_rejected_token_during_verification_requires_reauthorization(
        self, connected_project
    ):
        client, _user, project, connection = connected_project
        stub_property(status=401)

        response = client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "credential_refresh_failed"
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.REAUTH_REQUIRED


# --- Stored state, audit, and leakage ---------------------------------------


class TestStoredState:
    @responses.activate
    def test_metadata_is_minimal_and_carries_no_timestamp(self, connected_project):
        client, _user, project, connection = connected_project
        stub_property()

        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        connection.refresh_from_db()
        assert connection.external_resource_meta == {
            "account": "accounts/1",
            "property_type": "PROPERTY_TYPE_ORDINARY",
        }

    @responses.activate
    def test_selection_does_not_reassign_connected_by(self, connected_project):
        """connected_by records who authorized. Selecting is not authorizing."""
        client, user, project, connection = connected_project
        other = type(user).objects.create_user(
            email="colleague@example.com", password=PASSWORD
        )
        Membership.objects.create(
            workspace=project.workspace, user=other, role=Membership.Role.MEMBER
        )
        connection.connected_by = other
        connection.save(update_fields=["connected_by"])
        stub_property()

        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        connection.refresh_from_db()
        assert connection.connected_by == other

    @responses.activate
    def test_one_audit_event_records_the_whole_transition(self, connected_project):
        client, user, project, _connection = connected_project
        stub_property(display_name="Google's own name")

        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        events = list(AuditEvent.objects.filter(project=project))
        assert len(events) == 1
        event = events[0]
        assert event.action == AuditEvent.Action.INTEGRATION_RESOURCE_SELECTED
        assert event.actor == user
        assert event.workspace == project.workspace
        assert event.metadata == {
            "provider": "ga4",
            "resource_id": "properties/111",
            "resource_label": "Google's own name",
            "status": "connected",
            "previous_status": "awaiting_resource_selection",
        }

    @responses.activate
    def test_a_rejected_selection_writes_no_audit_event(self, connected_project):
        client, _user, project, _connection = connected_project
        stub_property(status=403)

        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        assert not AuditEvent.objects.filter(project=project).exists()


class TestNoLeakage:
    @responses.activate
    def test_no_credential_material_in_a_successful_response(self, connected_project):
        client, _user, project, _connection = connected_project
        stub_property()

        response = client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        body = json.dumps(response.data)
        assert "access-token-1" not in body
        assert "refresh-token-1" not in body
        assert "test-client-secret" not in body
        assert "external_resource_meta" not in body
        assert "granted_scopes" not in body

    @responses.activate
    def test_googles_error_text_never_reaches_the_response_or_the_log(
        self, connected_project, caplog
    ):
        client, _user, project, _connection = connected_project
        stub_property(status=403)

        with caplog.at_level("DEBUG"):
            response = client.post(
                selection_url(project.pk),
                {"resource_id": "properties/111"},
                format="json",
            )

        leak = "some Google detail that must not leak"
        assert leak not in json.dumps(response.data)
        assert leak not in caplog.text

    @responses.activate
    def test_the_access_token_is_never_logged(self, connected_project, caplog):
        client, _user, project, _connection = connected_project
        stub_property()

        with caplog.at_level("DEBUG"):
            client.post(
                selection_url(project.pk),
                {"resource_id": "properties/111"},
                format="json",
            )

        assert "access-token-1" not in caplog.text


class TestConnectedComesOnlyFromVerification:
    """`connected` is a claim about a live check, so only a live check may set it.

    Discovery is a convenience for the human choosing; it is not evidence about
    any particular property. These tests hold the line at the three places it
    could erode: discovery writing state, persistence running before
    verification, and persistence copying from the discovery payload.
    """

    @responses.activate
    def test_successful_discovery_never_connects_anything(self, connected_project):
        client, _user, project, connection = connected_project
        stub_summaries(
            summaries_body(
                accounts=[account(properties=[summary("properties/111", "acme")])]
            )
        )

        response = client.get(resources_url(project.pk))

        assert response.status_code == 200
        assert len(response.data["resources"]) == 1
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert connection.external_resource_id == ""
        assert connection.external_resource_label == ""
        assert connection.external_resource_meta == {}
        assert connection.last_health_check_at is None
        assert connection.last_successful_check_at is None

    @responses.activate
    def test_selection_verifies_the_exact_property_before_persisting(
        self, connected_project
    ):
        client, _user, project, connection = connected_project
        stub_property(property_id="properties/111")

        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        # Exactly one outbound call, and it is properties.get on the chosen id.
        assert len(responses.calls) == 1
        assert responses.calls[0].request.url == f"{ADMIN_BASE}/properties/111"
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.CONNECTED

    @responses.activate
    def test_nothing_persists_when_verification_never_succeeds(
        self, connected_project
    ):
        """No verified response, no connected state — whatever else happened."""
        client, _user, project, connection = connected_project
        stub_summaries(
            summaries_body(
                accounts=[account(properties=[summary("properties/111", "acme")])]
            )
        )
        client.get(resources_url(project.pk))  # the property was listed...
        responses.reset()
        stub_property(property_id="properties/111", status=403)  # ...but not readable

        response = client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        assert response.status_code == 400
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert connection.external_resource_id == ""
        assert connection.external_resource_meta == {}

    @responses.activate
    def test_stored_values_come_from_verification_not_from_discovery(
        self, connected_project
    ):
        """The discovery payload disagrees with the verification response.

        Every stored field must follow the verification response. Discovery is
        a list the user browsed; only the properties.get result is evidence
        about the property that was actually chosen.
        """
        client, _user, project, connection = connected_project
        stub_summaries(
            summaries_body(
                accounts=[
                    account(
                        display_name="Discovery Account",
                        account_id="accounts/999",
                        properties=[
                            {
                                "property": "properties/111",
                                "displayName": "Discovery label",
                                "propertyType": "PROPERTY_TYPE_SUBPROPERTY",
                            }
                        ],
                    )
                ]
            )
        )
        listed = client.get(resources_url(project.pk))
        assert listed.data["resources"][0]["label"] == "Discovery label"

        responses.reset()
        stub_property(
            property_id="properties/111",
            display_name="Verified label",
            parent="accounts/1",
        )

        client.post(
            selection_url(project.pk), {"resource_id": "properties/111"}, format="json"
        )

        connection.refresh_from_db()
        assert connection.external_resource_label == "Verified label"
        assert connection.external_resource_meta == {
            "account": "accounts/1",
            "property_type": "PROPERTY_TYPE_ORDINARY",
        }
        # None of the discovery payload's disagreeing values survived.
        assert "Discovery label" not in str(connection.external_resource_label)
        assert "accounts/999" not in str(connection.external_resource_meta)
        assert "SUBPROPERTY" not in str(connection.external_resource_meta)


class TestRefreshFence:
    """A refresh result may only be applied to the credential it came from.

    access_token_for is itself an outbound, state-mutating operation: it writes
    on success and calls mark_reauth_required on invalid_grant. Both writes
    happen before any caller could capture its own fence, so the refresh needs
    optimistic concurrency of its own (design §9.3.1).
    """

    def _expire(self, connection):
        credential = connection.credential
        credential.access_token_expires_at = timezone.now() - timedelta(minutes=5)
        credential.save(update_fields=["access_token_expires_at"])
        return credential

    def _reconnect(self, connection):
        """Simulate a reconnect committing while a refresh is in flight."""
        credential = connection.credential
        credential.access_token = "access-token-from-reconnect"
        credential.refresh_token = "refresh-token-from-reconnect"
        credential.access_token_expires_at = timezone.now() + timedelta(hours=1)
        credential.save()

    @responses.activate
    def test_stale_invalid_grant_does_not_mark_reauth_required(
        self, connected_project
    ):
        """Race A1. The verdict belongs to a token that no longer exists."""
        _client, _user, _project, connection = connected_project
        self._expire(connection)

        def reconnect_then_reject(request):
            self._reconnect(connection)
            return (400, {}, json.dumps({"error": "invalid_grant"}))

        responses.add_callback(
            responses.POST, TOKEN_URI, callback=reconnect_then_reject,
            content_type="application/json",
        )

        before = connection.status
        token = access_token_for(connection)

        assert token == "access-token-from-reconnect"
        connection.refresh_from_db()
        # Not knocked down to reauth_required by a verdict about a token that
        # no longer exists.
        assert connection.status == before
        assert connection.last_error_code == ""

    @responses.activate
    def test_stale_successful_refresh_does_not_overwrite_newer_credential(
        self, connected_project
    ):
        """Race A2 — the dangerous one, because it looks like success."""
        _client, _user, _project, connection = connected_project
        self._expire(connection)

        def reconnect_then_succeed(request):
            self._reconnect(connection)
            return (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "access-token-from-stale-refresh",
                        "expires_in": 3599,
                        "token_type": "Bearer",
                    }
                ),
            )

        responses.add_callback(
            responses.POST, TOKEN_URI, callback=reconnect_then_succeed,
            content_type="application/json",
        )

        token = access_token_for(connection)

        credential = IntegrationCredential.objects.get(connection=connection)
        assert token == "access-token-from-reconnect"
        assert credential.access_token == "access-token-from-reconnect"
        assert credential.refresh_token == "refresh-token-from-reconnect"

    @responses.activate
    def test_refresh_retry_returns_a_usable_current_token_without_a_second_call(
        self, connected_project
    ):
        _client, _user, _project, connection = connected_project
        self._expire(connection)

        def reconnect_then_succeed(request):
            self._reconnect(connection)
            return (
                200,
                {},
                json.dumps(
                    {"access_token": "stale", "expires_in": 3599, "token_type": "Bearer"}
                ),
            )

        responses.add_callback(
            responses.POST, TOKEN_URI, callback=reconnect_then_succeed,
            content_type="application/json",
        )

        access_token_for(connection)

        # The re-read token was usable, so no second refresh was needed.
        assert len(responses.calls) == 1

    @responses.activate
    def test_refresh_superseded_twice_raises_resource_unavailable(
        self, connected_project
    ):
        """State churning twice is transient, not proof anything is broken."""
        _client, _user, _project, connection = connected_project
        self._expire(connection)

        def supersede_but_leave_expired(request):
            credential = connection.credential
            credential.access_token = "still-expired"
            credential.access_token_expires_at = timezone.now() - timedelta(minutes=5)
            credential.save()
            return (
                200,
                {},
                json.dumps(
                    {"access_token": "stale", "expires_in": 3599, "token_type": "Bearer"}
                ),
            )

        responses.add_callback(
            responses.POST, TOKEN_URI, callback=supersede_but_leave_expired,
            content_type="application/json",
        )

        before = connection.status
        with pytest.raises(ResourceUnavailable):
            access_token_for(connection)

        connection.refresh_from_db()
        # Transient: nothing is proven broken, so no durable state changes.
        assert connection.status == before

    @responses.activate
    def test_refresh_deleted_credential_is_a_fence_mismatch_not_a_crash(
        self, connected_project
    ):
        """A disconnect deletes the row while the refresh is in flight."""
        _client, _user, _project, connection = connected_project
        self._expire(connection)

        def disconnect_then_succeed(request):
            IntegrationCredential.objects.filter(connection=connection).delete()
            return (
                200,
                {},
                json.dumps(
                    {"access_token": "stale", "expires_in": 3599, "token_type": "Bearer"}
                ),
            )

        responses.add_callback(
            responses.POST, TOKEN_URI, callback=disconnect_then_succeed,
            content_type="application/json",
        )

        with pytest.raises((ResourceUnavailable, CredentialMissing)):
            access_token_for(connection)

        assert not IntegrationCredential.objects.filter(connection=connection).exists()
