from django.urls import path

from .views import (
    GoogleOAuthCallbackView,
    IntegrationAuthorizeView,
    IntegrationDisconnectView,
    IntegrationHealthCheckView,
    IntegrationResourcesView,
    IntegrationResourceSelectionView,
    ProjectIntegrationsView,
)

urlpatterns = [
    path(
        "projects/<int:project_id>/integrations",
        ProjectIntegrationsView.as_view(),
        name="project-integrations",
    ),
    path(
        "projects/<int:project_id>/integrations/<str:provider>/authorize",
        IntegrationAuthorizeView.as_view(),
        name="integration-authorize",
    ),
    path(
        "projects/<int:project_id>/integrations/<str:provider>/resources",
        IntegrationResourcesView.as_view(),
        name="integration-resources",
    ),
    path(
        "projects/<int:project_id>/integrations/<str:provider>/resource",
        IntegrationResourceSelectionView.as_view(),
        name="integration-resource-selection",
    ),
    path(
        "projects/<int:project_id>/integrations/<str:provider>/health-check",
        IntegrationHealthCheckView.as_view(),
        name="integration-health-check",
    ),
    path(
        "projects/<int:project_id>/integrations/<str:provider>/disconnect",
        IntegrationDisconnectView.as_view(),
        name="integration-disconnect",
    ),
    path(
        "integrations/oauth/google/callback",
        GoogleOAuthCallbackView.as_view(),
        name="google-oauth-callback",
    ),
]
