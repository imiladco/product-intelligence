from django.urls import path

from .views import ProjectIntegrationsView

urlpatterns = [
    path(
        "projects/<int:project_id>/integrations",
        ProjectIntegrationsView.as_view(),
        name="project-integrations",
    ),
]
