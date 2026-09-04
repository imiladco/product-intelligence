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
from rest_framework.test import APIClient

from audit.models import AuditEvent
from integrations.google.errors import NoRefreshToken
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


def authorize(client, project, provider="ga4"):
    """POST the authorize endpoint (starting a flow has side effects)."""
    return client.post(authorize_url(project.id, provider), {}, format="json")


def start_flow(client, project, provider="ga4") -> str:
    """Run the authorize endpoint and return the plaintext state."""
    response = authorize(client, project, provider)
    assert response.status_code == 200, response.data
    url = response.data["authorization_url"]
    return parse_qs(urlparse(url).query)["state"][0]


class TestAuthorizationStart:
    def test_requires_login(self, csrf_client, make_user_with_workspace, make_project):
        _user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        response = csrf_client.post(authorize_url(project.id), {}, format="json")
        assert response.status_code == 403

    def test_foreign_project_returns_404(self, signed_in_client, make_user):
        client, _user, _workspace = signed_in_client
        stranger = make_user(email="stranger@example.com")
        other = Project.objects.create(
            workspace=create_initial_workspace(stranger),
            name="Theirs",
            website_url="https://theirs.example",
        )
        assert client.post(authorize_url(other.id), {}, format="json").status_code == 404
        assert not OAuthAuthorizationRequest.objects.exists()

    def test_unknown_provider_returns_404(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        assert authorize(client, project, "google_ads").status_code == 404

    def test_returns_a_google_consent_url_with_the_expected_parameters(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        response = authorize(client, project)
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
        # Forced even on a first authorization (design §5.3.2, changed in M6).
        # A new connection row proves only that this project has not connected
        # this provider; the same Google account may already have authorized
        # this application elsewhere, in which case Google can return no
        # refresh token and the first connection fails. Consent is shown for new
        # scopes anyway, so this costs the user nothing.
        # Full table: TestForcedConsentIsKeyedOnCapability.
        assert params["prompt"] == ["consent"]

    def test_requests_only_the_providers_own_scope(self, signed_in_client, make_project):
        """Each provider is independently authorizable."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        ga4 = parse_qs(urlparse(authorize(client, project, "ga4").data["authorization_url"]).query)
        gsc = parse_qs(urlparse(authorize(client, project, "search_console").data["authorization_url"]).query)

        assert ga4["scope"] == [GA4_SCOPE]
        assert gsc["scope"] == [GSC_SCOPE]
        assert GSC_SCOPE not in ga4["scope"][0]
        assert GA4_SCOPE not in gsc["scope"][0]

    def test_scopes_are_read_only(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        for provider in ("ga4", "search_console"):
            url = authorize(client, project, provider).data["authorization_url"]
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
        url = authorize(client, project).data["authorization_url"]

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

        url = authorize(client, project).data["authorization_url"]
        assert parse_qs(urlparse(url).query)["prompt"] == ["consent"]


class TestAuthorizeIsAStateChangingPost:
    """Starting an authorization has side effects, so it must not be a GET.

    It creates a connection row on first use, creates a single-use
    authorization request, and writes an audit event. A state-changing GET
    would be triggerable by any cross-site navigation and would bypass CSRF.
    """

    def test_get_is_rejected(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        response = client.get(authorize_url(project.id))

        assert response.status_code == 405
        assert not IntegrationConnection.objects.exists()
        assert not OAuthAuthorizationRequest.objects.exists()

    def test_unauthenticated_post_is_rejected(
        self, csrf_client, make_user_with_workspace, make_project
    ):
        _user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        response = csrf_client.post(authorize_url(project.id), {}, format="json")

        assert response.status_code == 403
        assert not IntegrationConnection.objects.exists()
        assert not OAuthAuthorizationRequest.objects.exists()

    def test_post_without_csrf_token_is_rejected(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        client.credentials()  # drop the X-CSRFToken header

        response = client.post(authorize_url(project.id), {}, format="json")

        assert response.status_code == 403
        assert "CSRF" in str(response.data)

    def test_csrf_failure_leaves_no_trace(self, signed_in_client, make_project):
        """A rejected request must not have started anything."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        client.credentials()

        client.post(authorize_url(project.id), {}, format="json")

        assert not IntegrationConnection.objects.exists()
        assert not OAuthAuthorizationRequest.objects.exists()
        assert not AuditEvent.objects.exists()

    def test_post_with_csrf_token_succeeds(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        response = client.post(authorize_url(project.id), {}, format="json")

        assert response.status_code == 200
        assert response.data["authorization_url"].startswith(
            "https://accounts.google.com/o/oauth2/v2/auth"
        )
        assert OAuthAuthorizationRequest.objects.count() == 1

    def test_foreign_project_post_returns_404(self, signed_in_client, make_user):
        client, _user, _workspace = signed_in_client
        stranger = make_user(email="outsider@example.com")
        other = Project.objects.create(
            workspace=create_initial_workspace(stranger),
            name="Theirs",
            website_url="https://theirs.example",
        )

        response = client.post(authorize_url(other.id), {}, format="json")

        assert response.status_code == 404
        assert not OAuthAuthorizationRequest.objects.exists()
        assert not AuditEvent.objects.exists()


class TestNoEmptyCredentialRow:
    """IntegrationCredential means "we hold credential material".

    It is never a marker that an authorization was attempted, so a failed
    authorization must leave no row at all.
    """

    @responses.activate
    def test_first_authorization_without_refresh_token_stores_nothing(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token(refresh_token=None)

        response = client.get(CALLBACK, {"state": state, "code": "c"})

        assert "oauth_error=no_refresh_token" in response["Location"]
        assert IntegrationCredential.objects.count() == 0
        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.ERROR
        assert connection.last_error_code == "no_refresh_token"

    @responses.activate
    def test_repeated_failures_still_store_nothing(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        for _ in range(3):
            responses.reset()
            state = start_flow(client, project)
            stub_token(refresh_token=None)
            client.get(CALLBACK, {"state": state, "code": "c"})

        assert IntegrationCredential.objects.count() == 0

    @responses.activate
    def test_successful_first_authorization_still_stores_the_credential(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token(refresh_token="refresh-first")

        client.get(CALLBACK, {"state": state, "code": "c"})

        credential = IntegrationCredential.objects.get()
        assert credential.refresh_token == "refresh-first"
        assert credential.access_token == "access-token-1"

    @responses.activate
    def test_retry_after_a_failure_forces_consent_from_connection_state(
        self, signed_in_client, make_project
    ):
        """Re-consent is decided by connection state, not an empty credential row."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token(refresh_token=None)
        client.get(CALLBACK, {"state": state, "code": "c"})
        assert IntegrationCredential.objects.count() == 0

        url = authorize(client, project).data["authorization_url"]
        assert parse_qs(urlparse(url).query)["prompt"] == ["consent"]

    @responses.activate
    def test_that_retry_can_then_succeed(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token(refresh_token=None)
        client.get(CALLBACK, {"state": state, "code": "c"})

        responses.reset()
        state = start_flow(client, project)
        stub_token(refresh_token="refresh-after-consent")
        client.get(CALLBACK, {"state": state, "code": "c"})

        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert IntegrationCredential.objects.get().refresh_token == "refresh-after-consent"

    @responses.activate
    def test_existing_refresh_token_is_still_preserved(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token(refresh_token="refresh-original")
        client.get(CALLBACK, {"state": state, "code": "c"})

        responses.reset()
        state = start_flow(client, project)
        stub_token(refresh_token=None)
        client.get(CALLBACK, {"state": state, "code": "c"})

        assert IntegrationCredential.objects.count() == 1
        assert IntegrationCredential.objects.get().refresh_token == "refresh-original"


class TestStartingAuthorizationPreservesState:
    """An in-flight attempt is the OAuthAuthorizationRequest, not a status change.

    Overwriting the connection's durable status when a flow starts makes
    cancelling destructive: the user would be left in pending_authorization
    with no way back to where they were.
    """

    @responses.activate
    def _authorize_successfully(self, client, project):
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "c"})

    @responses.activate
    def test_first_authorization_creates_a_pending_row(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        start_flow(client, project)

        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.PENDING_AUTHORIZATION
        )

    @responses.activate
    def test_awaiting_resource_selection_is_not_reset_to_pending(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "c"})
        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.AWAITING_RESOURCE_SELECTION
        )

        # Start a second authorization: the durable status must survive.
        start_flow(client, project)

        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.AWAITING_RESOURCE_SELECTION
        )

    @responses.activate
    def test_cancelling_that_authorization_preserves_the_previous_state(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "c"})

        # Re-authorize, then deny at Google.
        state = start_flow(client, project)
        response = client.get(CALLBACK, {"state": state, "error": "access_denied"})

        assert "oauth_error=access_denied" in response["Location"]
        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert IntegrationCredential.objects.count() == 1

    @responses.activate
    def test_reauth_required_is_not_reset_to_pending(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "c"})

        connection = IntegrationConnection.objects.get()
        connection.status = ConnectionStatus.REAUTH_REQUIRED
        connection.save()

        start_flow(client, project)

        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.REAUTH_REQUIRED
        )

    @responses.activate
    def test_no_fake_connected_transition_exists(self, signed_in_client, make_project):
        """Nothing in the OAuth flow may produce `connected`."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "c"})

        assert not IntegrationConnection.objects.filter(
            status=ConnectionStatus.CONNECTED
        ).exists()


class TestRestartingAnAbandonedAuthorization:
    """An abandoned flow must not strand the user in pending_authorization.

    Nothing reaches the callback when a user closes the Google tab, goes back,
    or loses connectivity. The authorization request expires, but the
    connection row does not, so without a restart path the user waits forever.
    Restarting supersedes the outstanding request, which also stops a stale
    browser tab from completing an authorization the user has moved on from.
    """

    def outstanding(self, **filters):
        return OAuthAuthorizationRequest.objects.filter(
            consumed_at__isnull=True, **filters
        )

    def test_restart_creates_a_fresh_request_with_a_different_state(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        first_state = start_flow(client, project)
        second_state = start_flow(client, project)

        assert first_state != second_state
        assert OAuthAuthorizationRequest.objects.count() == 2
        # Exactly one is still usable.
        assert self.outstanding().count() == 1
        assert self.outstanding().get().state_hash == hash_state(second_state)

    def test_the_previous_request_is_marked_consumed_not_deleted(
        self, signed_in_client, make_project
    ):
        """History survives; only usability is revoked."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        first_state = start_flow(client, project)
        start_flow(client, project)

        superseded = OAuthAuthorizationRequest.objects.get(
            state_hash=hash_state(first_state)
        )
        assert superseded.is_consumed

    @responses.activate
    def test_callback_from_the_superseded_attempt_is_rejected(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        stale_state = start_flow(client, project)
        start_flow(client, project)
        stub_token()

        response = client.get(CALLBACK, {"state": stale_state, "code": "c"})

        assert "oauth_error=invalid_state" in response["Location"]
        assert not IntegrationCredential.objects.exists()
        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.PENDING_AUTHORIZATION
        )

    @responses.activate
    def test_callback_from_the_newest_attempt_still_succeeds(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        start_flow(client, project)
        current_state = start_flow(client, project)
        stub_token()

        response = client.get(CALLBACK, {"state": current_state, "code": "c"})

        assert "authorized=1" in response["Location"]
        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.AWAITING_RESOURCE_SELECTION
        )

    @responses.activate
    def test_the_race_case_a_then_b_then_a_then_b(self, signed_in_client, make_project):
        """A starts, B starts, A's callback must fail, B's may succeed.

        Guarantees a restarted flow cannot be overwritten later by an older tab.
        """
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state_a = start_flow(client, project)
        state_b = start_flow(client, project)
        stub_token()

        from_a = client.get(CALLBACK, {"state": state_a, "code": "code-a"})
        assert "oauth_error=invalid_state" in from_a["Location"]
        assert not IntegrationCredential.objects.exists()

        from_b = client.get(CALLBACK, {"state": state_b, "code": "code-b"})
        assert "authorized=1" in from_b["Location"]
        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.AWAITING_RESOURCE_SELECTION
        )

    def test_restart_creates_no_duplicate_connection_row(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        for _ in range(4):
            start_flow(client, project)

        assert IntegrationConnection.objects.count() == 1
        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.PENDING_AUTHORIZATION
        )

    def test_restart_does_not_touch_another_provider(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        gsc_state = start_flow(client, project, "search_console")
        start_flow(client, project, "ga4")
        start_flow(client, project, "ga4")

        assert self.outstanding(provider=ProviderKey.SEARCH_CONSOLE).count() == 1
        assert self.outstanding(provider=ProviderKey.SEARCH_CONSOLE).get().state_hash == (
            hash_state(gsc_state)
        )

    @responses.activate
    def test_another_providers_flow_still_completes(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        gsc_state = start_flow(client, project, "search_console")
        start_flow(client, project, "ga4")
        stub_token(scope=GSC_SCOPE)

        response = client.get(CALLBACK, {"state": gsc_state, "code": "c"})

        assert "authorized=1" in response["Location"]
        assert IntegrationConnection.objects.get(
            provider=ProviderKey.SEARCH_CONSOLE
        ).status == ConnectionStatus.AWAITING_RESOURCE_SELECTION

    def test_restart_does_not_touch_another_project(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        first = make_project(workspace, name="One", website_url="https://one.example")
        second = make_project(workspace, name="Two", website_url="https://two.example")

        second_state = start_flow(client, second)
        start_flow(client, first)
        start_flow(client, first)

        assert self.outstanding(project=second).count() == 1
        assert self.outstanding(project=second).get().state_hash == hash_state(second_state)

    def test_restart_does_not_touch_another_users_request(
        self, signed_in_client, make_user, make_project
    ):
        """Two members of the same workspace can have flows in parallel."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        # A genuinely separate client: signed_in_client is built from
        # csrf_client, so reusing that fixture would just replace the session
        # on the same client rather than giving a second signed-in user.
        other = make_user(email="colleague@example.com")
        Membership.objects.create(workspace=workspace, user=other)
        other_client = APIClient(enforce_csrf_checks=True)
        other_client.get("/api/auth/csrf")
        other_client.credentials(
            HTTP_X_CSRFTOKEN=other_client.cookies["pi_csrftoken"].value
        )
        other_client.post(
            "/api/auth/login", {"email": other.email, "password": PASSWORD}, format="json"
        )
        # Django rotates the CSRF token on login; send the current one.
        other_client.credentials(
            HTTP_X_CSRFTOKEN=other_client.cookies["pi_csrftoken"].value
        )
        others_state = start_flow(other_client, project)

        start_flow(client, project)

        assert self.outstanding(user=other).count() == 1
        assert self.outstanding(user=other).get().state_hash == hash_state(others_state)

    @responses.activate
    def test_a_normal_first_authorization_is_unchanged(
        self, signed_in_client, make_project
    ):
        """The ordinary single-attempt path still behaves exactly as before."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token()
        response = client.get(CALLBACK, {"state": state, "code": "c"})

        assert "authorized=1" in response["Location"]
        assert OAuthAuthorizationRequest.objects.count() == 1
        connection = IntegrationConnection.objects.get()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert IntegrationCredential.objects.get().refresh_token == "refresh-token-1"

    @responses.activate
    def test_restarting_after_a_successful_authorization_keeps_the_credential(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        state = start_flow(client, project)
        stub_token()
        client.get(CALLBACK, {"state": state, "code": "c"})

        start_flow(client, project)

        assert IntegrationCredential.objects.count() == 1
        assert IntegrationConnection.objects.get().status == (
            ConnectionStatus.AWAITING_RESOURCE_SELECTION
        )


class TestForcedConsentIsKeyedOnCapability:
    """prompt=consent whenever no stored refresh token can be preserved.

    Design §5.3.1-§5.3.2. The predicate M3 shipped assumed a new connection row
    meant a first authorization of that Google account for this application.
    It does not: the same account may already have authorized us through
    another project or workspace, in which case Google can return no
    refresh_token at all and the *first* connection fails on NoRefreshToken.

    This system deliberately holds no Google identity, so it cannot ask whether
    a prior grant exists. The question it can always answer is local: can this
    authorization preserve a refresh token we already hold? If not, it must
    guarantee acquiring one.
    """

    def _prompt(self, client, project, provider="ga4"):
        response = authorize(client, project, provider)
        assert response.status_code == 200, response.data
        params = parse_qs(urlparse(response.data["authorization_url"]).query)
        return params.get("prompt")

    def _connection(self, project, **kwargs):
        return IntegrationConnection.objects.create(
            project=project, provider=ProviderKey.GA4, **kwargs
        )

    def _credential(self, connection, refresh_token="refresh-token-1"):
        return IntegrationCredential.objects.create(
            connection=connection,
            access_token="access-token-1",
            refresh_token=refresh_token,
        )

    # --- consent IS forced ---------------------------------------------------

    def test_no_connection_row(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        assert self._prompt(client, project) == ["consent"]

    def test_connection_without_a_credential(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        self._connection(project, status=ConnectionStatus.PENDING_AUTHORIZATION)

        assert self._prompt(client, project) == ["consent"]

    def test_stored_refresh_token_is_empty(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        connection = self._connection(project, status=ConnectionStatus.CONNECTED)
        self._credential(connection, refresh_token="")

        assert self._prompt(client, project) == ["consent"]

    def test_disconnected(self, signed_in_client, make_project):
        """Our token is gone; Google's consent is not."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        self._connection(project, status=ConnectionStatus.DISCONNECTED)

        assert self._prompt(client, project) == ["consent"]

    def test_reauth_required(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        connection = self._connection(project, status=ConnectionStatus.REAUTH_REQUIRED)
        self._credential(connection)

        assert self._prompt(client, project) == ["consent"]

    def test_error_with_no_refresh_token(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        self._connection(
            project,
            status=ConnectionStatus.ERROR,
            last_error_code="no_refresh_token",
        )

        assert self._prompt(client, project) == ["consent"]

    def test_second_project_first_connection_still_forces_consent(
        self, signed_in_client, make_project
    ):
        """The case M3 could not see.

        A credential for the same provider already exists in another project —
        so the same Google account may well have authorized this application
        already — and this connection has none of its own. The predicate reads
        *this* connection's credential, not the database at large.
        """
        client, _user, workspace = signed_in_client
        other_project = make_project(workspace, name="Other", website_url="https://o.example")
        other_connection = IntegrationConnection.objects.create(
            project=other_project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.CONNECTED,
        )
        self._credential(other_connection)
        fresh_project = make_project(workspace, name="Fresh", website_url="https://f.example")

        assert self._prompt(client, fresh_project) == ["consent"]

    # --- consent is NOT forced ----------------------------------------------

    def test_connected_with_a_usable_refresh_token(self, signed_in_client, make_project):
        """A working token is preserved; re-consent would be noise."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        connection = self._connection(project, status=ConnectionStatus.CONNECTED)
        self._credential(connection)

        assert self._prompt(client, project) is None

    def test_awaiting_resource_selection_with_a_refresh_token(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        connection = self._connection(
            project, status=ConnectionStatus.AWAITING_RESOURCE_SELECTION
        )
        self._credential(connection)

        assert self._prompt(client, project) is None

    def test_error_with_another_code_and_an_intact_credential(
        self, signed_in_client, make_project
    ):
        """The credential is not the problem, so do not re-consent."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        connection = self._connection(
            project,
            status=ConnectionStatus.ERROR,
            last_error_code="scope_not_granted",
        )
        self._credential(connection)

        assert self._prompt(client, project) is None


class TestCallbackGenerationFence:
    """Stage 3 refuses to persist for a superseded authorization (§9.4.2).

    The callback consumes its request *before* the token exchange, so from that
    moment consume-on-disconnect can no longer see it. The generation is what
    covers the remaining window: at the instant of writing, is this attempt
    still the user's current intent?
    """

    @responses.activate
    def test_callback_proceeds_when_generation_matches(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()

        response = client.get(CALLBACK, {"state": state, "code": "auth-code"})

        assert response.status_code == 302
        assert "authorized=1" in response.url
        connection = IntegrationConnection.objects.get(project=project)
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert IntegrationCredential.objects.filter(connection=connection).exists()

    @responses.activate
    def test_callback_is_discarded_when_generation_advanced(
        self, signed_in_client, make_project
    ):
        """A newer intent landed while the user was at Google."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()

        connection = IntegrationConnection.objects.get(project=project)
        before = IntegrationConnection.objects.get(pk=connection.pk)
        # Something expressed a newer intent for this integration.
        connection.lifecycle_generation += 1
        connection.save(update_fields=["lifecycle_generation", "updated_at"])

        response = client.get(CALLBACK, {"state": state, "code": "auth-code"})

        assert response.status_code == 302
        assert "oauth_error=invalid_state" in response.url

        connection.refresh_from_db()
        assert not IntegrationCredential.objects.filter(connection=connection).exists()
        assert connection.status == before.status
        assert connection.granted_scopes == before.granted_scopes
        assert not AuditEvent.objects.filter(
            action__in=[
                AuditEvent.Action.INTEGRATION_AUTHORIZED,
                AuditEvent.Action.INTEGRATION_RECONNECTED,
            ]
        ).exists()

    @responses.activate
    def test_callback_does_not_recreate_a_deleted_connection(
        self, signed_in_client, make_project
    ):
        """Finalization takes the existing-only lock; it never creates."""
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token()

        IntegrationConnection.objects.filter(project=project).delete()

        response = client.get(CALLBACK, {"state": state, "code": "auth-code"})

        assert response.status_code == 302
        assert "oauth_error=invalid_state" in response.url
        assert not IntegrationConnection.objects.filter(project=project).exists()
        assert not IntegrationCredential.objects.exists()
        assert not AuditEvent.objects.filter(
            action__in=[
                AuditEvent.Action.INTEGRATION_AUTHORIZED,
                AuditEvent.Action.INTEGRATION_RECONNECTED,
            ]
        ).exists()

    @responses.activate
    def test_no_database_lock_is_held_across_the_token_exchange(
        self, signed_in_client, make_project
    ):
        """Stage 2 is a network call of unbounded duration.

        Holding a row lock across it would block every other operation on the
        integration for as long as Google takes. The stub writes to the same
        connection mid-exchange; if a lock were held, this would deadlock or
        block rather than complete.
        """
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        def write_during_exchange(request):
            IntegrationConnection.objects.filter(project=project).update(
                last_error_message="written during the exchange"
            )
            return (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "access-token-1",
                        "expires_in": 3599,
                        "refresh_token": "refresh-token-1",
                        "scope": GA4_SCOPE,
                        "token_type": "Bearer",
                    }
                ),
            )

        responses.add_callback(
            responses.POST, TOKEN_URI, callback=write_during_exchange,
            content_type="application/json",
        )

        response = client.get(CALLBACK, {"state": state, "code": "auth-code"})

        assert response.status_code == 302
        assert "authorized=1" in response.url


class TestCallbackFailurePathsAreGenerationFenced:
    """A superseded callback may not write failure state either (§9.4.2).

    Stage 3 fences the success path, but every failure path also mutates the
    connection — and each did so through the stale object read before the token
    exchange. A newer authorization that completed while R1 was at Google would
    be overwritten with R1's error, or in the denial case have its connection
    deleted outright.
    """

    def _advance_generation(self, project):
        connection = IntegrationConnection.objects.get(project=project)
        connection.lifecycle_generation += 1
        connection.save(update_fields=["lifecycle_generation", "updated_at"])
        return connection

    def _assert_untouched_and_no_failure_audit(self, project, before):
        connection = IntegrationConnection.objects.get(project=project)
        assert connection.status == before.status
        assert connection.last_error_code == ""
        assert connection.last_error_message == ""
        assert connection.lifecycle_generation == before.lifecycle_generation
        assert not AuditEvent.objects.filter(
            action=AuditEvent.Action.INTEGRATION_AUTHORIZATION_FAILED
        ).exists()

    @responses.activate
    def test_stale_token_exchange_failure_does_not_overwrite_newer_authorization(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        def newer_intent_then_fail(request):
            self._advance_generation(project)
            return (400, {}, json.dumps({"error": "invalid_grant"}))

        responses.add_callback(
            responses.POST, TOKEN_URI, callback=newer_intent_then_fail,
            content_type="application/json",
        )
        before = self._advance_generation(project)
        before.lifecycle_generation += 1  # the stub will advance it once more

        response = client.get(CALLBACK, {"state": state, "code": "auth-code"})

        assert response.status_code == 302
        assert "oauth_error=invalid_state" in response.url
        self._assert_untouched_and_no_failure_audit(project, before)

    @responses.activate
    def test_stale_scope_not_granted_does_not_overwrite_newer_authorization(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        def newer_intent_then_wrong_scope(request):
            self._advance_generation(project)
            return (
                200,
                {},
                json.dumps(
                    {
                        "access_token": "access-token-1",
                        "expires_in": 3599,
                        "refresh_token": "refresh-token-1",
                        "scope": GSC_SCOPE,  # not the scope GA4 requires
                        "token_type": "Bearer",
                    }
                ),
            )

        responses.add_callback(
            responses.POST, TOKEN_URI, callback=newer_intent_then_wrong_scope,
            content_type="application/json",
        )
        before = self._advance_generation(project)
        before.lifecycle_generation += 1

        response = client.get(CALLBACK, {"state": state, "code": "auth-code"})

        assert response.status_code == 302
        assert "oauth_error=invalid_state" in response.url
        self._assert_untouched_and_no_failure_audit(project, before)
        assert not IntegrationCredential.objects.exists()

    def test_stale_denial_does_not_delete_newer_authorization_connection(
        self, monkeypatch, signed_in_client, make_project
    ):
        """The sharpest case: a stale denial would delete a live connection.

        Denial makes no outbound call, so the window is between consuming the
        request and writing the failure. A newer intent landing there is
        reproduced by advancing the generation immediately after consumption.
        """
        from integrations import oauth_service

        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)

        real_consume = oauth_service._consume_request

        def consume_then_newer_intent(**kwargs):
            request = real_consume(**kwargs)
            self._advance_generation(project)
            return request

        monkeypatch.setattr(oauth_service, "_consume_request", consume_then_newer_intent)

        before = IntegrationConnection.objects.get(project=project)
        before.lifecycle_generation += 1

        response = client.get(CALLBACK, {"state": state, "error": "access_denied"})

        assert response.status_code == 302
        assert "oauth_error=invalid_state" in response.url
        # The newer attempt's connection survives — this is the deletion the
        # unfenced denial path would have performed.
        assert IntegrationConnection.objects.filter(project=project).exists()
        self._assert_untouched_and_no_failure_audit(project, before)

    @responses.activate
    def test_no_refresh_token_failure_is_generation_fenced(
        self, monkeypatch, signed_in_client, make_project
    ):
        """The window after stage 3 rolls back and before the error is written.

        The advance has to happen *outside* stage 3's transaction to be a
        faithful simulation: a concurrent bump commits independently, whereas
        one made inside that transaction is undone by the same rollback that
        raises NoRefreshToken. So the whole of stage 3 is replaced here, which
        is exactly the state the failure finalizer inherits.
        """
        from integrations import oauth_service

        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        state = start_flow(client, project)
        stub_token(refresh_token=None)

        def rolled_back_then_newer_intent(*, request, result, user):
            # Stage 3 rolled back (nothing written), and a newer intent lands
            # before the error state is persisted.
            self._advance_generation(project)
            raise NoRefreshToken

        monkeypatch.setattr(
            oauth_service, "_finalize_credentials", rolled_back_then_newer_intent
        )

        before = IntegrationConnection.objects.get(project=project)
        before.lifecycle_generation += 1

        response = client.get(CALLBACK, {"state": state, "code": "auth-code"})

        assert response.status_code == 302
        assert "oauth_error=invalid_state" in response.url
        self._assert_untouched_and_no_failure_audit(project, before)
        assert not IntegrationCredential.objects.exists()
