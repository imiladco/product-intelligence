from django.urls import path

from .views import (
    GoogleOAuthCallbackView,
    IntegrationAuthorizeView,
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
        "integrations/oauth/google/callback",
        GoogleOAuthCallbackView.as_view(),
        name="google-oauth-callback",
    ),
]
