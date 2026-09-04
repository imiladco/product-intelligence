"""The provider-neutral vocabulary for "did this resource verify".

One classifier, one conversion table, used from two places. ``verify`` wraps a
catalog call; ``outcome_from_lifecycle_error`` converts a failure that happened
*before* verification could begin — which is why the honest statement is that
**this module** is the only place a lifecycle error becomes an outcome, rather
than that ``verify`` is.

Nothing here inspects a provider response. It catches this project's own error
classes, which the provider modules have already translated, so there is no
provider branch anywhere in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .google.errors import (
    CredentialRefreshFailed,
    GoogleApiError,
    ResourceNotAccessible,
    ResourceUnavailable,
)
from .resources import RemoteResource


class VerificationResult(StrEnum):
    SUCCESS = "success"
    RESOURCE_NOT_ACCESSIBLE = "resource_not_accessible"
    CREDENTIAL_REJECTED = "credential_rejected"
    TRANSIENT = "transient"


class VerificationContext(StrEnum):
    """Which status table applies to the outcome."""

    HEALTH_CHECK = "health_check"
    RECONNECT = "reconnect"


@dataclass(frozen=True)
class VerificationOutcome:
    result: VerificationResult
    #: Evidence the provider returned. Present on SUCCESS only, and read only
    #: to know that — never written to the connection's selection fields.
    resource: RemoteResource | None = None
    error_code: str = ""
    error_message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.result is VerificationResult.SUCCESS


def outcome_from_lifecycle_error(error: GoogleApiError) -> VerificationOutcome:
    """The single conversion table from a lifecycle error to an outcome.

    CredentialMissing is deliberately absent: it means the check never began,
    so there is no outcome to report and the caller raises it onward.
    """
    if isinstance(error, ResourceNotAccessible):
        result = VerificationResult.RESOURCE_NOT_ACCESSIBLE
    elif isinstance(error, CredentialRefreshFailed):
        result = VerificationResult.CREDENTIAL_REJECTED
    elif isinstance(error, ResourceUnavailable):
        result = VerificationResult.TRANSIENT
    else:
        # The base class, and anything unrecognised: treat as transient rather
        # than asserting a durable failure we cannot substantiate.
        result = VerificationResult.TRANSIENT

    return VerificationOutcome(
        result=result, error_code=error.code, error_message=error.message
    )


def verify(*, catalog, access_token: str, resource_id: str) -> VerificationOutcome:
    """Ask the provider whether this resource is usable by this credential."""
    try:
        resource = catalog.verify_resource(access_token, resource_id)
    except GoogleApiError as error:
        return outcome_from_lifecycle_error(error)
    return VerificationOutcome(
        result=VerificationResult.SUCCESS, resource=resource
    )
