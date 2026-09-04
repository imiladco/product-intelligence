"""Search Console site discovery, selection, and the permission rule.

Mirrors test_ga4_resources.py so the two providers read the same way, plus the
three things that are genuinely different here: there is no pagination, the
identifier is a URL that must survive percent-encoding, and a 200 response is
not by itself proof of access.

No test contacts Google. Several assert that *no* call was made at all, which
only works because rejection happens before the request is built.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
import responses
from django.utils import timezone
from rest_framework.test import APIClient

from audit.models import AuditEvent
from integrations.google import search_console
from integrations.models import IntegrationConnection, IntegrationCredential
from integrations.providers import ProviderKey
from integrations.status import ConnectionStatus
from workspaces.models import Membership

pytestmark = pytest.mark.django_db

BASE = "https://www.googleapis.com/webmasters/v3"
SITES_URL = f"{BASE}/sites"
GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

DOMAIN_SITE = "sc-domain:example.com"
PREFIX_SITE = "https://shop.example.com/"


@pytest.fixture(autouse=True)
def google_settings(settings):
    settings.GOOGLE_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    settings.GOOGLE_CLIENT_SECRET = "test-client-secret"
    settings.SEARCH_CONSOLE_BASE_URL = BASE
    return settings


def resources_url(project_id) -> str:
    return f"/api/projects/{project_id}/integrations/search_console/resources"


def selection_url(project_id) -> str:
    return f"/api/projects/{project_id}/integrations/search_console/resource"


@pytest.fixture
def authorized_project(signed_in_client, make_project):
    """A project whose Search Console integration is authorized, awaiting a site."""
    client, user, workspace = signed_in_client
    project = make_project(workspace)
    connection = IntegrationConnection.objects.create(
        project=project,
        provider=ProviderKey.SEARCH_CONSOLE,
        status=ConnectionStatus.AWAITING_RESOURCE_SELECTION,
        granted_scopes=[GSC_SCOPE],
        connected_by=user,
    )
    IntegrationCredential.objects.create(
        connection=connection,
        access_token="access-token-1",
        refresh_token="refresh-token-1",
        access_token_expires_at=timezone.now() + timedelta(hours=1),
    )
    return client, user, project, connection


def entry(site_url=DOMAIN_SITE, permission_level="siteOwner"):
    return {"siteUrl": site_url, "permissionLevel": permission_level}


def stub_sites(*entries, status=200):
    body = (
        {"siteEntry": list(entries)}
        if status == 200
        else {"error": {"message": "some Google detail that must not leak"}}
    )
    responses.add(responses.GET, SITES_URL, json=body, status=status)


def stub_site(site_url=DOMAIN_SITE, permission_level="siteOwner", status=200):
    """Stub sites.get. The URL is registered percent-encoded, as it is sent."""
    from urllib.parse import quote

    body = (
        {"siteUrl": site_url, "permissionLevel": permission_level}
        if status == 200
        else {"error": {"message": "some Google detail that must not leak"}}
    )
    responses.add(
        responses.GET, f"{BASE}/sites/{quote(site_url, safe='')}", json=body, status=status
    )


# --- Tenancy ----------------------------------------------------------------


class TestTenancy:
    def test_other_workspace_project_is_not_found(
        self, signed_in_client, make_user_with_workspace, make_project
    ):
        client, _user, _workspace = signed_in_client
        _other, other_workspace = make_user_with_workspace(email="other@example.com")
        foreign = make_project(other_workspace)

        assert client.get(resources_url(foreign.pk)).status_code == 404
        assert (
            client.post(
                selection_url(foreign.pk), {"resource_id": DOMAIN_SITE}, format="json"
            ).status_code
            == 404
        )

    def test_authentication_is_required(self, authorized_project):
        _client, _user, project, _connection = authorized_project
        anonymous = APIClient(enforce_csrf_checks=True)

        assert anonymous.get(resources_url(project.pk)).status_code == 403


# --- Discovery --------------------------------------------------------------


class TestDiscovery:
    @responses.activate
    def test_lists_sites_flat_with_no_grouping(self, authorized_project):
        client, _user, project, _connection = authorized_project
        stub_sites(entry(PREFIX_SITE), entry(DOMAIN_SITE))

        response = client.get(resources_url(project.pk))

        assert response.status_code == 200
        assert response.data["truncated"] is False
        # Sorted by identifier, and no provider grouping exists here.
        assert [item["id"] for item in response.data["resources"]] == [
            PREFIX_SITE,
            DOMAIN_SITE,
        ]
        assert all(item["group_label"] == "" for item in response.data["resources"])

    @responses.activate
    def test_makes_exactly_one_request(self, authorized_project):
        """The API documents no pagination; a paging loop would invent one."""
        client, _user, project, _connection = authorized_project
        stub_sites(entry())

        client.get(resources_url(project.pk))

        assert len(responses.calls) == 1
        assert responses.calls[0].request.url == SITES_URL

    @responses.activate
    def test_reports_each_property_kind(self, authorized_project):
        client, _user, project, _connection = authorized_project
        stub_sites(entry(DOMAIN_SITE), entry(PREFIX_SITE))

        by_id = {
            item["id"]: item["resource_type"]
            for item in client.get(resources_url(project.pk)).data["resources"]
        }

        assert by_id[DOMAIN_SITE] == "Domain property"
        assert by_id[PREFIX_SITE] == "URL-prefix property"

    @responses.activate
    def test_unverified_sites_are_not_offered(self, authorized_project):
        client, _user, project, _connection = authorized_project
        stub_sites(
            entry("https://verified.example/", "siteOwner"),
            entry("https://unverified.example/", "siteUnverifiedUser"),
        )

        listed = [item["id"] for item in client.get(resources_url(project.pk)).data["resources"]]

        assert listed == ["https://verified.example/"]

    @responses.activate
    def test_malformed_entries_are_skipped_not_fatal(self, authorized_project):
        client, _user, project, _connection = authorized_project
        stub_sites(
            {"permissionLevel": "siteOwner"},
            {"siteUrl": "not-a-site", "permissionLevel": "siteOwner"},
            entry(DOMAIN_SITE),
        )

        listed = [item["id"] for item in client.get(resources_url(project.pk)).data["resources"]]

        assert listed == [DOMAIN_SITE]

    @responses.activate
    def test_no_sites_is_an_empty_list_not_an_error(self, authorized_project):
        client, _user, project, _connection = authorized_project
        stub_sites()

        response = client.get(resources_url(project.pk))

        assert response.status_code == 200
        assert response.data["resources"] == []

    @responses.activate
    def test_google_failure_is_a_service_error_not_a_state_change(
        self, authorized_project
    ):
        client, _user, project, connection = authorized_project
        stub_sites(status=503)

        response = client.get(resources_url(project.pk))

        assert response.status_code == 503
        assert response.data["error"]["code"] == "resource_unavailable"
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION


# --- Path encoding ----------------------------------------------------------


class TestPathEncoding:
    @responses.activate
    def test_a_url_identifier_is_encoded_into_one_path_segment(
        self, authorized_project
    ):
        """The identifier is a path parameter, so its slashes must not be bare.

        Also a guard on `requests`, which re-quotes URLs it is given: only
        unreserved characters are unquoted on the way through, so %2F survives.
        This design depends on that, so it is asserted rather than assumed.
        """
        client, _user, project, _connection = authorized_project
        stub_site(PREFIX_SITE)

        client.post(selection_url(project.pk), {"resource_id": PREFIX_SITE}, format="json")

        sent = responses.calls[0].request.url
        assert sent == f"{BASE}/sites/https%3A%2F%2Fshop.example.com%2F"
        after_sites = sent.split("/sites/", 1)[1]
        assert "/" not in after_sites
        assert ":" not in after_sites

    @responses.activate
    def test_a_domain_identifier_has_its_colon_encoded(self, authorized_project):
        client, _user, project, _connection = authorized_project
        stub_site(DOMAIN_SITE)

        client.post(selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json")

        assert responses.calls[0].request.url == f"{BASE}/sites/sc-domain%3Aexample.com"

    @responses.activate
    @pytest.mark.parametrize("site_url", [DOMAIN_SITE, PREFIX_SITE])
    def test_both_forms_round_trip_unchanged(self, authorized_project, site_url):
        client, _user, project, connection = authorized_project
        stub_site(site_url)

        client.post(selection_url(project.pk), {"resource_id": site_url}, format="json")

        connection.refresh_from_db()
        assert connection.external_resource_id == site_url
        assert connection.external_resource_label == site_url


# --- The permission rule ----------------------------------------------------


class TestPermissionRule:
    @responses.activate
    @pytest.mark.parametrize(
        "permission_level", ["siteOwner", "siteFullUser", "siteRestrictedUser"]
    )
    def test_levels_that_can_read_are_accepted(
        self, authorized_project, permission_level
    ):
        """Restricted users included: Google grants them the Performance report."""
        client, _user, project, connection = authorized_project
        stub_site(DOMAIN_SITE, permission_level)

        response = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.CONNECTED
        assert connection.external_resource_meta == {"permission_level": permission_level}

    @responses.activate
    @pytest.mark.parametrize(
        "permission_level", ["siteUnverifiedUser", "", "siteSomethingNewGoogleAdds"]
    )
    def test_levels_that_are_not_proven_are_refused(
        self, authorized_project, permission_level
    ):
        """An allowlist: what is not recognized is not permission."""
        client, _user, project, connection = authorized_project
        stub_site(DOMAIN_SITE, permission_level)

        response = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "resource_not_accessible"
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION
        assert connection.external_resource_id == ""

    @responses.activate
    def test_a_missing_permission_level_is_refused(self, authorized_project):
        client, _user, project, connection = authorized_project
        responses.add(
            responses.GET,
            f"{BASE}/sites/sc-domain%3Aexample.com",
            json={"siteUrl": DOMAIN_SITE},
            status=200,
        )

        response = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )

        assert response.status_code == 400
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION

    @responses.activate
    def test_unverified_is_indistinguishable_from_forbidden_and_missing(
        self, authorized_project
    ):
        """Otherwise the endpoint tells a caller which sites exist."""
        client, _user, project, _connection = authorized_project

        stub_site(DOMAIN_SITE, "siteUnverifiedUser")
        unverified = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )
        responses.reset()

        stub_site(DOMAIN_SITE, status=403)
        forbidden = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )
        responses.reset()

        stub_site(DOMAIN_SITE, status=404)
        missing = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )

        assert unverified.status_code == forbidden.status_code == missing.status_code == 400
        assert unverified.data["error"] == forbidden.data["error"] == missing.data["error"]

    @responses.activate
    def test_a_site_that_was_listed_can_still_be_refused_on_verification(
        self, authorized_project
    ):
        """The list is a convenience; the verification call is the authority."""
        client, _user, project, connection = authorized_project
        stub_sites(entry(DOMAIN_SITE, "siteOwner"))
        client.get(resources_url(project.pk))
        responses.reset()
        stub_site(DOMAIN_SITE, "siteUnverifiedUser")

        response = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )

        assert response.status_code == 400
        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION


# --- Identifier validation --------------------------------------------------


class TestIdentifierValidation:
    @responses.activate
    @pytest.mark.parametrize(
        "resource_id",
        [
            "javascript:alert(1)",
            "example.com",
            "sc-domain:../../etc/passwd",
            "sc-domain:",
            "https://example.com",
            "ftp://example.com/",
            "https://example.com/ with space",
            "properties/111",
            "sc-domain:example.com?x=1",
        ],
    )
    def test_malformed_identifiers_never_reach_google(
        self, authorized_project, resource_id
    ):
        client, _user, project, _connection = authorized_project

        response = client.post(
            selection_url(project.pk), {"resource_id": resource_id}, format="json"
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "invalid_resource_id"
        assert len(responses.calls) == 0

    @responses.activate
    def test_an_over_length_identifier_is_stopped_by_the_serializer(
        self, authorized_project
    ):
        """Two bounds, and the outer one fires first.

        The serializer caps the field at the width of the column that stores
        it, so an over-length value is a validation error before the provider
        ever sees it. The provider caps it too (defence in depth), but this
        records which guard actually answers, so a later change to either is
        not mistaken for a regression.
        """
        client, _user, project, _connection = authorized_project

        response = client.post(
            selection_url(project.pk),
            {"resource_id": "https://example.com/" + "a" * 300},
            format="json",
        )

        assert response.status_code == 400
        assert response.data["error"]["code"] == "validation_error"
        assert len(responses.calls) == 0

    def test_the_length_cap_matches_the_column(self):
        from integrations.models import IntegrationConnection as Connection

        field = Connection._meta.get_field("external_resource_id")
        assert search_console.MAX_RESOURCE_ID_LENGTH == field.max_length


# --- Parity with GA4 --------------------------------------------------------


class TestParityWithGa4:
    @responses.activate
    def test_a_label_in_the_request_body_has_no_effect(self, authorized_project):
        client, _user, project, connection = authorized_project
        stub_site(DOMAIN_SITE)

        client.post(
            selection_url(project.pk),
            {
                "resource_id": DOMAIN_SITE,
                "external_resource_label": "Attacker's label",
                "label": "Attacker's label",
                "status": "connected",
            },
            format="json",
        )

        connection.refresh_from_db()
        assert connection.external_resource_label == DOMAIN_SITE

    @responses.activate
    def test_selection_requires_an_authorized_connection(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.SEARCH_CONSOLE,
            status=ConnectionStatus.PENDING_AUTHORIZATION,
        )

        response = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )

        assert response.status_code == 409
        assert len(responses.calls) == 0

    @responses.activate
    def test_changing_to_a_different_site_replaces_the_selection(
        self, authorized_project
    ):
        """Replaces the M5 test that pinned the 409 this milestone lifts (§6)."""
        client, _user, project, connection = authorized_project
        stub_site(DOMAIN_SITE)
        client.post(selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json")
        responses.reset()
        stub_site(PREFIX_SITE)

        response = client.post(
            selection_url(project.pk), {"resource_id": PREFIX_SITE}, format="json"
        )

        assert response.status_code == 200
        connection.refresh_from_db()
        assert connection.external_resource_id == PREFIX_SITE
        assert connection.external_resource_label == PREFIX_SITE
        assert connection.status == ConnectionStatus.CONNECTED

    @responses.activate
    def test_a_failed_change_leaves_the_previous_site_intact(self, authorized_project):
        client, _user, project, connection = authorized_project
        stub_site(DOMAIN_SITE)
        client.post(selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json")
        connection.refresh_from_db()
        before = (
            connection.external_resource_id,
            connection.external_resource_label,
            dict(connection.external_resource_meta),
            connection.last_health_check_at,
            connection.last_successful_check_at,
        )
        responses.reset()
        stub_site(PREFIX_SITE, status=403)

        response = client.post(
            selection_url(project.pk), {"resource_id": PREFIX_SITE}, format="json"
        )

        assert response.status_code == 400
        connection.refresh_from_db()
        assert (
            connection.external_resource_id,
            connection.external_resource_label,
            connection.external_resource_meta,
            connection.last_health_check_at,
            connection.last_successful_check_at,
        ) == before

    @responses.activate
    def test_selection_does_not_reassign_connected_by(self, authorized_project):
        client, user, project, connection = authorized_project
        other = type(user).objects.create_user(
            email="colleague@example.com", password="correct-horse-battery-staple"
        )
        Membership.objects.create(
            workspace=project.workspace, user=other, role=Membership.Role.MEMBER
        )
        connection.connected_by = other
        connection.save(update_fields=["connected_by"])
        stub_site(DOMAIN_SITE)

        client.post(selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json")

        connection.refresh_from_db()
        assert connection.connected_by == other

    @responses.activate
    def test_one_audit_event_records_the_whole_transition(self, authorized_project):
        client, user, project, _connection = authorized_project
        stub_site(DOMAIN_SITE)

        client.post(selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json")

        events = list(AuditEvent.objects.filter(project=project))
        assert len(events) == 1
        assert events[0].action == AuditEvent.Action.INTEGRATION_RESOURCE_SELECTED
        assert events[0].actor == user
        assert events[0].metadata == {
            "provider": "search_console",
            "resource_id": DOMAIN_SITE,
            "resource_label": DOMAIN_SITE,
            "status": "connected",
            "previous_status": "awaiting_resource_selection",
        }

    @responses.activate
    def test_a_rejected_token_writes_no_state(self, authorized_project):
        """Replaces the M5 test that pinned reauth_required here (§4.1, §6).

        Parity with GA4 in the corrected behaviour as much as in the old one:
        the 409 is unchanged, and neither provider's selection path writes a
        verdict about the stored grant from a candidate resource's 401.
        """
        client, _user, project, connection = authorized_project
        before_status = connection.status
        stub_site(DOMAIN_SITE, status=401)

        response = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )

        assert response.status_code == 409
        connection.refresh_from_db()
        assert connection.status == before_status
        assert connection.last_error_code == ""


# --- Provider independence --------------------------------------------------


class TestProviderIndependence:
    @responses.activate
    def test_connecting_search_console_leaves_ga4_untouched(self, authorized_project):
        client, user, project, _connection = authorized_project
        ga4_connection = IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.CONNECTED,
            external_resource_id="properties/549483499",
            external_resource_label="poolino",
            external_resource_meta={"account": "accounts/1"},
            granted_scopes=["https://www.googleapis.com/auth/analytics.readonly"],
            last_health_check_at=timezone.now(),
            last_successful_check_at=timezone.now(),
        )
        before = IntegrationConnection.objects.get(pk=ga4_connection.pk).__dict__.copy()
        stub_site(DOMAIN_SITE)

        client.post(selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json")

        after = IntegrationConnection.objects.get(pk=ga4_connection.pk).__dict__.copy()
        before.pop("_state", None)
        after.pop("_state", None)
        assert before == after


# --- Leakage ----------------------------------------------------------------


class TestNoLeakage:
    @responses.activate
    def test_no_credential_material_in_a_successful_response(self, authorized_project):
        client, _user, project, _connection = authorized_project
        stub_site(DOMAIN_SITE)

        response = client.post(
            selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
        )

        body = json.dumps(response.data)
        assert "access-token-1" not in body
        assert "refresh-token-1" not in body
        assert "test-client-secret" not in body
        assert "external_resource_meta" not in body

    @responses.activate
    def test_googles_error_text_never_reaches_the_response_or_the_log(
        self, authorized_project, caplog
    ):
        client, _user, project, _connection = authorized_project
        stub_site(DOMAIN_SITE, status=403)

        with caplog.at_level("DEBUG"):
            response = client.post(
                selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json"
            )

        leak = "some Google detail that must not leak"
        assert leak not in json.dumps(response.data)
        assert leak not in caplog.text

    @responses.activate
    def test_the_access_token_is_never_logged(self, authorized_project, caplog):
        client, _user, project, _connection = authorized_project
        stub_site(DOMAIN_SITE)

        with caplog.at_level("DEBUG"):
            client.post(selection_url(project.pk), {"resource_id": DOMAIN_SITE}, format="json")

        assert "access-token-1" not in caplog.text


class TestAFailedChangeWritesNothing:
    """§4.1/§6, for the second provider: the rule is not GA4-specific."""

    def _connected(self, connection):
        connection.status = ConnectionStatus.CONNECTED
        connection.external_resource_id = DOMAIN_SITE
        connection.external_resource_label = DOMAIN_SITE
        connection.external_resource_meta = {"permission_level": "siteOwner"}
        connection.last_health_check_at = timezone.now() - timedelta(days=1)
        connection.last_successful_check_at = timezone.now() - timedelta(days=1)
        connection.save()
        connection.refresh_from_db()
        return connection

    def _snapshot(self, connection):
        connection.refresh_from_db()
        return (
            connection.status,
            connection.external_resource_id,
            connection.external_resource_label,
            dict(connection.external_resource_meta),
            connection.last_health_check_at,
            connection.last_successful_check_at,
            connection.last_error_code,
            connection.last_error_message,
            connection.updated_at,
        )

    @responses.activate
    def test_a_401_verifying_the_candidate_leaves_everything_unchanged(
        self, authorized_project
    ):
        client, _user, project, connection = authorized_project
        self._connected(connection)
        before = self._snapshot(connection)
        stub_site(PREFIX_SITE, status=401)

        response = client.post(
            selection_url(project.pk), {"resource_id": PREFIX_SITE}, format="json"
        )

        assert response.status_code == 409
        assert response.data["error"]["code"] == "credential_refresh_failed"
        assert self._snapshot(connection) == before
