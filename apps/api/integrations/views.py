from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.selectors import get_project_for_user

from common.errors import error_response

from .google.errors import GoogleApiError, OAuthError
from .lifecycle_service import disconnect, health_check
from .oauth_service import complete_authorization, start_authorization
from .providers import get_provider
from .resource_service import discover_resources, select_resource
from .serializers import (
    DiscoveredResourceSerializer,
    IntegrationEntrySerializer,
    ResourceSelectionSerializer,
)
from .services import integrations_for_project, integration_entry_for_provider

logger = logging.getLogger(__name__)


class ProjectIntegrationsView(APIView):
    """GET /api/projects/{project_id}/integrations

    The project is resolved through the requesting user's memberships first, so
    a project in another workspace is a 404 before any integration data is
    touched. The path parameter is never treated as proof of ownership.
    """

    def get(self, request, project_id):
        project = get_project_for_user(request.user, project_id)
        entries = integrations_for_project(project)
        return Response(IntegrationEntrySerializer(entries, many=True).data)


class IntegrationAuthorizeView(APIView):
    """POST /api/projects/{project_id}/integrations/{provider}/authorize

    Returns the Google consent URL. The browser is sent there by the frontend;
    Next.js never builds an authorization URL or holds a client secret.

    POST, not GET: starting an authorization has real side effects — it creates
    a connection row on first use, creates a single-use authorization request,
    and writes an audit event. A state-changing GET would be triggerable by any
    cross-site navigation and would bypass CSRF entirely. As a POST it goes
    through SessionAuthentication's CSRF enforcement like every other mutation.
    A GET is answered with 405 by DRF, so an old link cannot start a flow.
    """

    def post(self, request, project_id, provider):
        project = get_project_for_user(request.user, project_id)
        if get_provider(provider) is None:
            raise Http404("Unknown provider.")

        start = start_authorization(
            user=request.user, project=project, provider_key=provider
        )
        return Response({"authorization_url": start.authorization_url})


class GoogleOAuthCallbackView(APIView):
    """GET /api/integrations/oauth/google/callback

    Google redirects the browser here with transient authorization parameters.
    The response is always a redirect to a clean frontend URL: no HTML is
    rendered containing the code, and the code and state do not survive in the
    address bar.

    One callback URL serves both providers. The provider and project come from
    the stored authorization request, never from the query string, so neither
    can be tampered with.
    """

    def get(self, request):
        # Never log request.GET or the full path: they carry the code and state.
        state = request.GET.get("state", "")
        code = request.GET.get("code", "")
        error = request.GET.get("error", "")

        try:
            oauth_request = complete_authorization(
                user=request.user, state=state, code=code, error=error
            )
        except OAuthError as exc:
            logger.info("OAuth callback rejected: %s", exc.code)
            return self._redirect_to_projects(exc.code)

        return self._redirect(
            project_id=oauth_request.project_id,
            params={"provider": oauth_request.provider, "authorized": "1"},
        )

    def _redirect(self, *, project_id, params: dict) -> HttpResponseRedirect:
        base = settings.APP_URL.rstrip("/")
        return HttpResponseRedirect(
            f"{base}/projects/{project_id}/integrations?{urlencode(params)}"
        )

    def _redirect_to_projects(self, error_code: str) -> HttpResponseRedirect:
        """Failure path.

        The project is not disclosed here: a rejected state may belong to
        another tenant, or to nobody, and the two must look the same.
        """
        base = settings.APP_URL.rstrip("/")
        return HttpResponseRedirect(f"{base}/projects?{urlencode({'oauth_error': error_code})}")


class GoogleApiErrorMixin:
    """Turns a Google boundary error into the project's error envelope.

    Every one of these carries its own code, message and status, so the mapping
    is one lookup rather than a chain of except clauses in each view. The
    exception's own text is what the user sees; Google's is never in it.
    """

    def handle_exception(self, exc):
        if isinstance(exc, GoogleApiError):
            if exc.http_status == 404:
                raise Http404(exc.message) from exc
            return error_response(
                exc.code, exc.message, http_status=exc.http_status
            )
        return super().handle_exception(exc)


class IntegrationResourcesView(GoogleApiErrorMixin, APIView):
    """GET /api/projects/{project_id}/integrations/{provider}/resources

    The external resources the connection's Google account can actually read —
    for GA4, its properties. Read-only, and it creates nothing: a provider
    nobody has authorized answers with a conflict rather than a fresh row.

    Throttled separately from the rest of the API: every call spends quota on
    Google's side, not ours.
    """

    throttle_scope = "integrations"

    def get(self, request, project_id, provider):
        project = get_project_for_user(request.user, project_id)
        if get_provider(provider) is None:
            raise Http404("Unknown provider.")

        discovered = discover_resources(project=project, provider_key=provider)
        return Response(
            {
                "resources": DiscoveredResourceSerializer(
                    discovered.resources, many=True
                ).data,
                "truncated": discovered.truncated,
            }
        )


class IntegrationResourceSelectionView(GoogleApiErrorMixin, APIView):
    """POST /api/projects/{project_id}/integrations/{provider}/resource

    Selects the resource this integration will use. The body carries an
    identifier and nothing else: the backend verifies that exact resource
    against Google and stores the label Google returns, so posting an arbitrary
    identifier — or a flattering label — cannot make a connection connected.

    Returns the same integration entry the page already renders, so the client
    re-renders from one authoritative payload instead of patching local state.
    """

    throttle_scope = "integrations"

    def post(self, request, project_id, provider):
        project = get_project_for_user(request.user, project_id)
        if get_provider(provider) is None:
            raise Http404("Unknown provider.")

        serializer = ResourceSelectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        select_resource(
            user=request.user,
            project=project,
            provider_key=provider,
            resource_id=serializer.validated_data["resource_id"],
        )
        entry = integration_entry_for_provider(project, provider)
        return Response(IntegrationEntrySerializer(entry).data)


class IntegrationHealthCheckView(GoogleApiErrorMixin, APIView):
    """POST /api/projects/{project_id}/integrations/{provider}/health-check

    Runs the check now, against the resource the connection already points at.
    The request body is not read at all: an identifier in it would be a way to
    ask about a resource this connection has not selected.

    A check that *ran* is always a 200 carrying the resulting entry, whatever
    it found — a dead credential and an unreachable Google are answers, not
    failures of the endpoint. Only a check that could not begin is a 409.

    POST because it writes: status, both health timestamps and the recorded
    error are all consequences of running it.
    """

    throttle_scope = "integrations"

    def post(self, request, project_id, provider):
        project = get_project_for_user(request.user, project_id)
        if get_provider(provider) is None:
            raise Http404("Unknown provider.")

        health_check(project=project, provider_key=provider)
        entry = integration_entry_for_provider(project, provider)
        return Response(IntegrationEntrySerializer(entry).data)


class IntegrationDisconnectView(GoogleApiErrorMixin, APIView):
    """POST /api/projects/{project_id}/integrations/{provider}/disconnect

    Ends the integration here. The Google grant is never revoked: it belongs to
    the user's Google account, and one consent can cover more than this
    connection.

    Answers 200 even when there was nothing to disconnect. The result the
    caller asked for — not connected, no credential — is already true, and the
    response carries the same entry the page renders either way.

    On the same throttle scope as every other integration endpoint: it is a
    write on a connection the same rate limit governs, and leaving one view out
    of the scope is invisible until someone finds it.
    """

    throttle_scope = "integrations"

    def post(self, request, project_id, provider):
        project = get_project_for_user(request.user, project_id)
        if get_provider(provider) is None:
            raise Http404("Unknown provider.")

        disconnect(user=request.user, project=project, provider_key=provider)
        entry = integration_entry_for_provider(project, provider)
        return Response(IntegrationEntrySerializer(entry).data)
