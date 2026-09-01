"""Signup, login, logout, session persistence, and CSRF behaviour."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from tests.conftest import PASSWORD
from workspaces.models import Membership, Workspace

User = get_user_model()

pytestmark = pytest.mark.django_db


class TestSignup:
    def test_creates_user_and_signs_them_in(self, csrf_client):
        response = csrf_client.post(
            "/api/auth/signup",
            {"email": "New.User@Example.com", "password": PASSWORD, "name": "New User"},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["user"]["email"] == "new.user@example.com"

        # The session is live immediately after signup.
        me = csrf_client.get("/api/auth/me")
        assert me.status_code == 200
        assert me.data["user"]["email"] == "new.user@example.com"

    def test_creates_exactly_one_workspace_owned_by_the_user(self, csrf_client):
        response = csrf_client.post(
            "/api/auth/signup",
            {"email": "owner@example.com", "password": PASSWORD, "name": "Owner"},
            format="json",
        )
        assert response.status_code == 201

        user = User.objects.get(email="owner@example.com")
        workspaces = Workspace.objects.filter(memberships__user=user)
        assert workspaces.count() == 1

        membership = Membership.objects.get(user=user)
        assert membership.role == Membership.Role.OWNER
        assert membership.is_owner

        # And it is reported back to the client.
        assert len(response.data["workspaces"]) == 1
        assert response.data["workspaces"][0]["role"] == "owner"

    def test_workspace_name_defaults_from_the_user(self, csrf_client):
        csrf_client.post(
            "/api/auth/signup",
            {"email": "dana@example.com", "password": PASSWORD, "name": "Dana"},
            format="json",
        )
        assert Workspace.objects.get().name == "Dana's workspace"

    def test_duplicate_email_is_rejected_case_insensitively(self, csrf_client, make_user):
        make_user(email="taken@example.com")
        response = csrf_client.post(
            "/api/auth/signup",
            {"email": "TAKEN@example.com", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["error"]["code"] == "validation_error"
        assert "email" in response.data["error"]["detail"]

    def test_weak_password_is_rejected_and_no_user_is_created(self, csrf_client):
        response = csrf_client.post(
            "/api/auth/signup",
            {"email": "weak@example.com", "password": "password"},
            format="json",
        )
        assert response.status_code == 400
        assert "password" in response.data["error"]["detail"]
        assert not User.objects.filter(email="weak@example.com").exists()

    def test_password_is_hashed_not_stored_in_plaintext(self, csrf_client):
        csrf_client.post(
            "/api/auth/signup",
            {"email": "hash@example.com", "password": PASSWORD},
            format="json",
        )
        user = User.objects.get(email="hash@example.com")
        assert user.password != PASSWORD
        assert user.password.startswith("argon2")
        assert user.check_password(PASSWORD)


class TestLogin:
    def test_valid_credentials_start_a_session(self, csrf_client, make_user_with_workspace):
        user, workspace = make_user_with_workspace()
        response = csrf_client.post(
            "/api/auth/login",
            {"email": user.email, "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 200
        assert response.data["user"]["email"] == user.email
        assert [w["id"] for w in response.data["workspaces"]] == [workspace.id]

    def test_email_is_case_insensitive(self, csrf_client, make_user_with_workspace):
        user, _ = make_user_with_workspace(email="case@example.com")
        response = csrf_client.post(
            "/api/auth/login",
            {"email": "CASE@Example.com", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 200

    def test_wrong_password_is_rejected(self, csrf_client, make_user_with_workspace):
        user, _ = make_user_with_workspace()
        response = csrf_client.post(
            "/api/auth/login",
            {"email": user.email, "password": "not-the-password"},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["error"]["message"] == "Incorrect email or password."

    def test_unknown_email_gives_the_same_message(self, csrf_client):
        """The endpoint must not reveal whether an account exists."""
        response = csrf_client.post(
            "/api/auth/login",
            {"email": "nobody@example.com", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 400
        assert response.data["error"]["message"] == "Incorrect email or password."

    def test_inactive_user_cannot_sign_in(self, csrf_client, make_user):
        user = make_user(email="off@example.com")
        user.is_active = False
        user.save()
        response = csrf_client.post(
            "/api/auth/login",
            {"email": user.email, "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 400

    def test_session_key_rotates_on_login(self, csrf_client, make_user_with_workspace):
        """Guards against session fixation."""
        user, _ = make_user_with_workspace()
        csrf_client.get("/api/auth/csrf")
        before = csrf_client.cookies.get("pi_sessionid")
        before_value = before.value if before else None

        csrf_client.post(
            "/api/auth/login",
            {"email": user.email, "password": PASSWORD},
            format="json",
        )
        after_value = csrf_client.cookies["pi_sessionid"].value
        assert after_value
        assert after_value != before_value


class TestLogout:
    def test_logout_ends_the_session(self, signed_in_client):
        client, _user, _workspace = signed_in_client
        assert client.get("/api/auth/me").status_code == 200

        response = client.post("/api/auth/logout")
        assert response.status_code == 204

        assert client.get("/api/auth/me").status_code == 403

    def test_logout_requires_authentication(self, csrf_client):
        assert csrf_client.post("/api/auth/logout").status_code == 403


class TestSessionCookies:
    def test_session_cookie_is_httponly_and_samesite_lax(self, signed_in_client):
        client, _user, _workspace = signed_in_client
        cookie = client.cookies["pi_sessionid"]
        assert cookie["httponly"]
        assert cookie["samesite"] == "Lax"

    def test_csrf_cookie_is_readable_by_the_frontend(self, csrf_client):
        cookie = csrf_client.cookies["pi_csrftoken"]
        assert not cookie["httponly"]


class TestCSRFEnforcement:
    """DRF views are csrf_exempt by default, so these must be explicitly protected."""

    def test_login_without_csrf_token_is_rejected(self, api_client, make_user_with_workspace):
        user, _ = make_user_with_workspace()
        response = api_client.post(
            "/api/auth/login",
            {"email": user.email, "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 403

    def test_signup_without_csrf_token_is_rejected(self, api_client):
        response = api_client.post(
            "/api/auth/signup",
            {"email": "nocsrf@example.com", "password": PASSWORD},
            format="json",
        )
        assert response.status_code == 403
        assert not User.objects.filter(email="nocsrf@example.com").exists()

    def test_authenticated_mutation_without_csrf_token_is_rejected(
        self, signed_in_client
    ):
        client, _user, workspace = signed_in_client
        client.credentials()  # drop the X-CSRFToken header
        response = client.post(
            "/api/projects",
            {"workspace": workspace.id, "name": "No CSRF", "website_url": "example.com"},
            format="json",
        )
        assert response.status_code == 403

    def test_safe_requests_do_not_need_a_csrf_token(self, signed_in_client):
        client, _user, _workspace = signed_in_client
        client.credentials()
        assert client.get("/api/projects").status_code == 200


class TestThrottling:
    """Signup and login are rate limited to blunt credential stuffing."""

    def test_repeated_failed_logins_are_throttled(self, csrf_client):
        # Exercises the configured rate rather than patching it: SimpleRateThrottle
        # caches THROTTLE_RATES as a class attribute at import time, so overriding
        # the setting at runtime would not take effect.
        rate = int(settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["auth"].split("/")[0])
        payload = {"email": "nobody@example.com", "password": "wrong-password-here"}
        statuses = [
            csrf_client.post("/api/auth/login", payload, format="json").status_code
            for _ in range(rate + 1)
        ]
        assert statuses[0] == 400
        assert statuses[-1] == 429
