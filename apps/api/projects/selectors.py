"""Tenant-scoped project lookups.

Nested project resources (integrations now, OAuth and health checks later)
resolve their parent project through here, so every one of them derives access
from the request user's memberships rather than trusting a path parameter.
"""

from __future__ import annotations

from django.db.models import QuerySet
from django.http import Http404

from .models import Project


def projects_for_user(user) -> QuerySet[Project]:
    if not user.is_authenticated:
        return Project.objects.none()
    return Project.objects.filter(workspace__memberships__user=user).distinct()


def get_project_for_user(user, project_id) -> Project:
    """Return the project, or raise 404.

    A project in another workspace and a project that does not exist are
    indistinguishable: both raise Http404, so the API discloses nothing.
    """
    try:
        return projects_for_user(user).select_related("workspace").get(pk=project_id)
    except (Project.DoesNotExist, ValueError, TypeError) as exc:
        raise Http404("No Project matches the given query.") from exc
