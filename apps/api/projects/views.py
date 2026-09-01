from __future__ import annotations

from common.viewsets import TenantScopedModelViewSet

from .models import Project
from .serializers import ProjectSerializer


class ProjectViewSet(TenantScopedModelViewSet):
    serializer_class = ProjectSerializer

    def tenant_queryset(self, user):
        """Every project the user can reach, derived from their memberships.

        A ``workspace`` query parameter can only narrow this set further; it is
        never the source of authority.
        """
        queryset = (
            Project.objects.filter(workspace__memberships__user=user)
            .select_related("workspace")
            .distinct()
        )
        workspace_id = self.request.query_params.get("workspace")
        if workspace_id:
            if not workspace_id.isdigit():
                return queryset.none()
            queryset = queryset.filter(workspace_id=int(workspace_id))
        return queryset

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
