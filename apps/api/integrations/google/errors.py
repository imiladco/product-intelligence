"""Error taxonomy for the Google OAuth flow.

Google's failures are mapped to our own codes before anything is logged or
returned, so a provider error message never carries a token or code into a log
line or an API response.
"""

from __future__ import annotations


class OAuthError(Exception):
    """Base for every failure in the authorization flow."""

    code = "oauth_error"
    message = "Google authorization failed."


class AuthorizationDenied(OAuthError):
    code = "access_denied"
    message = "Authorization was cancelled."


class InvalidState(OAuthError):
    """Missing, unknown, malformed, expired, replayed, or foreign state.

    One class for all of them on purpose: the user-facing response must not
    distinguish them, or it becomes an oracle for whether another tenant's
    authorization request exists.
    """

    code = "invalid_state"
    message = "That authorization link is no longer valid. Please try again."


class TokenExchangeFailed(OAuthError):
    code = "token_exchange_failed"
    message = "Google could not complete the authorization. Please try again."


class ScopeNotGranted(OAuthError):
    code = "scope_not_granted"
    message = (
        "The required read-only permission was not granted. Please connect "
        "again and leave the requested permission ticked."
    )


class NoRefreshToken(OAuthError):
    """Google returned no refresh token and none is stored.

    Offline access is a real requirement — the backend reaches the selected
    resource without the user present — so this is surfaced rather than
    recorded as a durable authorization.
    """

    code = "no_refresh_token"
    message = (
        "Google did not return long-lived access. Please connect again to "
        "grant offline access."
    )


class ProviderMismatch(OAuthError):
    code = "provider_mismatch"
    message = "Unexpected provider for this authorization."


class GoogleApiError(Exception):
    """Base for every failure reaching a Google API after authorization.

    Separate from OAuthError on purpose: these happen while *using* a
    connection, not while creating one, and they map to different HTTP
    responses and different connection states.

    Every subclass carries a fixed ``message``. Google's own error text is
    never forwarded into one — it can echo a request that carried a token, and
    it is written for a developer reading a console, not for a user reading a
    card.
    """

    code = "google_api_error"
    message = "Google could not be reached. Please try again."
    http_status = 503


class CredentialMissing(GoogleApiError):
    """No usable credential is stored for this connection.

    The connection was never authorized, or its authorization failed before any
    refresh token was stored.
    """

    code = "credential_missing"
    message = "This integration is not authorized yet. Connect it first."
    http_status = 409


class CredentialRefreshFailed(GoogleApiError):
    """The stored refresh token no longer works.

    Where ``invalid_grant`` lands: access revoked in the Google account, the
    password changed, or the grant expired. The connection moves to
    ``reauth_required`` — the user has to authorize again, and no amount of
    retrying will help.
    """

    code = "credential_refresh_failed"
    message = "Google access has expired. Please reauthorize this integration."
    http_status = 409


class ResourceNotAccessible(GoogleApiError):
    """The selected resource does not exist, or this credential cannot read it.

    One class for both, as with InvalidState: telling the two apart would let a
    caller probe whether a resource they have no access to exists at all.
    """

    code = "resource_not_accessible"
    message = (
        "That property is not available to the connected Google account. "
        "Choose one from the list."
    )
    http_status = 400


class InvalidResourceId(GoogleApiError):
    """The submitted resource identifier is not in the provider's format.

    Rejected before any outbound call: the value is interpolated into a Google
    URL, so a malformed one never gets the chance to become a request.
    """

    code = "invalid_resource_id"
    message = "That is not a valid property identifier."
    http_status = 400


class ResourceUnavailable(GoogleApiError):
    """Google is unreachable, rate-limiting, or failing. Transient."""

    code = "resource_unavailable"
    message = "Google could not be reached. Please try again in a moment."
    http_status = 503


class ResourceChangeNotSupported(GoogleApiError):
    """A different resource was submitted for an already connected integration.

    Changing a selection belongs with reconnect and disconnect in a later
    milestone. Re-submitting the *same* resource is allowed, so a retried or
    double-submitted request is not an error.
    """

    code = "resource_change_not_supported"
    message = "This integration already has a property selected."
    http_status = 409


class ResourceSelectionUnsupported(GoogleApiError):
    """This provider has no resource selection yet.

    Answered as a 404: the endpoint genuinely does not exist for this provider.
    """

    code = "resource_selection_unsupported"
    message = "Not found."
    http_status = 404


class ResourceMissing(GoogleApiError):
    """A health check on a connection with nothing selected.

    There is nothing to check, so the check never begins — distinct from a
    check that ran and found the resource gone.
    """

    code = "resource_missing"
    message = "No property is selected for this integration yet."
    http_status = 409
