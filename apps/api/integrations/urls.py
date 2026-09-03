from django.urls import path

from .views import (
    GoogleOAuthCallbackView,
    IntegrationAuthorizeView,
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
        "integrations/oauth/google/callback",
        GoogleOAuthCallbackView.as_view(),
        name="google-oauth-callback",
    ),
]
