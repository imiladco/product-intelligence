"""The health endpoint, and the release it reports.

The deploy health gate asserts that a hostname serves the release it was just
given (design §10.4, §13.3). Container health cannot prove that — a request has
to come back naming the release that answered it. That is the only reason this
field exists, so it carries a commit SHA and nothing else.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

HEALTH = "/api/health"
SHA_A = "a" * 40
SHA_B = "b" * 40


@pytest.fixture
def client() -> APIClient:
    return APIClient()


class TestReleaseReporting:
    def test_health_reports_the_release_from_the_environment(self, client, monkeypatch):
        monkeypatch.setenv("RELEASE_SHA", SHA_A)

        response = client.get(HEALTH)

        assert response.status_code == 200
        assert response.data["release"] == SHA_A

    def test_health_reports_unknown_when_unset(self, client, monkeypatch):
        monkeypatch.delenv("RELEASE_SHA", raising=False)

        response = client.get(HEALTH)

        assert response.data["release"] == "unknown"

    def test_health_reports_unknown_when_empty(self, client, monkeypatch):
        # An unset variable and one set to "" are the same fact: nobody told us
        # which release this is. Reporting "" would make the gate compare
        # against an empty string and pass on a connection that proves nothing.
        monkeypatch.setenv("RELEASE_SHA", "")

        response = client.get(HEALTH)

        assert response.data["release"] == "unknown"

    def test_release_is_read_per_request(self, client, monkeypatch):
        """Read at request time, not at import.

        A container restarted with a new RELEASE_SHA must report the new one
        without a rebuild. Reading at import would freeze the value at the
        first worker start and make the health gate assert a stale truth.
        """
        monkeypatch.setenv("RELEASE_SHA", SHA_A)
        assert client.get(HEALTH).data["release"] == SHA_A

        monkeypatch.setenv("RELEASE_SHA", SHA_B)
        assert client.get(HEALTH).data["release"] == SHA_B


class TestPayloadBoundaries:
    def test_health_carries_no_other_configuration(self, client, monkeypatch):
        """A public unauthenticated endpoint: three keys, and no more.

        Widening this payload is how an unauthenticated diagnostic becomes a
        configuration disclosure.
        """
        monkeypatch.setenv("RELEASE_SHA", SHA_A)

        response = client.get(HEALTH)

        assert set(response.data) == {"status", "database", "release"}

    def test_the_payload_carries_nothing_credential_shaped(self, client, monkeypatch):
        monkeypatch.setenv("RELEASE_SHA", SHA_A)

        body = str(client.get(HEALTH).data).lower()

        for forbidden in ("secret", "token", "password", "postgres://", "database_url"):
            assert forbidden not in body

    def test_the_release_is_reported_when_the_database_is_down(
        self, client, monkeypatch
    ):
        """The gate must be able to tell *which* release is unhealthy.

        A 503 with no release leaves an operator unable to say whether the
        candidate or the previous release is the one failing.
        """
        from django.db import connection

        monkeypatch.setenv("RELEASE_SHA", SHA_A)

        def explode():
            raise RuntimeError("database is down")

        monkeypatch.setattr(connection, "cursor", explode)

        response = client.get(HEALTH)

        assert response.status_code == 503
        assert response.data["status"] == "error"
        assert response.data["release"] == SHA_A


class TestExistingContract:
    """M3 behaviour the container healthcheck depends on. Unchanged by M7."""

    def test_health_needs_no_authentication(self, client):
        response = client.get(HEALTH)
        assert response.status_code == 200
        assert response.data["status"] == "ok"
        assert response.data["database"] == "ok"
