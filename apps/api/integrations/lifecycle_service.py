"""Operating an integration after it exists: checking it, and writing verdicts.

Two things live here and nothing else does them:

* ``health_check`` — asking the provider, on demand, whether the stored
  selection is still usable by the stored credential.
* ``apply_verification_outcome`` — the single writer that turns a
  ``VerificationOutcome`` into connection state, for every context that
  produces one.

Neither names a provider. The catalog comes from the provider registry, the
outcome vocabulary is provider-neutral, and the status table below switches on
``VerificationResult`` alone.
"""

from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_event

from . import verification
from .concurrency import Fence, advance_generation, locked_existing_connection
from .google.credentials import access_token_for
from .google.errors import (
    CredentialMissing,
    CredentialRefreshFailed,
    GoogleApiError,
    ResourceMissing,
    ResourceSelectionUnsupported,
    ResourceUnavailable,
)
from .models import (
    IntegrationConnection,
    IntegrationCredential,
    OAuthAuthorizationRequest,
)
from .providers import get_provider
from .resources import ResourceCatalog
from .status import ConnectionStatus
from .verification import (
    VerificationContext,
    VerificationOutcome,
    VerificationResult,
)

#: The (context, result) status table — the single place §4.3 and §5.1.1 live.
#: ``None`` means "leave the durable status exactly as it is": a transient
#: failure during a health check is not evidence about the connection, and
#: recording one would turn a network blip into a state change the user sees.
_STATUS_TABLE: dict[
    tuple[VerificationContext, VerificationResult], ConnectionStatus | None
] = {
    (VerificationContext.HEALTH_CHECK, VerificationResult.SUCCESS): (
        ConnectionStatus.CONNECTED
    ),
    (VerificationContext.HEALTH_CHECK, VerificationResult.RESOURCE_NOT_ACCESSIBLE): (
        ConnectionStatus.ERROR
    ),
    (VerificationContext.HEALTH_CHECK, VerificationResult.CREDENTIAL_REJECTED): (
        ConnectionStatus.REAUTH_REQUIRED
    ),
    (VerificationContext.HEALTH_CHECK, VerificationResult.TRANSIENT): None,
    (VerificationContext.RECONNECT, VerificationResult.SUCCESS): (
        ConnectionStatus.CONNECTED
    ),
    (VerificationContext.RECONNECT, VerificationResult.RESOURCE_NOT_ACCESSIBLE): (
        ConnectionStatus.AWAITING_RESOURCE_SELECTION
    ),
    (VerificationContext.RECONNECT, VerificationResult.CREDENTIAL_REJECTED): (
        ConnectionStatus.REAUTH_REQUIRED
    ),
    (VerificationContext.RECONNECT, VerificationResult.TRANSIENT): (
        ConnectionStatus.AWAITING_RESOURCE_SELECTION
    ),
}


def _catalog(provider_key: str) -> ResourceCatalog:
    """This provider's resource catalog, or the reason there is none."""
    provider = get_provider(provider_key)
    if provider is None or provider.resources is None:
        raise ResourceSelectionUnsupported
    return provider.resources


def _checkable_connection(project, provider_key: str) -> IntegrationConnection:
    """The connection a check can run against, or the reason it cannot start.

    Never creates a row. The two failures are told apart deliberately: nothing
    to authorize with, and nothing to check — a user can act on each, and only
    one of them is fixed by connecting again.
    """
    connection = IntegrationConnection.objects.filter(
        project=project, provider=provider_key
    ).first()
    if connection is None:
        raise CredentialMissing

    try:
        credential = connection.credential
    except ObjectDoesNotExist:
        credential = None
    if credential is None or not credential.refresh_token:
        raise CredentialMissing

    if not connection.external_resource_id:
        raise ResourceMissing
    return connection


def health_check(*, project, provider_key: str) -> IntegrationConnection:
    """Ask the provider whether the stored selection still works, and record it.

    The resource identifier comes from the database, never from the request:
    a health check verifies what the connection actually points at, and no
    request body can redirect it at something else.
    """
    catalog = _catalog(provider_key)
    connection = _checkable_connection(project, provider_key)

    outcome: VerificationOutcome | None = None
    try:
        access_token: str | None = access_token_for(connection)
    except CredentialMissing:
        # The check never began, so there is no outcome to report (§3.1).
        raise
    except (CredentialRefreshFailed, ResourceUnavailable, GoogleApiError) as exc:
        # The check *completed*, with the answer "this credential is dead" or
        # "Google could not be reached". Both are results, not failures to run.
        access_token = None
        outcome = verification.outcome_from_lifecycle_error(exc)

    # After token acquisition, whatever it did: a failed refresh may already
    # have written status through mark_reauth_required, and the fence must
    # describe the state as it is now rather than fencing out this very check.
    connection.refresh_from_db()
    fence = Fence.capture(connection)

    if access_token is not None:
        outcome = verification.verify(
            catalog=catalog,
            access_token=access_token,
            resource_id=connection.external_resource_id,
        )

    return apply_verification_outcome(
        connection=connection,
        outcome=outcome,
        fence=fence,
        context=VerificationContext.HEALTH_CHECK,
    )


def apply_verification_outcome(
    *,
    connection: IntegrationConnection,
    outcome: VerificationOutcome,
    fence: Fence,
    context: VerificationContext,
    expected_generation: int | None = None,
) -> IntegrationConnection:
    """Write a verification verdict to the connection, or write nothing at all.

    Two guards run under the row lock and before any write. A generation
    mismatch means this verdict belongs to an intent the user has since
    replaced; a fence mismatch means it describes a state that no longer
    exists. Either way the result is discarded whole — not even a timestamp,
    because a stale result has no claim on any field — and the connection as it
    actually is now is returned.

    **The three selection fields are never written here**, on any result
    including success. A check is not a selection: ``_persist_selection``
    remains the only writer of ``external_resource_id``,
    ``external_resource_label`` and ``external_resource_meta``, and the absence
    of those names from ``update_fields`` is what makes that structural.
    """
    with transaction.atomic():
        locked = locked_existing_connection(connection.project, connection.provider)

        if (
            expected_generation is not None
            and locked.lifecycle_generation != expected_generation
        ):
            return locked
        if not fence.matches(locked):
            return locked

        now = timezone.now()
        status = _STATUS_TABLE[(context, outcome.result)]
        if status is not None:
            locked.status = status

        locked.last_health_check_at = now
        if outcome.succeeded:
            locked.last_successful_check_at = now
            locked.last_error_code = ""
            locked.last_error_message = ""
        else:
            locked.last_error_code = outcome.error_code
            locked.last_error_message = outcome.error_message

        locked.save(
            update_fields=[
                "status",
                "last_health_check_at",
                "last_successful_check_at",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )
    return locked


def disconnect(*, user, project, provider_key: str) -> IntegrationConnection | None:
    """End this integration locally, without touching the Google grant.

    Google's revoke endpoint is deliberately never called (§3.2). The grant
    belongs to the user's Google account and one consent can cover more than
    this connection; revoking it here would silently break authorizations this
    project knows nothing about. Disconnecting removes what *we* hold.

    Idempotent in what it means, not in what it touches (§9.1). A second
    disconnect finds the meaningful result already true — no credential, status
    ``disconnected`` — and writes no audit event for a transition that did not
    happen. It still advances the generation and consumes outstanding
    authorization requests, because a callback may have started in between and
    must not be allowed to land on a connection the user has since ended.

    Returns None when there is no connection row: already not connected, with
    nothing to end. Creating one in order to mark it disconnected would be a
    row the user never asked for.
    """
    with transaction.atomic():
        try:
            connection = locked_existing_connection(project, provider_key)
        except IntegrationConnection.DoesNotExist:
            return None

        previous_status = connection.status
        advance_generation(connection)

        # Deleted, not blanked: an all-empty credential row still reads as
        # "a credential exists" to every caller that checks for one.
        IntegrationCredential.objects.filter(connection=connection).delete()

        # Unconditional, and the part of a repeat disconnect that is not inert.
        OAuthAuthorizationRequest.objects.filter(
            project=project,
            provider=provider_key,
            consumed_at__isnull=True,
        ).update(consumed_at=timezone.now())

        if previous_status != ConnectionStatus.DISCONNECTED:
            connection.status = ConnectionStatus.DISCONNECTED
            connection.last_error_code = ""
            connection.last_error_message = ""
            connection.save(
                update_fields=[
                    "status",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )
            # The selection and last_successful_check_at are deliberately kept:
            # reconnecting restores what the user chose rather than asking
            # again, and the history of the connection is not a secret to erase.
            record_event(
                action=AuditEvent.Action.INTEGRATION_DISCONNECTED,
                actor=user,
                project=project,
                provider=provider_key,
                metadata={
                    "provider": provider_key,
                    "status": ConnectionStatus.DISCONNECTED,
                    "previous_status": previous_status,
                },
            )
    return connection
