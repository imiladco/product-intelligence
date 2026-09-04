"""Concurrency: the three fences, and every race the design names.

Three separate mechanisms are exercised here and never merged (design §9.6):

* ``lifecycle_generation`` — is this authorization still the current intent?
* ``Fence`` — is this provider result about the state it was computed from?
* ``RefreshFence`` — is this refresh result about the credential it came from?

Interleaving is produced by stubbing the *outbound call* to perform the
competing mutation before it returns: deterministic, no threads, no sleeps. The
one exception is the connection-creation race, which needs real concurrency and
uses threads under ``transaction=True``.
"""

from __future__ import annotations

import pytest

from integrations.concurrency import (
    locked_existing_connection,
    locked_or_create_connection_for_authorization,
)
from datetime import timedelta

from django.utils import timezone

from audit.models import AuditEvent
from integrations.models import (
    IntegrationConnection,
    IntegrationCredential,
    OAuthAuthorizationRequest,
)
from integrations.providers import get_provider
from integrations.oauth_service import start_authorization
from integrations.providers import ProviderKey
from integrations.status import ConnectionStatus

pytestmark = pytest.mark.django_db


class TestGenerationFields:
    """The counter exists and starts where the migration says it does."""

    def test_new_connection_starts_at_generation_zero(self, make_user_with_workspace, make_project):
        _user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        connection = IntegrationConnection.objects.create(
            project=project, provider=ProviderKey.GA4
        )

        assert connection.lifecycle_generation == 0

    def test_new_authorization_request_defaults_to_generation_zero(
        self, make_user_with_workspace, make_project
    ):
        """Rows predating the migration default to 0 on both sides (§11.2)."""
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        request = OAuthAuthorizationRequest.objects.create(
            state_hash="a" * 64,
            project=project,
            provider=ProviderKey.GA4,
            user=user,
            expires_at="2030-01-01T00:00:00Z",
        )

        assert request.connection_generation == 0


class TestGenerationAssignment:
    """start_authorization advances the counter, atomically, every time."""

    def test_start_authorization_advances_the_generation(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        connection = IntegrationConnection.objects.create(
            project=project, provider=ProviderKey.GA4
        )

        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)

        connection.refresh_from_db()
        assert connection.lifecycle_generation == 1

    def test_request_carries_the_generation_it_was_assigned(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)

        connection = IntegrationConnection.objects.get(project=project)
        request = OAuthAuthorizationRequest.objects.get(project=project)
        assert request.connection_generation == connection.lifecycle_generation

    def test_two_starts_on_an_existing_connection_get_distinct_generations(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        IntegrationConnection.objects.create(project=project, provider=ProviderKey.GA4)

        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)
        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)

        generations = sorted(
            OAuthAuthorizationRequest.objects.filter(project=project).values_list(
                "connection_generation", flat=True
            )
        )
        assert generations == [1, 2]

    def test_connection_holds_the_newest_generation(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)
        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)

        connection = IntegrationConnection.objects.get(project=project)
        newest = max(
            OAuthAuthorizationRequest.objects.filter(project=project).values_list(
                "connection_generation", flat=True
            )
        )
        assert connection.lifecycle_generation == newest

    def test_generation_advances_even_from_pending_authorization(
        self, make_user_with_workspace, make_project
    ):
        """The counter tracks intent, not state changes (§9.4).

        A start from pending_authorization changes no durable field, and must
        still advance — the same principle that makes a repeat disconnect
        non-inert.
        """
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        connection = IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.PENDING_AUTHORIZATION,
        )

        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.PENDING_AUTHORIZATION
        assert connection.lifecycle_generation == 1

    def test_start_authorization_does_not_destroy_durable_state(
        self, make_user_with_workspace, make_project
    ):
        """M3's rule: starting an authorization writes no status (§9.4)."""
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        connection = IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.CONNECTED,
            external_resource_id="properties/1",
            external_resource_label="Kept",
        )

        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.CONNECTED
        assert connection.external_resource_id == "properties/1"
        assert connection.external_resource_label == "Kept"


class TestConnectionAcquisition:
    """Only an authorization start may create a connection (§9.4.1a)."""

    def test_locked_existing_connection_never_creates(
        self, make_user_with_workspace, make_project
    ):
        _user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        with pytest.raises(IntegrationConnection.DoesNotExist):
            locked_existing_connection(project, ProviderKey.GA4)

        assert not IntegrationConnection.objects.exists()

    def test_first_authorization_creates_the_connection_and_advances_generation(
        self, make_user_with_workspace, make_project
    ):
        """The path a split T02/T03 would have broken."""
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)

        connection = IntegrationConnection.objects.get(project=project)
        assert connection.status == ConnectionStatus.PENDING_AUTHORIZATION
        assert connection.lifecycle_generation == 1
        assert (
            OAuthAuthorizationRequest.objects.get(project=project).connection_generation
            == 1
        )

    def test_creation_race_recovery_branch(
        self, monkeypatch, make_user_with_workspace, make_project
    ):
        """No row observed -> create loses the unique race -> locked get wins.

        The row genuinely exists; only the initial lookup is forced to miss it,
        so the IntegrityError comes from the real unique constraint and the
        recovery has a real winner to find. A patched exception alone would
        leave nothing for the recovery get() to select.
        """
        from integrations import concurrency

        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        existing = IntegrationConnection.objects.create(
            project=project, provider=ProviderKey.GA4, lifecycle_generation=7
        )

        lookups = {"n": 0}
        creates = {"n": 0}
        real_lookup = concurrency._existing_locked
        real_create = IntegrationConnection.objects.create

        def blind_once(*args, **kwargs):
            lookups["n"] += 1
            if lookups["n"] == 1:
                return None  # force the no-row branch
            return real_lookup(*args, **kwargs)

        def counted_create(*args, **kwargs):
            # Delegates to the real create, so the IntegrityError comes from
            # the real unique constraint rather than a fabricated exception.
            creates["n"] += 1
            return real_create(*args, **kwargs)

        monkeypatch.setattr(concurrency, "_existing_locked", blind_once)
        monkeypatch.setattr(IntegrationConnection.objects, "create", counted_create)

        connection = locked_or_create_connection_for_authorization(
            project, ProviderKey.GA4, user=user
        )

        # The create branch was genuinely attempted and genuinely lost...
        assert creates["n"] == 1
        # ...and the recovery selected the row that was there all along.
        assert connection.pk == existing.pk
        assert connection.lifecycle_generation == 7
        assert IntegrationConnection.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_first_authorizations_race(django_db_setup, django_db_blocker):
    """Two genuine first Connects, concurrently, with no connection row.

    The deterministic test above forces the recovery branch; this one proves
    Postgres actually serializes the insert, which no amount of patching can
    show. Real threads, real connections, real unique constraint.
    """
    import threading

    from django.contrib.auth import get_user_model
    from django.db import connection as db_connection

    from projects.models import Project
    from workspaces.services import create_initial_workspace

    User = get_user_model()

    with django_db_blocker.unblock():
        user = User.objects.create_user(email="race@example.com", password="x" * 20)
        workspace = create_initial_workspace(user)
        project = Project.objects.create(
            workspace=workspace, name="Race", website_url="https://race.example"
        )

    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def start() -> None:
        try:
            barrier.wait(timeout=10)
            start_authorization(user=user, project=project, provider_key=ProviderKey.GA4)
        except BaseException as exc:  # noqa: BLE001 - recorded and re-raised below
            errors.append(exc)
        finally:
            db_connection.close()

    threads = [threading.Thread(target=start) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    try:
        assert errors == [], f"a start_authorization raised: {errors!r}"

        # Exactly one connection row, despite two concurrent creators.
        assert IntegrationConnection.objects.filter(project=project).count() == 1
        connection = IntegrationConnection.objects.get(project=project)

        generations = sorted(
            OAuthAuthorizationRequest.objects.filter(project=project).values_list(
                "connection_generation", flat=True
            )
        )
        # Distinct generations, and the connection holds the newest, so only
        # the newest request could finalize.
        assert generations == [1, 2]
        assert connection.lifecycle_generation == 2
    finally:
        with django_db_blocker.unblock():
            OAuthAuthorizationRequest.objects.all().delete()
            IntegrationConnection.objects.all().delete()
            Project.objects.all().delete()
            User.objects.all().delete()


class TestSelectionFence:
    """A selection result may only be applied to the state it was computed from.

    Design §9.3. Captured after any refresh has committed and before the
    provider call; compared under the write lock. A mismatch discards
    everything, including timestamps — a stale result has no claim on any field.
    """

    def _authorized(self, project, user):
        connection = IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.AWAITING_RESOURCE_SELECTION,
            granted_scopes=["https://www.googleapis.com/auth/analytics.readonly"],
            connected_by=user,
        )
        IntegrationCredential.objects.create(
            connection=connection,
            access_token="access-token-1",
            refresh_token="refresh-token-1",
            access_token_expires_at=timezone.now() + timedelta(hours=1),
        )
        return connection

    def test_stale_selection_result_is_discarded(
        self, monkeypatch, make_user_with_workspace, make_project
    ):
        from integrations import resource_service
        from integrations.resources import RemoteResource

        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        connection = self._authorized(project, user)

        def mutate_then_return(access_token, resource_id):
            # Something else writes to the connection mid-flight.
            IntegrationConnection.objects.filter(pk=connection.pk).update(
                external_resource_id="properties/999",
                external_resource_label="Newer",
            )
            return RemoteResource(id=resource_id, label="Stale", metadata={})

        monkeypatch.setattr(
            resource_service, "_verify", mutate_then_return, raising=False
        )
        catalog = get_provider(ProviderKey.GA4).resources
        monkeypatch.setattr(catalog, "verify_resource", mutate_then_return)

        resource_service.select_resource(
            user=user,
            project=project,
            provider_key=ProviderKey.GA4,
            resource_id="properties/111",
        )

        connection.refresh_from_db()
        # The newer state survives; the stale result wrote nothing.
        assert connection.external_resource_id == "properties/999"
        assert connection.external_resource_label == "Newer"
        assert connection.status == ConnectionStatus.AWAITING_RESOURCE_SELECTION

    def test_a_discarded_selection_writes_nothing_at_all(
        self, monkeypatch, make_user_with_workspace, make_project
    ):
        """Not even a timestamp. 'Discard but still stamp' is the tempting bug."""
        from integrations import resource_service
        from integrations.resources import RemoteResource

        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        connection = self._authorized(project, user)

        def mutate_then_return(access_token, resource_id):
            # Written the way production writes: save() with updated_at in
            # update_fields, so auto_now fires. queryset.update() would bypass
            # it, which no service path does.
            newer = IntegrationConnection.objects.get(pk=connection.pk)
            newer.last_error_message = "newer write"
            newer.save(update_fields=["last_error_message", "updated_at"])
            return RemoteResource(id=resource_id, label="Stale", metadata={})

        catalog = get_provider(ProviderKey.GA4).resources
        monkeypatch.setattr(catalog, "verify_resource", mutate_then_return)

        resource_service.select_resource(
            user=user,
            project=project,
            provider_key=ProviderKey.GA4,
            resource_id="properties/111",
        )

        connection.refresh_from_db()
        assert connection.external_resource_id == ""
        assert connection.external_resource_label == ""
        assert connection.external_resource_meta == {}
        assert connection.last_health_check_at is None
        assert connection.last_successful_check_at is None
        assert not AuditEvent.objects.filter(
            action=AuditEvent.Action.INTEGRATION_RESOURCE_SELECTED
        ).exists()


class TestHealthCheckFence:
    """A health-check verdict may only be applied to the state it is about.

    Design §9.5 races A and B. Both stubs make the world move *while the
    provider is answering*, which is exactly the window a fence exists for: the
    verdict that comes back is true about a connection that no longer exists.
    """

    def _connected(self, project, user):
        connection = IntegrationConnection.objects.create(
            project=project,
            provider=ProviderKey.GA4,
            status=ConnectionStatus.CONNECTED,
            external_resource_id="properties/111",
            external_resource_label="poolino",
            granted_scopes=["https://www.googleapis.com/auth/analytics.readonly"],
            connected_by=user,
        )
        IntegrationCredential.objects.create(
            connection=connection,
            access_token="access-token-1",
            refresh_token="refresh-token-1",
            access_token_expires_at=timezone.now() + timedelta(hours=1),
        )
        return connection

    def test_stale_provider_401_after_reconnect_is_discarded(
        self, monkeypatch, make_user_with_workspace, make_project
    ):
        """Race A: a 401 about a credential that has since been replaced."""
        from integrations import lifecycle_service
        from integrations.google.errors import CredentialRefreshFailed

        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        connection = self._connected(project, user)

        def reconnect_then_reject(access_token, resource_id):
            # A reconnect lands: new credential material, connection touched.
            credential = IntegrationCredential.objects.get(connection=connection)
            credential.access_token = "access-token-2"
            credential.refresh_token = "refresh-token-2"
            credential.save(
                update_fields=["access_token", "refresh_token", "updated_at"]
            )
            raise CredentialRefreshFailed

        catalog = get_provider(ProviderKey.GA4).resources
        monkeypatch.setattr(catalog, "verify_resource", reconnect_then_reject)

        lifecycle_service.health_check(project=project, provider_key=ProviderKey.GA4)

        connection.refresh_from_db()
        # The repaired connection is untouched — not even a health timestamp.
        assert connection.status == ConnectionStatus.CONNECTED
        assert connection.last_error_code == ""
        assert connection.last_health_check_at is None

    def test_stale_403_after_resource_change_is_discarded(
        self, monkeypatch, make_user_with_workspace, make_project
    ):
        """Race B: a 403 about a resource this connection no longer points at."""
        from integrations import lifecycle_service
        from integrations.google.errors import ResourceNotAccessible

        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        connection = self._connected(project, user)

        def change_resource_then_reject(access_token, resource_id):
            newer = IntegrationConnection.objects.get(pk=connection.pk)
            newer.external_resource_id = "properties/222"
            newer.external_resource_label = "Newer"
            newer.save(
                update_fields=[
                    "external_resource_id",
                    "external_resource_label",
                    "updated_at",
                ]
            )
            raise ResourceNotAccessible

        catalog = get_provider(ProviderKey.GA4).resources
        monkeypatch.setattr(catalog, "verify_resource", change_resource_then_reject)

        lifecycle_service.health_check(project=project, provider_key=ProviderKey.GA4)

        connection.refresh_from_db()
        assert connection.status == ConnectionStatus.CONNECTED
        assert connection.external_resource_id == "properties/222"
        assert connection.last_error_code == ""
        assert connection.last_health_check_at is None
