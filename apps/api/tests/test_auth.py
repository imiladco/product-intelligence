"""Signup, login, logout, session persistence, and CSRF behaviour."""

from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from accounts.serializers import SignupSerializer
from accounts.services import EmailAlreadyRegistered, register_user
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


class TestSignupAtomicity:
    """Successful signup means User + Workspace + owner Membership, or nothing.

    A User with no workspace could sign in but would have nowhere to put a
    project, and nothing in V1 creates the missing workspace afterwards.
    """

    def test_workspace_failure_rolls_back_the_user(self, csrf_client, monkeypatch):
        def boom(_user):
            raise RuntimeError("workspace backend unavailable")

        monkeypatch.setattr("accounts.services.create_initial_workspace", boom)

        with pytest.raises(RuntimeError, match="workspace backend unavailable"):
            csrf_client.post(
                "/api/auth/signup",
                {"email": "orphan@example.com", "password": PASSWORD, "name": "Orphan"},
                format="json",
            )

        assert not User.objects.filter(email="orphan@example.com").exists()
        assert Workspace.objects.count() == 0

    def test_membership_failure_leaves_no_partial_account(self, csrf_client, monkeypatch):
        """The workspace row is created before the membership; both must roll back."""
        original_create = Membership.objects.create

        def boom(*args, **kwargs):
            raise RuntimeError("membership insert failed")

        monkeypatch.setattr(Membership.objects, "create", boom)

        with pytest.raises(RuntimeError, match="membership insert failed"):
            csrf_client.post(
                "/api/auth/signup",
                {"email": "partial@example.com", "password": PASSWORD, "name": "Partial"},
                format="json",
            )

        monkeypatch.setattr(Membership.objects, "create", original_create)

        assert not User.objects.filter(email="partial@example.com").exists()
        assert Workspace.objects.count() == 0
        assert Membership.objects.count() == 0

    def test_duplicate_email_race_is_a_validation_error_not_a_500(
        self, csrf_client, make_user, monkeypatch
    ):
        """The database constraint is the backstop the pre-check cannot provide.

        Two concurrent signups both pass validate_email, then one insert loses.
        Simulated by disabling the pre-check so the unique constraint is what
        rejects the request.
        """
        make_user(email="race@example.com")

        monkeypatch.setattr(
            SignupSerializer,
            "validate_email",
            lambda _self, value: value.strip().lower(),
        )

        response = csrf_client.post(
            "/api/auth/signup",
            {"email": "race@example.com", "password": PASSWORD, "name": "Racer"},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "validation_error"
        assert response.data["error"]["detail"]["email"] == [
            "An account with this email already exists."
        ]
        # Exactly one account, and no stray workspace from the losing attempt.
        assert User.objects.filter(email="race@example.com").count() == 1
        assert Workspace.objects.count() == 0

    def test_race_response_is_identical_to_the_ordinary_duplicate(
        self, csrf_client, make_user, monkeypatch
    ):
        make_user(email="same@example.com")
        payload = {"email": "same@example.com", "password": PASSWORD}

        ordinary = csrf_client.post("/api/auth/signup", payload, format="json")

        monkeypatch.setattr(
            SignupSerializer,
            "validate_email",
            lambda _self, value: value.strip().lower(),
        )
        raced = csrf_client.post("/api/auth/signup", payload, format="json")

        assert ordinary.status_code == raced.status_code == 400
        assert ordinary.data == raced.data

    def test_successful_signup_persists_all_three_rows(self, csrf_client):
        response = csrf_client.post(
            "/api/auth/signup",
            {"email": "whole@example.com", "password": PASSWORD, "name": "Whole"},
            format="json",
        )
        assert response.status_code == 201

        user = User.objects.get(email="whole@example.com")
        membership = Membership.objects.get(user=user)
        assert membership.role == Membership.Role.OWNER
        assert Workspace.objects.filter(pk=membership.workspace_id).exists()


class TestSignupErrorClassification:
    """Only an email collision may be reported as a duplicate email.

    Registration inserts three rows, so an IntegrityError can come from the
    workspace or the membership as easily as from the user. Reporting any of
    them as "an account with this email already exists" would hide a real fault
    behind a validation message the user cannot act on.
    """

    def test_workspace_integrity_error_is_not_a_duplicate_email(self, db, monkeypatch):
        def failing_workspace(_user):
            raise IntegrityError("workspaces_workspace_slug_key")

        monkeypatch.setattr("accounts.services.create_initial_workspace", failing_workspace)

        with pytest.raises(IntegrityError):
            register_user(email="ws@example.com", password=PASSWORD, name="WS")

        assert not User.objects.filter(email="ws@example.com").exists()
        assert Workspace.objects.count() == 0
        assert Membership.objects.count() == 0

    def test_membership_integrity_error_is_not_a_duplicate_email(self, db, monkeypatch):
        def failing_membership(*_args, **_kwargs):
            raise IntegrityError("unique_membership_per_workspace")

        monkeypatch.setattr(Membership.objects, "create", failing_membership)

        with pytest.raises(IntegrityError):
            register_user(email="mem@example.com", password=PASSWORD, name="Mem")

        monkeypatch.undo()

        assert not User.objects.filter(email="mem@example.com").exists()
        assert Workspace.objects.count() == 0
        assert Membership.objects.count() == 0

    def test_workspace_integrity_error_reaches_the_api_as_a_server_error(
        self, csrf_client, monkeypatch
    ):
        """Not a 400 telling the user to pick a different email."""

        def failing_workspace(_user):
            raise IntegrityError("workspaces_workspace_slug_key")

        monkeypatch.setattr("accounts.services.create_initial_workspace", failing_workspace)

        with pytest.raises(IntegrityError):
            csrf_client.post(
                "/api/auth/signup",
                {"email": "boom@example.com", "password": PASSWORD, "name": "Boom"},
                format="json",
            )

        assert not User.objects.filter(email="boom@example.com").exists()

    def test_genuine_email_collision_is_still_classified_as_duplicate(
        self, db, make_user, monkeypatch
    ):
        """The user insert itself fails, and the address is genuinely taken."""
        make_user(email="taken@example.com")

        with pytest.raises(EmailAlreadyRegistered):
            register_user(email="TAKEN@example.com", password=PASSWORD, name="Dup")

        assert User.objects.filter(email="taken@example.com").count() == 1
        assert Workspace.objects.count() == 0

    def test_a_non_email_failure_of_the_user_insert_is_not_reclassified(
        self, db, monkeypatch
    ):
        """An IntegrityError on the user insert with no matching row must propagate.

        The probe asks the database whether the address is now taken rather than
        parsing a driver message, so an unrelated constraint failure on the same
        statement stays an IntegrityError.
        """

        def failing_create_user(*_args, **_kwargs):
            raise IntegrityError("some_other_constraint")

        monkeypatch.setattr(User.objects, "create_user", failing_create_user)

        with pytest.raises(IntegrityError):
            register_user(email="other@example.com", password=PASSWORD, name="Other")

        monkeypatch.undo()

        assert not User.objects.filter(email="other@example.com").exists()
