"""Audit integrity.

The audit table exists to answer tenant questions ("who connected what, in
which workspace"), so a row that misattributes an action to the wrong tenant
would be worse than no row. It also must never become a place secrets land.
"""

from __future__ import annotations

import json

import pytest

from audit.models import AuditEvent
from audit.services import (
    ALLOWED_METADATA_KEYS,
    AuditIntegrityError,
    filter_metadata,
    record_event,
)
from workspaces.services import create_workspace

pytestmark = pytest.mark.django_db


class TestWorkspaceProjectIntegrity:
    def test_workspace_is_derived_from_the_project(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        event = record_event(
            action=AuditEvent.Action.INTEGRATION_AUTHORIZED, actor=user, project=project
        )
        assert event.workspace == workspace

    def test_mismatched_workspace_and_project_is_refused(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)
        other_workspace = create_workspace(name="Other", owner=user)

        with pytest.raises(AuditIntegrityError, match="does not own project"):
            record_event(
                action=AuditEvent.Action.INTEGRATION_AUTHORIZED,
                actor=user,
                project=project,
                workspace=other_workspace,
            )
        assert not AuditEvent.objects.exists()

    def test_matching_workspace_and_project_is_accepted(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        project = make_project(workspace)

        event = record_event(
            action=AuditEvent.Action.INTEGRATION_AUTHORIZED,
            actor=user,
            project=project,
            workspace=workspace,
        )
        assert event.workspace == workspace

    def test_a_workspace_only_event_is_allowed(self, make_user_with_workspace):
        user, workspace = make_user_with_workspace()
        event = record_event(
            action=AuditEvent.Action.INTEGRATION_AUTHORIZED,
            actor=user,
            workspace=workspace,
        )
        assert event.project is None

    def test_neither_workspace_nor_project_is_refused(self, make_user_with_workspace):
        user, _workspace = make_user_with_workspace()
        with pytest.raises(AuditIntegrityError):
            record_event(action=AuditEvent.Action.INTEGRATION_AUTHORIZED, actor=user)

    def test_every_stored_event_is_internally_consistent(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        record_event(
            action=AuditEvent.Action.INTEGRATION_AUTHORIZED,
            actor=user,
            project=make_project(workspace),
        )
        for event in AuditEvent.objects.exclude(project=None):
            assert event.project.workspace_id == event.workspace_id


class TestMetadataAllowlist:
    @pytest.mark.parametrize(
        "key",
        [
            "access_token",
            "refresh_token",
            "code",
            "authorization_code",
            "state",
            "client_secret",
            "code_verifier",
            "id_token",
        ],
    )
    def test_secret_bearing_keys_are_dropped(self, key):
        assert filter_metadata({key: "super-secret-value", "provider": "ga4"}) == {
            "provider": "ga4"
        }

    def test_only_allowlisted_keys_survive(self):
        assert set(filter_metadata({key: "v" for key in ALLOWED_METADATA_KEYS})) == set(
            ALLOWED_METADATA_KEYS
        )

    def test_non_scalar_values_are_dropped(self):
        """A nested structure could smuggle a token past a key-name check."""
        assert filter_metadata({"provider": {"nested": "token"}}) == {}
        assert filter_metadata({"provider": ["token"]}) == {}

    def test_allowlist_contains_no_secret_bearing_name(self):
        for key in ALLOWED_METADATA_KEYS:
            assert not any(
                bad in key for bad in ("token", "secret", "code_verifier", "state")
            ) or key in {"error_code"}

    def test_record_event_filters_metadata(self, make_user_with_workspace, make_project):
        user, workspace = make_user_with_workspace()
        event = record_event(
            action=AuditEvent.Action.INTEGRATION_AUTHORIZED,
            actor=user,
            project=make_project(workspace),
            metadata={
                "provider": "ga4",
                "access_token": "ya29.secret",
                "state": "oauth-state",
            },
        )
        event.refresh_from_db()
        serialized = json.dumps(event.metadata)
        assert "ya29.secret" not in serialized
        assert "oauth-state" not in serialized
        assert event.metadata == {"provider": "ga4"}

    def test_no_stored_audit_event_contains_a_secret_shaped_key(
        self, make_user_with_workspace, make_project
    ):
        user, workspace = make_user_with_workspace()
        record_event(
            action=AuditEvent.Action.INTEGRATION_AUTHORIZED,
            actor=user,
            project=make_project(workspace),
            metadata={"provider": "ga4", "refresh_token": "leak"},
        )
        for event in AuditEvent.objects.all():
            assert set(event.metadata) <= ALLOWED_METADATA_KEYS
