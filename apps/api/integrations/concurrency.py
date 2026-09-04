"""Concurrency primitives for the integration lifecycle.

Three separate mechanisms guard three different questions, and they are
deliberately **not** merged (design §9.6):

* ``lifecycle_generation`` — is this authorization still the user's current
  *intent*?  An integer on the connection, advanced by the two operations that
  express a new intent, and compared for equality at callback finalization.
* ``Fence`` — is this provider result about the state it was computed from?  A
  snapshot captured before an outbound call and compared under the write lock.
* ``RefreshFence`` — the same question for a token refresh.  It lives with the
  refresh in ``google/credentials.py`` because that is the only code allowed to
  answer it.

The first two live here.  Merging them would be a mistake: a snapshot cannot
express "an explicit end-of-life happened after this attempt began", because
that question spans two tables and two lifetimes, and a counter cannot express
"the row moved under me" without becoming a second `updated_at`.

Acquiring a connection is split into two functions on purpose.  **Only an
authorization start may bring a connection into existence.**  A disconnect that
created the row it is ending, or a stale callback that resurrected a deleted
one, would both be defects; giving creation its own long, explicit name makes
them hard to write by accident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ObjectDoesNotExist
from django.db import IntegrityError, transaction

from .models import IntegrationConnection
from .status import ConnectionStatus


def _existing_locked(project, provider_key: str) -> IntegrationConnection | None:
    """Lock and return the existing connection, or None.

    A named seam rather than an inline query: a test forces the no-row branch
    through it while the row genuinely exists, which is the only way to
    exercise the creation race deterministically against the real constraint.
    """
    return (
        IntegrationConnection.objects.select_for_update()
        .filter(project=project, provider=provider_key)
        .first()
    )


def locked_existing_connection(project, provider_key: str) -> IntegrationConnection:
    """Lock and return an existing connection. **Never creates one.**

    Raises ``IntegrationConnection.DoesNotExist``; each caller maps that to its
    own contract. This is what every lifecycle operation except an
    authorization start uses.
    """
    return IntegrationConnection.objects.select_for_update().get(
        project=project, provider=provider_key
    )


def locked_or_create_connection_for_authorization(
    project, provider_key: str, *, user
) -> IntegrationConnection:
    """Lock the connection, creating it if this is a first authorization.

    The only creating entry point in the codebase, and its name says so.

    ``select_for_update`` cannot lock a row that does not exist, so two
    simultaneous first Connects have nothing to serialize on until one of them
    has inserted. The existing unique constraint on (project, provider) is that
    serialization point, and the row lock takes over from the moment the row
    exists (§9.4.1a).
    """
    connection = _existing_locked(project, provider_key)
    if connection is not None:
        return connection

    try:
        # A savepoint: a unique violation must not poison the outer
        # transaction, which still has an authorization request to create.
        with transaction.atomic():
            return IntegrationConnection.objects.create(
                project=project,
                provider=provider_key,
                status=ConnectionStatus.PENDING_AUTHORIZATION,
                connected_by=user,
                # lifecycle_generation keeps its default; the caller advances
                # it, so created and existing rows share one code path.
            )
    except IntegrityError:
        # Lost the insert race. Postgres blocked this INSERT on the unique
        # index until the winner committed, so the row is visible to a fresh
        # statement now, and select_for_update serializes the rest.
        return locked_existing_connection(project, provider_key)


def advance_generation(connection: IntegrationConnection) -> int:
    """Advance the connection's lifecycle generation and return the new value.

    Must be called with the row already locked by the caller's transaction.
    Read-increment-save rather than ``F()``: under the lock there is no lost
    update to avoid, and ``F()`` would not hand back the assigned value —
    learning it afterwards reopens the very window the lock closes (§9.4.1).
    """
    connection.lifecycle_generation += 1
    connection.save(update_fields=["lifecycle_generation", "updated_at"])
    return connection.lifecycle_generation


@dataclass(frozen=True)
class Fence:
    """What an outbound provider result is about.

    Captured immediately before the call — and after any refresh has committed,
    so an operation never fences out its own refresh — then compared under the
    write lock. A mismatch means the world moved, and the result describes a
    state that no longer exists: discard it entirely, including timestamps.

    The last two fields are redundant with ``connection_updated_at`` while every
    save lists ``updated_at`` in ``update_fields``. They are here because that
    is a convention, and one forgetful save would otherwise disable the whole
    fence silently.
    """

    connection_updated_at: datetime
    external_resource_id: str
    credential_updated_at: datetime | None

    @classmethod
    def capture(cls, connection: IntegrationConnection) -> "Fence":
        return cls(
            connection_updated_at=connection.updated_at,
            external_resource_id=connection.external_resource_id,
            credential_updated_at=_credential_updated_at(connection),
        )

    def matches(self, connection: IntegrationConnection) -> bool:
        return self == Fence.capture(connection)


def _credential_updated_at(connection: IntegrationConnection) -> datetime | None:
    """The credential's updated_at, or None when there is no credential.

    None is a meaningful value: a disconnect deletes the row, and a result that
    returns afterwards must see that as a mismatch rather than a crash.
    """
    try:
        return connection.credential.updated_at
    except ObjectDoesNotExist:
        return None
