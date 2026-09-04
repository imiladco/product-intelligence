"""Starting and completing a Google authorization.

The security-critical rules live here, in one place:

* OAuth ``state`` is 32 random bytes; only its SHA-256 hash is stored.
* An authorization request is single-use and consumed under a row lock, so two
  concurrent callbacks cannot both succeed.
* The callback trusts the *stored* request for project and provider, never the
  query string.
* Membership is re-checked at callback time. Being authorized when the flow
  started does not mean still being authorized when it finishes.
* A token response without a refresh token never overwrites a stored one.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEvent
from audit.services import record_event
from projects.selectors import projects_for_user

from .google.errors import (
    AuthorizationDenied,
    InvalidState,
    NoRefreshToken,
    ProviderMismatch,
    ScopeNotGranted,
)
from .google.oauth import build_authorization_redirect, exchange_code
from .concurrency import advance_generation, locked_or_create_connection_for_authorization
from .models import IntegrationConnection, IntegrationCredential, OAuthAuthorizationRequest
from .providers import get_provider
from .status import ConnectionStatus

logger = logging.getLogger(__name__)

#: Bytes of entropy in the OAuth state value.
STATE_BYTES = 32


def hash_state(state: str) -> str:
    """SHA-256 of the state value. Only this is ever persisted."""
    return hashlib.sha256(state.encode()).hexdigest()


@dataclass(frozen=True)
class AuthorizationStart:
    authorization_url: str
    request_id: int


def _needs_forced_consent(connection: IntegrationConnection | None) -> bool:
    """True unless we hold a refresh token we have no reason to distrust.

    The rule is local capability, not "is this the first time":

        Can this authorization preserve an existing refresh token?
        If not, it must guarantee acquiring a new one.

    M3 asked the wrong question. It assumed a new connection row meant a first
    authorization of this Google account for this application, and skipped
    consent on that basis. A new row proves only that *this project* has not
    connected this provider. The same account may already have authorized us
    through another project or workspace, and Google may then return no
    ``refresh_token`` at all — so a *first* connection fails on NoRefreshToken
    exactly as a post-disconnect one does. This system deliberately holds no
    Google identity, so it cannot detect that; it does not need to, because the
    local question is always answerable.

    A genuinely first authorization pays nothing for this: Google shows a
    consent screen for newly requested scopes regardless, so ``prompt=consent``
    changes nothing the user sees. Offline access is a hard requirement — the
    backend reaches the provider with no user present — and old behaviour is not
    preserved for being old when it cannot guarantee it.
    """
    if connection is None:
        # Nothing stored means nothing to preserve.
        return True

    # REAUTH_REQUIRED is semantically necessary, not defensive: the credential
    # row may still be there, holding a refresh token we already know is dead.
    # The credential check below would see a non-empty token and wrongly say
    # "preservable".
    #
    # DISCONNECTED is the genuinely redundant one — disconnect deletes the
    # credential row, so the check below would catch it too. Kept deliberately,
    # so a future change to how disconnect clears credentials cannot silently
    # remove forced consent.
    if connection.status in (
        ConnectionStatus.REAUTH_REQUIRED,
        ConnectionStatus.DISCONNECTED,
    ):
        return True

    credential = IntegrationCredential.objects.filter(connection=connection).first()
    if credential is None or not credential.refresh_token:
        return True

    # The previous attempt ended because Google returned no refresh token and
    # none was stored. Re-consent is the documented way to obtain one.
    return (
        connection.status == ConnectionStatus.ERROR
        and connection.last_error_code == NoRefreshToken.code
    )


@transaction.atomic
def start_authorization(*, user, project, provider_key: str) -> AuthorizationStart:
    """Create a single-use authorization request and the Google consent URL."""
    provider = get_provider(provider_key)
    if provider is None:
        raise ProviderMismatch

    # A first authorization creates the row as pending. An existing row keeps
    # its durable status: the in-flight attempt is represented by the
    # OAuthAuthorizationRequest, and starting one must not destroy meaningful
    # state that survives a cancellation. Someone who reaches
    # awaiting_resource_selection and then cancels a re-authorization is still
    # awaiting resource selection.
    #
    # This is the only place in the codebase allowed to create a connection.
    connection = locked_or_create_connection_for_authorization(
        project, provider.key, user=user
    )

    # A new attempt supersedes any older one. Advanced on every invocation, not
    # only when a durable field changes: the counter tracks expressions of
    # intent, and a start that changes no status is still one.
    generation = advance_generation(connection)

    # Supersede any attempt still outstanding for this exact tuple. Without
    # this, abandoning a flow (closing the Google tab, losing connectivity)
    # leaves the connection in pending_authorization with a live request the
    # user cannot reach, and a stale browser tab could later complete an
    # authorization the user has already restarted.
    #
    # Scoped to user + project + provider, so another user's, project's or
    # provider's in-flight attempt is untouched. This runs in the same
    # transaction as the new request's creation, so there is never a moment
    # with two usable requests for the tuple.
    OAuthAuthorizationRequest.objects.filter(
        user=user,
        project=project,
        provider=provider.key,
        consumed_at__isnull=True,
    ).update(consumed_at=timezone.now())

    state = secrets.token_urlsafe(STATE_BYTES)
    redirect = build_authorization_redirect(
        scopes=list(provider.oauth_scopes),
        state=state,
        force_consent=_needs_forced_consent(connection),
    )

    request = OAuthAuthorizationRequest.objects.create(
        state_hash=hash_state(state),
        project=project,
        provider=provider.key,
        user=user,
        code_verifier=redirect.code_verifier,
        connection_generation=generation,
        expires_at=timezone.now()
        + timedelta(seconds=settings.OAUTH_STATE_TTL_SECONDS),
    )

    record_event(
        action=AuditEvent.Action.INTEGRATION_AUTHORIZATION_STARTED,
        actor=user,
        project=project,
        provider=provider.key,
        metadata={"provider": provider.key},
    )
    return AuthorizationStart(authorization_url=redirect.url, request_id=request.pk)


def _consume_request(*, state: str, user) -> OAuthAuthorizationRequest:
    """Atomically claim the authorization request for this state.

    Every rejection raises the same InvalidState, so the response can never be
    used to learn whether another tenant's authorization request exists.
    """
    if not state:
        raise InvalidState

    try:
        request = (
            OAuthAuthorizationRequest.objects.select_for_update()
            .select_related("project")
            .get(state_hash=hash_state(state))
        )
    except OAuthAuthorizationRequest.DoesNotExist as exc:
        raise InvalidState from exc

    if request.is_consumed or request.is_expired():
        raise InvalidState
    if request.user_id != user.pk:
        raise InvalidState
    # Membership is re-checked now, not trusted from when the flow started:
    # access can be revoked while the user is away at Google.
    if not projects_for_user(user).filter(pk=request.project_id).exists():
        raise InvalidState

    request.consumed_at = timezone.now()
    request.save(update_fields=["consumed_at"])
    return request


def _store_credentials(connection: IntegrationConnection, result) -> None:
    """Persist tokens, preserving an existing refresh token.

    A token response that omits ``refresh_token`` is normal — Google returns
    one only when it issues a new one — and must never blank a stored one.

    The refresh-token rule is decided *before* anything is written, so a failed
    authorization leaves no row behind. An IntegrationCredential means "we hold
    credential material", never "an authorization was attempted".
    """
    credential = IntegrationCredential.objects.filter(connection=connection).first()
    stored_refresh_token = credential.refresh_token if credential else ""

    if result.refresh_token:
        refresh_token = result.refresh_token
    elif stored_refresh_token:
        refresh_token = stored_refresh_token
    else:
        # No new refresh token and none stored: offline access was not granted,
        # so do not record this as a durable authorization — and do not create
        # an empty row as a side effect of failing.
        raise NoRefreshToken

    if credential is None:
        credential = IntegrationCredential(connection=connection)
    credential.refresh_token = refresh_token
    credential.access_token = result.access_token
    expires_at = result.expires_at
    if expires_at is not None and timezone.is_naive(expires_at):
        # google-auth returns a naive UTC expiry; the project stores aware
        # datetimes (USE_TZ=True).
        expires_at = timezone.make_aware(expires_at, UTC)
    credential.access_token_expires_at = expires_at
    credential.save()


def _record_failure(request, error_code: str) -> None:
    record_event(
        action=AuditEvent.Action.INTEGRATION_AUTHORIZATION_FAILED,
        actor=request.user,
        project=request.project,
        provider=request.provider,
        metadata={"provider": request.provider, "error_code": error_code},
    )


def complete_authorization(*, user, state: str, code: str, error: str = "") -> OAuthAuthorizationRequest:
    """Finish an authorization and return the consumed request.

    The request is consumed first and on every path — including denial and
    provider errors — so a callback URL cannot be replayed.

    Only the consumption is transactional. Wrapping the whole function would be
    wrong: every failure below raises, and a raise inside the transaction would
    roll the consumption back, making a denied or failed callback replayable,
    and would discard the error status written for the user to see.
    """
    with transaction.atomic():
        request = _consume_request(state=state, user=user)
    # The consumption is committed from here on, so the failure paths below
    # keep their writes.

    provider = get_provider(request.provider)
    if provider is None:
        _record_failure(request, ProviderMismatch.code)
        raise ProviderMismatch

    connection = IntegrationConnection.objects.get(
        project=request.project, provider=request.provider
    )

    def fail(exc_class) -> None:
        connection.status = ConnectionStatus.ERROR
        connection.last_error_code = exc_class.code
        connection.last_error_message = exc_class.message
        connection.save(
            update_fields=["status", "last_error_code", "last_error_message", "updated_at"]
        )
        _record_failure(request, exc_class.code)

    if error:
        # The user declined, or Google refused. Not an error state on the
        # connection: nothing was ever authorized, so the row returns to
        # "not connected" by being removed if it has no credential.
        if not IntegrationCredential.objects.filter(connection=connection).exists():
            connection.delete()
        _record_failure(request, AuthorizationDenied.code)
        raise AuthorizationDenied

    if not code:
        fail(InvalidState)
        raise InvalidState

    try:
        result = exchange_code(
            code=code,
            scopes=list(provider.oauth_scopes),
            code_verifier=request.code_verifier,
        )
    except Exception as exc:
        fail(type(exc))
        raise

    # Granular consent means the user can untick a scope. Verify what was
    # actually granted rather than assuming the request was honoured.
    missing = set(provider.oauth_scopes) - set(result.granted_scopes)
    if missing:
        fail(ScopeNotGranted)
        raise ScopeNotGranted

    try:
        _store_credentials(connection, result)
    except NoRefreshToken:
        fail(NoRefreshToken)
        raise

    connection.status = ConnectionStatus.AWAITING_RESOURCE_SELECTION
    connection.granted_scopes = list(result.granted_scopes)
    connection.connected_by = user
    connection.last_error_code = ""
    connection.last_error_message = ""
    connection.save(
        update_fields=[
            "status",
            "granted_scopes",
            "connected_by",
            "last_error_code",
            "last_error_message",
            "updated_at",
        ]
    )

    record_event(
        action=AuditEvent.Action.INTEGRATION_AUTHORIZED,
        actor=user,
        project=request.project,
        provider=request.provider,
        metadata={"provider": request.provider, "status": connection.status},
    )
    return request
