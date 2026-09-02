"""The Google OAuth flow: authorization start, callback, and their security.

No test contacts Google. The token endpoint is stubbed with `responses`, and
the authorization endpoint is never called — only the URL we build for it is
inspected.
"""

from __future__ import annotations

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
import responses
from django.utils import timezone

from audit.models import AuditEvent
from integrations.google.oauth import TOKEN_URI
from integrations.models import (
    IntegrationConnection,
    IntegrationCredential,
    OAuthAuthorizationRequest,
)
from integrations.oauth_service import hash_state
from integrations.providers import ProviderKey
from integrations.status import ConnectionStatus
from projects.models import Project
from tests.conftest import PASSWORD
from workspaces.models import Membership
from workspaces.services import create_initial_workspace

pytestmark = pytest.mark.django_db

GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
CALLBACK = "/api/integrations/oauth/google/callback"


@pytest.fixture(autouse=True)
def google_client(settings):
    settings.GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    settings.GOOGLE_CLIENT_SECRET = "test-client-secret"
    settings.GOOGLE_OAUTH_REDIRECT_URI = (
        "http://localhost:3000/api/integrations/oauth/google/callback"
    )
    return settings


def authorize_url(project_id, provider="ga4") -> str:
    return f"/api/projects/{project_id}/integrations/{provider}/authorize"


def stub_token(*, scope=GA4_SCOPE, refresh_token="refresh-token-1", status=200):
    body = {
        "access_token": "access-token-1",
        "expires_in": 3599,
        "scope": scope,
        "token_type": "Bearer",
    }
    if refresh_token is not None:
        body["refresh_token"] = refresh_token
    if status != 200:
        body = {"error": "invalid_grant", "error_description": "Bad code"}
    responses.add(responses.POST, TOKEN_URI, json=body, status=status)


def start_flow(client, project, provider="ga4") -> str:
    """Run the authorize endpoint and return the plaintext state."""
    response = client.get(authorize_url(project.id, provider))
    assert response.status_code == 200, response.data
    url = response.data["authorization_url"]
    return parse_qs(urlparse(url).query)["state"][0]


class TestAuthorizationStart:
    def test_requires_login(self, csrf_client, make_user_with_workspace, make_project):
        _user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        assert csrf_client.get(authorize_url(project.id)).status_code == 403

    def test_foreign_project_returns_404(self, signed_in_client, make_user):
        client, _user, _workspace = signed_in_client
        stranger = make_user(email="stranger@example.com")
        other = Project.objects.create(
            workspace=create_initial_workspace(stranger),
            name="Theirs",
            website_url="https://theirs.example",
        )
        assert client.get(authorize_url(other.id)).status_code == 404
        assert not OAuthAuthorizationRequest.objects.exists()

    def test_unknown_provider_returns_404(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        assert client.get(authorize_url(project.id, "google_ads")).status_code == 404

    def test_returns_a_google_consent_url_with_the_expected_parameters(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        response = client.get(authorize_url(project.id))
        assert response.status_code == 200
        url = response.data["authorization_url"]
        assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")

        params = parse_qs(urlparse(url).query)
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["test-client-id.apps.googleusercontent.com"]
        assert params["redirect_uri"] == [
            "http://localhost:3000/api/integrations/oauth/google/callback"
        ]
        assert params["access_type"] == ["offline"]
        assert params["include_granted_scopes"] == ["true"]
        assert params["state"]
        # Not forced on a normal first authorization.
        assert "prompt" not in params

    def test_requests_only_the_providers_own_scope(self, signed_in_client, make_project):
        """Each provider is independently authorizable."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        ga4 = parse_qs(urlparse(client.get(authorize_url(project.id, "ga4")).data["authorization_url"]).query)
        gsc = parse_qs(urlparse(client.get(authorize_url(project.id, "search_console")).data["authorization_url"]).query)

        assert ga4["scope"] == [GA4_SCOPE]
        assert gsc["scope"] == [GSC_SCOPE]
        assert GSC_SCOPE not in ga4["scope"][0]
        assert GA4_SCOPE not in gsc["scope"][0]

    def test_scopes_are_read_only(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        for provider in ("ga4", "search_console"):
            url = client.get(authorize_url(project.id, provider)).data["authorization_url"]
            scope = parse_qs(urlparse(url).query)["scope"][0]
            assert scope.endswith(".readonly")

    def test_state_is_high_entropy_and_only_its_hash_is_stored(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        assert len(state) >= 40  # 32 random bytes, urlsafe-base64

        request = OAuthAuthorizationRequest.objects.get()
        assert request.state_hash == hash_state(state)
        assert state not in request.state_hash
        # The plaintext state is nowhere in the table.
        assert not OAuthAuthorizationRequest.objects.filter(state_hash=state).exists()

    def test_two_starts_produce_different_states(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        assert start_flow(client, project) != start_flow(client, project)

    def test_request_is_bound_to_user_project_and_provider(
        self, signed_in_client, make_project
    ):
        client, user, workspace = signed_in_client
        project = make_project(workspace)
        start_flow(client, project, "search_console")

        request = OAuthAuthorizationRequest.objects.get()
        assert request.user == user
        assert request.project == project
        assert request.provider == ProviderKey.SEARCH_CONSOLE

    def test_request_expires(self, signed_in_client, make_project, settings):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        start_flow(client, project)

        request = OAuthAuthorizationRequest.objects.get()
        delta = request.expires_at - request.created_at
        assert delta <= timedelta(seconds=settings.OAUTH_STATE_TTL_SECONDS + 5)
        assert not request.is_expired()

    def test_connection_enters_pending_authorization(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        start_flow(client, project)

        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.PENDING_AUTHORIZATION
        assert not IntegrationCredential.objects.exists()

    def test_pkce_challenge_is_sent_and_the_verifier_is_stored(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        url = client.get(authorize_url(project.id)).data["authorization_url"]

        params = parse_qs(urlparse(url).query)
        assert params["code_challenge_method"] == ["S256"]
        assert params["code_challenge"]

        request = OAuthAuthorizationRequest.objects.get()
        assert request.code_verifier
        # The verifier is not the challenge.
        assert request.code_verifier != params["code_challenge"][0]

    def test_audit_event_recorded_with_no_secrets(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        event = AuditEvent.objects.get(action=AuditEvent.Action.INTEGRATION_AUTHORIZATION_STARTED)
        assert event.project == project
        assert event.workspace == workspace
        serialized = json.dumps(event.metadata)
        assert state not in serialized
        assert set(event.metadata) <= {"provider"}


class TestCallbackSuccess:
    @responses.activate
    def test_valid_callback_reaches_awaiting_resource_selection(
        self, signed_in_client, make_project
    ):
        client, user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()

        response = client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        assert response.status_code == 302
        assert response["Location"].endswith(
            f"/projects/{project.id}/integrations?provider=ga4&authorized=1"
        )

        connection = IntegrationConnection.objects.get()
        # OAuth success is NOT "connected": that needs a verified resource.
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert connection.granted_scopes == [GA4_SCOPE]
        assert connection.connected_by == user

    @responses.activate
    def test_tokens_are_stored_and_expiry_is_persisted(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()

        before = timezone.now()
        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        credential = IntegrationCredential.objects.get()
        assert credential.access_token == "access-token-1"
        assert credential.refresh_token == "refresh-token-1"
        assert credential.access_token_expires_at is not None
        assert timezone.is_aware(credential.access_token_expires_at)
        # expires_in was 3599 seconds.
        delta = credential.access_token_expires_at - before
        assert timedelta(minutes=50) < delta < timedelta(minutes=70)

    @responses.activate
    def test_authorization_code_is_never_persisted(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "super-secret-auth-code"})

        from django.db import connection as db

        with db.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM integrations_integrationcredential; "
            )
            rows = str(cursor.fetchall())
        assert "super-secret-auth-code" not in rows

    @responses.activate
    def test_callback_response_contains_no_credential_material(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()

        response = client.get(CALLBACK, {"state": state, "code": "auth-code-1"})
        body = response.content.decode()
        location = response["Location"]
        for secret in ("access-token-1", "refresh-token-1", "auth-code-1", state):
            assert secret not in body
            assert secret not in location

    @responses.activate
    def test_each_provider_authorizes_independently(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project, "ga4")
        stub_token(scope=GA4_SCOPE)
        client.get(CALLBACK, {"state": state, "code": "c1"})

        ga4 = IntegrationConnection.objects.get(provider=ProviderKey.GA4)
        assert ga4.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        # Search Console has no row at all: it was never authorized.
        assert not IntegrationConnection.objects.filter(
            provider=ProviderKey.SEARCH_CONSOLE
        ).exists()

    @responses.activate
    def test_audit_event_on_success_carries_no_secrets(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "auth-code-1"})

        event = AuditEvent.objects.get(action=AuditEvent.Action.INTEGRATION_AUTHORIZED)
        assert event.workspace == workspace
        assert event.project == project
        serialized = json.dumps(event.metadata)
        for secret in ("access-token-1", "refresh-token-1", "auth-code-1", state):
            assert secret not in serialized


class TestCallbackStateSecurity:
    """Every rejection must look identical: no oracle for another tenant."""

    @pytest.mark.parametrize(
        "params",
        [
            {},
            {"code": "c"},
            {"state": "", "code": "c"},
            {"state": "not-a-real-state", "code": "c"},
            {"state": "!!!malformed!!!", "code": "c"},
        ],
        ids=["nothing", "no-state", "empty-state", "unknown-state", "malformed-state"],
    )
    def test_bad_state_is_rejected(self, signed_in_client, params):
        client, _user, _workspace = signed_in_client
        response = client.get(CALLBACK, params)
        assert response.status_code == 302
        assert "oauth_error=invalid_state" in response["Location"]
        assert not IntegrationCredential.objects.exists()

    @responses.activate
    def test_expired_state_is_rejected(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        request = OAuthAuthorizationRequest.objects.get()
        request.expires_at = timezone.now() - timedelta(seconds=1)
        request.save()

        stub_token()
        response = client.get(CALLBACK, {"state": state, "code": "c"})
        assert "oauth_error=invalid_state" in response["Location"]
        assert not IntegrationCredential.objects.exists()

    @responses.activate
    def test_state_is_single_use(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()

        first = client.get(CALLBACK, {"state": state, "code": "c"})
        assert "authorized=1" in first["Location"]

        replay = client.get(CALLBACK, {"state": state, "code": "c"})
        assert "oauth_error=invalid_state" in replay["Location"]

    @responses.activate
    def test_replay_does_not_disturb_the_established_connection(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "c"})

        client.get(CALLBACK, {"state": state, "code": "c"})

        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert IntegrationCredential.objects.count() == 1

    def test_another_users_state_is_rejected(
        self, signed_in_client, csrf_client, make_user, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        # A second user, signed in on their own client, presents the state.
        other = make_user(email="other@example.com")
        create_initial_workspace(other)
        csrf_client.get("/api/auth/csrf")
        csrf_client.credentials(HTTP_X_CSRFTOKEN=csrf_client.cookies["pi_csrftoken"].value)
        csrf_client.post(
            "/api/auth/login", {"email": other.email, "password": PASSWORD}, format="json"
        )

        response = csrf_client.get(CALLBACK, {"state": state, "code": "c"})
        assert "oauth_error=invalid_state" in response["Location"]
        assert not IntegrationCredential.objects.exists()
        # Not consumed by the stranger, so the rightful owner can still finish.
        assert not OAuthAuthorizationRequest.objects.get().is_consumed

    @responses.activate
    def test_membership_removed_after_the_flow_started(self, signed_in_client, make_project):
        """Authorized at start does not mean authorized at callback."""
        client, user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        Membership.objects.filter(user=user, workspace=workspace).delete()

        stub_token()
        response = client.get(CALLBACK, {"state": state, "code": "c"})
        assert "oauth_error=invalid_state" in response["Location"]
        assert not IntegrationCredential.objects.exists()

    def test_unauthenticated_callback_is_rejected(self, csrf_client):
        response = csrf_client.get(CALLBACK, {"state": "x", "code": "c"})
        assert response.status_code == 403

    def test_failure_redirect_does_not_disclose_the_project(self, signed_in_client):
        client, _user, _workspace = signed_in_client
        response = client.get(CALLBACK, {"state": "unknown", "code": "c"})
        assert "/projects?" in response["Location"]
        assert "/integrations" not in response["Location"]


class TestCallbackFailures:
    def test_user_denies_consent(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        response = client.get(CALLBACK, {"state": state, "error": "access_denied"})

        assert "oauth_error=access_denied" in response["Location"]
        # Nothing was authorized, so the provider goes back to "not connected"
        # rather than sitting in a misleading error state.
        assert not IntegrationConnection.objects.exists()
        assert not IntegrationCredential.objects.exists()

    def test_denial_consumes_the_request_so_it_cannot_be_replayed(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        client.get(CALLBACK, {"state": state, "error": "access_denied"})
        assert OAuthAuthorizationRequest.objects.get().is_consumed

        replay = client.get(CALLBACK, {"state": state, "code": "c"})
        assert "oauth_error=invalid_state" in replay["Location"]

    @responses.activate
    def test_token_exchange_error_is_mapped_not_leaked(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token(status=400)

        response = client.get(CALLBACK, {"state": state, "code": "bad-code"})

        assert "oauth_error=token_exchange_failed" in response["Location"]
        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.ERROR
        assert connection.last_error_code == "token_exchange_failed"
        assert not IntegrationCredential.objects.exists()

    @responses.activate
    def test_required_scope_not_granted(self, signed_in_client, make_project):
        """Granular consent: the user unticked the permission."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        # Google returns a token, but not for the scope we need.
        stub_token(scope="https://www.googleapis.com/auth/userinfo.email")

        response = client.get(CALLBACK, {"state": state, "code": "c"})

        assert "oauth_error=scope_not_granted" in response["Location"]
        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.ERROR
        assert connection.last_error_code == "scope_not_granted"
        # No misleading progress, and no credential stored.
        assert connection.status != ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert not IntegrationCredential.objects.exists()

    @responses.activate
    def test_extra_granted_scopes_are_accepted(self, signed_in_client, make_project):
        """include_granted_scopes can return more than we asked for."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token(scope=f"{GA4_SCOPE} {GSC_SCOPE}")

        client.get(CALLBACK, {"state": state, "code": "c"})
        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION

    @responses.activate
    def test_error_state_clears_on_a_later_success(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token(status=400)
        client.get(CALLBACK, {"state": state, "code": "c"})
        assert IntegrationConnection.objects.get().last_error_code == "token_exchange_failed"

        responses.reset()
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "c"})

        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert connection.last_error_code == ""
        assert connection.last_error_message == ""


class TestRefreshTokenRules:
    """A response without a refresh token must never blank a stored one."""

    @responses.activate
    def test_new_refresh_token_is_stored(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token(refresh_token="refresh-A")

        client.get(CALLBACK, {"state": state, "code": "c"})
        assert IntegrationCredential.objects.get().refresh_token == "refresh-A"

    @responses.activate
    def test_absent_refresh_token_preserves_the_stored_one(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token(refresh_token="refresh-A")
        client.get(CALLBACK, {"state": state, "code": "c"})

        # Re-authorize; Google omits refresh_token, which is normal.
        responses.reset()
        state = start_flow(client, project)
        stub_token(refresh_token=None)
        client.get(CALLBACK, {"state": state, "code": "c"})

        credential = IntegrationCredential.objects.get()
        assert credential.refresh_token == "refresh-A"
        assert credential.access_token == "access-token-1"
        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.AWAITING_RESOURCE_SELECTION
        )

    @responses.activate
    def test_empty_string_refresh_token_also_preserves(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token(refresh_token="refresh-A")
        client.get(CALLBACK, {"state": state, "code": "c"})

        responses.reset()
        state = start_flow(client, project)
        stub_token(refresh_token="")
        client.get(CALLBACK, {"state": state, "code": "c"})

        assert IntegrationCredential.objects.get().refresh_token == "refresh-A"

    @responses.activate
    def test_no_refresh_token_and_none_stored_is_surfaced(
        self, signed_in_client, make_project
    ):
        """Do not silently claim durable authorization."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token(refresh_token=None)

        response = client.get(CALLBACK, {"state": state, "code": "c"})

        assert "oauth_error=no_refresh_token" in response["Location"]
        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.ERROR
        assert connection.last_error_code == "no_refresh_token"
        assert connection.status != ConnectionStatus.AWAITING_RESOURCE_SELECTION

    @responses.activate
    def test_reauthorization_after_missing_refresh_token_forces_consent(
        self, signed_in_client, make_project
    ):
        """prompt=consent only when a refresh token must be reacquired."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token(refresh_token=None)
        client.get(CALLBACK, {"state": state, "code": "c"})

        connection = IntegrationConnection.objects.get()
        connection.status = ConnectionStatus.REAUTH_REQUIRED
        connection.save()

        url = client.get(authorize_url(project.id)).data["authorization_url"]
        assert parse_qs(urlparse(url).query)["prompt"] == ["consent"]
