from __future__ import annotations

from rest_framework import mixins, viewsets
from rest_framework.response import Response

from common.viewsets import TenantScopedViewSetMixin

from .models import Workspace
from .serializers import WorkspaceCreateSerializer, WorkspaceSerializer
from .services import create_workspace


class WorkspaceViewSet(
    TenantScopedViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """Workspaces the requesting user belongs to. Never all workspaces."""

    serializer_class = WorkspaceSerializer

    def tenant_queryset(self, user):
        return Workspace.objects.filter(memberships__user=user).distinct()

    def create(self, request, *args, **kwargs):
        serializer = WorkspaceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        workspace = create_workspace(
            name=serializer.validated_data["name"], owner=request.user
        )
        output = WorkspaceSerializer(workspace, context=self.get_serializer_context())
        return Response(output.data, status=201)
