"""Getting a usable Google access token for a stored connection.

The one place a refresh happens. Every caller that needs to reach a Google API
asks here and receives a bearer token, or an error it can map to a response —
never a partially-refreshed credential and never a Google exception.

Three rules this module exists to keep in one place:

* A token response without a ``refresh_token`` never blanks the stored one.
* A rejected refresh (``invalid_grant``) is a durable problem — the connection
  moves to ``reauth_required``. A network failure is not, and changes nothing.
* Nothing here touches the OAuth *authorization* flow. Authorization requests,
  their state hashes and their PKCE verifiers belong to ``oauth_service``; a
  refresh reads and rewrites token material only.
"""

from __future__ import annotations

import logging
from datetime import UTC, timedelta

import requests
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials

from ..status import ConnectionStatus
from .errors import CredentialMissing, CredentialRefreshFailed, ResourceUnavailable
from .oauth import TOKEN_URI

logger = logging.getLogger(__name__)

#: Refresh this far ahead of the recorded expiry. A token that expires while a
#: request is in flight is indistinguishable to the user from a broken
#: connection, and a minute of margin costs nothing.
EXPIRY_SKEW = timedelta(seconds=60)


def _stored_credential(connection):
    """The connection's credential row, or None. Never creates one."""
    try:
        return connection.credential
    except ObjectDoesNotExist:
        return None


def _is_still_usable(credential) -> bool:
    """True when the stored access token can be used as it stands.

    An unknown expiry counts as unusable: refreshing unnecessarily is cheap,
    while sending a dead token costs a round trip and an error the user sees.
    """
    if not credential.access_token or credential.access_token_expires_at is None:
        return False
    return credential.access_token_expires_at - timezone.now() > EXPIRY_SKEW


def mark_reauth_required(connection) -> None:
    """Record that this connection needs the user to authorize again.

    Public because the API boundary reaches the same conclusion from a 401 and
    must record it the same way, rather than keeping a second copy of what
    "needs reauthorization" means.

    Only connection state is written. The authorization request table is not
    touched: starting a new authorization is the user's action, not a side
    effect of a background refresh.
    """
    connection.status = ConnectionStatus.REAUTH_REQUIRED
    connection.last_error_code = CredentialRefreshFailed.code
    connection.last_error_message = CredentialRefreshFailed.message
    connection.save(
        update_fields=["status", "last_error_code", "last_error_message", "updated_at"]
    )


def _persist(credential, refreshed: Credentials) -> None:
    credential.access_token = refreshed.token or ""
    # Google returns a refresh token only when it issues a new one. google-auth
    # already preserves the old value, and this guard states the rule locally so
    # it cannot be lost to a library change.
    if refreshed.refresh_token:
        credential.refresh_token = refreshed.refresh_token

    expires_at = refreshed.expiry
    if expires_at is not None and timezone.is_naive(expires_at):
        # google-auth returns a naive UTC expiry; the project stores aware
        # datetimes (USE_TZ=True).
        expires_at = timezone.make_aware(expires_at, UTC)
    credential.access_token_expires_at = expires_at

    credential.save(
        update_fields=[
            "access_token",
            "refresh_token",
            "access_token_expires_at",
            "updated_at",
        ]
    )


def access_token_for(connection) -> str:
    """A usable access token for this connection, refreshing if needed.

    Raises CredentialMissing when nothing is stored, CredentialRefreshFailed
    when the grant is gone, and ResourceUnavailable when Google could not be
    reached. Never raises a Google exception, and never logs one: their text
    can echo a request body carrying the refresh token and the client secret.
    """
    credential = _stored_credential(connection)
    if credential is None or not credential.refresh_token:
        raise CredentialMissing

    if _is_still_usable(credential):
        return credential.access_token

    refreshed = Credentials(
        token=credential.access_token or None,
        refresh_token=credential.refresh_token,
        token_uri=TOKEN_URI,
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=list(connection.granted_scopes),
    )

    try:
        refreshed.refresh(GoogleAuthRequest())
    except RefreshError as exc:
        # The grant itself is gone: revoked in the Google account, password
        # changed, or expired. Retrying cannot help, so this is durable state.
        logger.warning(
            "Google credential refresh rejected for connection %s", connection.pk
        )
        mark_reauth_required(connection)
        raise CredentialRefreshFailed from exc
    except (GoogleAuthError, requests.RequestException) as exc:
        # Transport-level: unreachable, timed out, TLS. Transient, so the
        # connection keeps the state it had.
        logger.warning(
            "Google credential refresh could not complete (%s)", type(exc).__name__
        )
        raise ResourceUnavailable from exc

    if not refreshed.token:
        # A refresh that returns no access token leaves nothing usable to
        # store, and storing an empty one would loop forever on the next call.
        logger.warning(
            "Google credential refresh returned no access token for connection %s",
            connection.pk,
        )
        mark_reauth_required(connection)
        raise CredentialRefreshFailed

    _persist(credential, refreshed)
    return credential.access_token
