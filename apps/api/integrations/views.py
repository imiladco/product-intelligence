from __future__ import annotations

import logging
from urllib.parse import urlencode

from django.conf import settings
from django.http import Http404, HttpResponseRedirect
from rest_framework.response import Response
from rest_framework.views import APIView

from projects.selectors import get_project_for_user

from .google.errors import OAuthError
from .oauth_service import complete_authorization, start_authorization
from .providers import get_provider
from .serializers import IntegrationEntrySerializer
from .services import integrations_for_project

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
    """GET /api/projects/{project_id}/integrations/{provider}/authorize

    Returns the Google consent URL. The browser is sent there by the frontend;
    Next.js never builds an authorization URL or holds a client secret.
    """

    def get(self, request, project_id, provider):
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
