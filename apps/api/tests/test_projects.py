"""Project creation, listing, validation, and website URL normalization."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError

from projects.models import Project
from projects.normalization import normalize_website_url
from workspaces.services import create_workspace

pytestmark = pytest.mark.django_db


class TestProjectCreation:
    def test_create_and_list(self, signed_in_client):
        client, user, workspace = signed_in_client
        response = client.post(
            "/api/projects",
            {"workspace": workspace.id, "name": "Acme Store",
             "website_url": "https://acme.example"},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["name"] == "Acme Store"
        assert response.data["workspace"] == workspace.id

        listed = client.get("/api/projects")
        assert listed.status_code == 200
        assert [item["name"] for item in listed.data] == ["Acme Store"]

    def test_created_by_is_recorded(self, signed_in_client):
        client, user, workspace = signed_in_client
        response = client.post(
            "/api/projects",
            {"workspace": workspace.id, "name": "Acme", "website_url": "acme.example"},
            format="json",
        )
        project = Project.objects.get(pk=response.data["id"])
        assert project.created_by == user

    def test_workspace_may_be_omitted_when_the_user_has_exactly_one(
        self, signed_in_client
    ):
        client, _user, workspace = signed_in_client
        response = client.post(
            "/api/projects",
            {"name": "Sole", "website_url": "sole.example"},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["workspace"] == workspace.id

    def test_workspace_is_required_when_the_user_has_several(self, signed_in_client):
        client, user, _workspace = signed_in_client
        create_workspace(name="Second", owner=user)
        response = client.post(
            "/api/projects",
            {"name": "Ambiguous", "website_url": "ambiguous.example"},
            format="json",
        )
        assert response.status_code == 400
        assert "workspace" in response.data["error"]["detail"]

    def test_duplicate_name_in_the_same_workspace_is_rejected(self, signed_in_client):
        client, _user, workspace = signed_in_client
        payload = {"workspace": workspace.id, "name": "Dup", "website_url": "dup.example"}
        assert client.post("/api/projects", payload, format="json").status_code == 201

        second = client.post("/api/projects", payload, format="json")
        assert second.status_code == 400
        assert "name" in second.data["error"]["detail"]

    def test_same_name_in_a_different_workspace_is_allowed(self, signed_in_client):
        client, user, workspace = signed_in_client
        other = create_workspace(name="Other", owner=user)
        payload = {"name": "Shared", "website_url": "shared.example"}
        first = client.post(
            "/api/projects", {**payload, "workspace": workspace.id}, format="json"
        )
        second = client.post(
            "/api/projects", {**payload, "workspace": other.id}, format="json"
        )
        assert first.status_code == 201
        assert second.status_code == 201

    def test_blank_name_is_rejected(self, signed_in_client):
        client, _user, workspace = signed_in_client
        response = client.post(
            "/api/projects",
            {"workspace": workspace.id, "name": "   ", "website_url": "x.example"},
            format="json",
        )
        assert response.status_code == 400


class TestProjectUpdateAndDelete:
    def test_rename(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        response = client.patch(
            f"/api/projects/{project.id}", {"name": "Renamed"}, format="json"
        )
        assert response.status_code == 200
        project.refresh_from_db()
        assert project.name == "Renamed"

    def test_workspace_cannot_be_changed(self, signed_in_client, make_project):
        """Moving a project between workspaces is not a V1 operation."""
        client, user, workspace = signed_in_client
        other = create_workspace(name="Other", owner=user)
        project = make_project(workspace)

        response = client.patch(
            f"/api/projects/{project.id}", {"workspace": other.id}, format="json"
        )
        assert response.status_code == 200
        project.refresh_from_db()
        assert project.workspace == workspace

    def test_delete(self, signed_in_client, make_project):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        assert client.delete(f"/api/projects/{project.id}").status_code == 204
        assert not Project.objects.filter(pk=project.pk).exists()


class TestWebsiteUrlNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("example.com", "https://example.com"),
            ("  example.com  ", "https://example.com"),
            ("EXAMPLE.COM", "https://example.com"),
            ("https://example.com", "https://example.com"),
            ("https://example.com/", "https://example.com"),
            ("https://example.com/pricing?a=1#top", "https://example.com"),
            ("http://example.com", "http://example.com"),
            ("https://www.example.co.uk/path", "https://www.example.co.uk"),
            ("https://example.com:443", "https://example.com"),
            ("http://example.com:80", "http://example.com"),
            ("https://example.com:8443", "https://example.com:8443"),
            ("https://sub.domain.example.com", "https://sub.domain.example.com"),
        ],
    )
    def test_valid_inputs_normalize(self, raw, expected):
        assert normalize_website_url(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "not a url",
            "ftp://example.com",
            "javascript:alert(1)",
            "localhost",
            "https://localhost",
            "https://192.168.0.1",
            "example",
        ],
    )
    def test_invalid_inputs_are_rejected(self, raw):
        with pytest.raises(ValidationError):
            normalize_website_url(raw)

    def test_api_stores_the_normalized_form(self, signed_in_client):
        client, _user, workspace = signed_in_client
        response = client.post(
            "/api/projects",
            {"workspace": workspace.id, "name": "Norm",
             "website_url": "  HTTPS://Example.COM/pricing  "},
            format="json",
        )
        assert response.status_code == 201
        assert response.data["website_url"] == "https://example.com"

    def test_api_rejects_an_invalid_website(self, signed_in_client):
        client, _user, workspace = signed_in_client
        response = client.post(
            "/api/projects",
            {"workspace": workspace.id, "name": "Bad", "website_url": "not a url"},
            format="json",
        )
        assert response.status_code == 400
        assert "website_url" in response.data["error"]["detail"]

    def test_model_save_normalizes_too(self, make_user_with_workspace):
        """Normalization is not only a serializer concern."""
        _user, workspace = make_user_with_workspace()
        project = Project.objects.create(
            workspace=workspace, name="Direct", website_url="Example.com/deep/path"
        )
        project.refresh_from_db()
        assert project.website_url == "https://example.com"


class TestPersistenceAcrossSessions:
    def test_project_survives_logout_and_login(self, signed_in_client):
        """The full acceptance loop: create, sign out, sign back in, still there."""
        client, user, workspace = signed_in_client
        created = client.post(
            "/api/projects",
            {"workspace": workspace.id, "name": "Persistent",
             "website_url": "persistent.example"},
            format="json",
        )
        assert created.status_code == 201

        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/projects").status_code == 403

        client.get("/api/auth/csrf")
        client.credentials(HTTP_X_CSRFTOKEN=client.cookies["pi_csrftoken"].value)
        login = client.post(
            "/api/auth/login",
            {"email": user.email, "password": "correct-horse-battery-staple"},
            format="json",
        )
        assert login.status_code == 200

        listed = client.get("/api/projects")
        assert listed.status_code == 200
        assert [item["name"] for item in listed.data] == ["Persistent"]
        assert listed.data[0]["website_url"] == "https://persistent.example"
