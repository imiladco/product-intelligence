from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APIClient

from projects.models import Project
from workspaces.services import create_initial_workspace

User = get_user_model()

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(autouse=True)
def reset_throttle_state():
    """DRF throttles persist in the cache between tests; isolate each test."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_client() -> APIClient:
    """CSRF checks are enforced, matching production behaviour."""
    return APIClient(enforce_csrf_checks=True)


@pytest.fixture
def make_user(db):
    def _make(email: str = "user@example.com", password: str = PASSWORD, **extra):
        return User.objects.create_user(email=email, password=password, **extra)

    return _make


@pytest.fixture
def make_user_with_workspace(make_user):
    def _make(email: str = "user@example.com", **extra):
        user = make_user(email=email, **extra)
        workspace = create_initial_workspace(user)
        return user, workspace

    return _make


@pytest.fixture
def make_project(db):
    def _make(workspace, name: str = "Acme", website_url: str = "https://acme.example"):
        return Project.objects.create(
            workspace=workspace, name=name, website_url=website_url
        )

    return _make


@pytest.fixture
def csrf_client(api_client):
    """A client that has fetched the CSRF cookie and sends the matching header."""

    def _prepare(client: APIClient) -> APIClient:
        client.get("/api/auth/csrf")
        token = client.cookies["pi_csrftoken"].value
        client.credentials(HTTP_X_CSRFTOKEN=token)
        return client

    return _prepare(api_client)


@pytest.fixture
def signed_in_client(csrf_client, make_user_with_workspace):
    """An authenticated client with a CSRF token, plus its user and workspace."""
    user, workspace = make_user_with_workspace()
    response = csrf_client.post(
        "/api/auth/login",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert response.status_code == 200, response.data
    # Django rotates the CSRF token on login; re-send the current one.
    csrf_client.credentials(HTTP_X_CSRFTOKEN=csrf_client.cookies["pi_csrftoken"].value)
    return csrf_client, user, workspace
