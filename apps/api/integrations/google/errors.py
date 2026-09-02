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
