"""Tenant isolation.

This is the invariant that matters most in this codebase: a user must never
reach another workspace's data. Cross-tenant access returns 404, never 403, so
the API does not disclose that the object exists.
"""

from __future__ import annotations

import pytest

from common.viewsets import TenantScopedModelViewSet
from projects.models import Project
from tests.conftest import PASSWORD
from workspaces.models import Workspace
from workspaces.services import create_initial_workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_tenants(make_user, csrf_client):
    """Two users in separate workspaces, each with a project."""
    alice = make_user(email="alice@example.com")
    bob = make_user(email="bob@example.com")
    alice_ws = create_initial_workspace(alice)
    bob_ws = create_initial_workspace(bob)
    alice_project = Project.objects.create(
        workspace=alice_ws, name="Alice Site", website_url="https://alice.example"
    )
    bob_project = Project.objects.create(
        workspace=bob_ws, name="Bob Site", website_url="https://bob.example"
    )

    csrf_client.post(
        "/api/auth/login",
        {"email": bob.email, "password": PASSWORD},
        format="json",
    )
    csrf_client.credentials(HTTP_X_CSRFTOKEN=csrf_client.cookies["pi_csrftoken"].value)
    return {
        "client_as_bob": csrf_client,
        "alice": alice,
        "bob": bob,
        "alice_ws": alice_ws,
        "bob_ws": bob_ws,
        "alice_project": alice_project,
        "bob_project": bob_project,
    }


class TestProjectIsolation:
    def test_list_returns_only_the_users_own_projects(self, two_tenants):
        response = two_tenants["client_as_bob"].get("/api/projects")
        assert response.status_code == 200
        returned = {item["id"] for item in response.data}
        assert returned == {two_tenants["bob_project"].id}
        assert two_tenants["alice_project"].id not in returned

    def test_retrieving_another_tenants_project_returns_404(self, two_tenants):
        response = two_tenants["client_as_bob"].get(
            f"/api/projects/{two_tenants['alice_project'].id}"
        )
        assert response.status_code == 404
        assert response.data["error"]["code"] == "not_found"

    def test_updating_another_tenants_project_returns_404(self, two_tenants):
        response = two_tenants["client_as_bob"].patch(
            f"/api/projects/{two_tenants['alice_project'].id}",
            {"name": "Hijacked"},
            format="json",
        )
        assert response.status_code == 404
        two_tenants["alice_project"].refresh_from_db()
        assert two_tenants["alice_project"].name == "Alice Site"

    def test_deleting_another_tenants_project_returns_404(self, two_tenants):
        response = two_tenants["client_as_bob"].delete(
            f"/api/projects/{two_tenants['alice_project'].id}"
        )
        assert response.status_code == 404
        assert Project.objects.filter(pk=two_tenants["alice_project"].pk).exists()

    def test_cannot_create_a_project_in_another_tenants_workspace(self, two_tenants):
        response = two_tenants["client_as_bob"].post(
            "/api/projects",
            {
                "workspace": two_tenants["alice_ws"].id,
                "name": "Planted",
                "website_url": "https://planted.example",
            },
            format="json",
        )
        assert response.status_code == 400
        assert "workspace" in response.data["error"]["detail"]
        assert not Project.objects.filter(name="Planted").exists()

    def test_foreign_and_nonexistent_workspace_ids_are_indistinguishable(
        self, two_tenants
    ):
        """No existence oracle: both cases produce the same error."""
        client = two_tenants["client_as_bob"]
        foreign = client.post(
            "/api/projects",
            {"workspace": two_tenants["alice_ws"].id, "name": "A",
             "website_url": "https://a.example"},
            format="json",
        )
        missing = client.post(
            "/api/projects",
            {"workspace": 9_999_999, "name": "B", "website_url": "https://b.example"},
            format="json",
        )
        assert foreign.status_code == missing.status_code == 400
        assert foreign.data["error"] == missing.data["error"]

    def test_workspace_query_param_cannot_widen_access(self, two_tenants):
        """The filter may only narrow the membership-derived queryset."""
        response = two_tenants["client_as_bob"].get(
            f"/api/projects?workspace={two_tenants['alice_ws'].id}"
        )
        assert response.status_code == 200
        assert response.data == []

    def test_malformed_workspace_query_param_is_not_a_server_error(self, two_tenants):
        response = two_tenants["client_as_bob"].get("/api/projects?workspace=abc")
        assert response.status_code == 200
        assert response.data == []


class TestWorkspaceIsolation:
    def test_list_returns_only_the_users_own_workspaces(self, two_tenants):
        response = two_tenants["client_as_bob"].get("/api/workspaces")
        assert response.status_code == 200
        assert {item["id"] for item in response.data} == {two_tenants["bob_ws"].id}

    def test_retrieving_another_tenants_workspace_returns_404(self, two_tenants):
        response = two_tenants["client_as_bob"].get(
            f"/api/workspaces/{two_tenants['alice_ws'].id}"
        )
        assert response.status_code == 404

    def test_creating_a_workspace_makes_the_creator_its_owner(self, two_tenants):
        response = two_tenants["client_as_bob"].post(
            "/api/workspaces", {"name": "Second Workspace"}, format="json"
        )
        assert response.status_code == 201
        assert response.data["role"] == "owner"

        workspace = Workspace.objects.get(pk=response.data["id"])
        assert workspace.has_member(two_tenants["bob"])
        assert not workspace.has_member(two_tenants["alice"])


class TestUnauthenticatedAccess:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("get", "/api/projects"),
            ("post", "/api/projects"),
            ("get", "/api/projects/1"),
            ("patch", "/api/projects/1"),
            ("delete", "/api/projects/1"),
            ("get", "/api/workspaces"),
            ("post", "/api/workspaces"),
            ("get", "/api/workspaces/1"),
            ("get", "/api/auth/me"),
            ("post", "/api/auth/logout"),
        ],
    )
    def test_endpoints_reject_anonymous_requests(self, csrf_client, method, path):
        response = getattr(csrf_client, method)(path)
        assert response.status_code == 403
        assert response.data["error"]["code"] in {
            "not_authenticated",
            "permission_denied",
        }

    def test_anonymous_requests_never_leak_data(self, csrf_client, make_user_with_workspace):
        _user, workspace = make_user_with_workspace()
        Project.objects.create(
            workspace=workspace, name="Secret", website_url="https://secret.example"
        )
        response = csrf_client.get("/api/projects")
        assert response.status_code == 403
        assert "Secret" not in response.content.decode()


class TestScopedViewSetGuard:
    """The base class must make the unsafe path impossible, not merely discouraged."""

    def test_subclass_without_a_tenant_filter_fails_at_definition(self):
        with pytest.raises(TypeError, match="tenant_queryset"):

            class Unsafe(TenantScopedModelViewSet):
                pass

    def test_subclass_with_a_tenant_filter_is_accepted(self):
        class Safe(TenantScopedModelViewSet):
            def tenant_queryset(self, user):
                return Project.objects.filter(workspace__memberships__user=user)

        assert callable(Safe.tenant_queryset)
