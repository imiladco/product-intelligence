from __future__ import annotations

from rest_framework.response import Response
from rest_framework.views import APIView

from projects.selectors import get_project_for_user

from .serializers import IntegrationEntrySerializer
from .services import integrations_for_project


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
