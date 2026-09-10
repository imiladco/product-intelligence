import os

from django.db import connection
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


def _release() -> str:
    """Which release is answering, or "unknown".

    Read per request rather than at import: a container restarted with a new
    RELEASE_SHA must report the new one, and a value frozen at the first worker
    start would let the deploy health gate assert a stale truth.

    An unset variable and one set to "" mean the same thing — nobody told us —
    and both must read as "unknown" rather than as an empty string the gate
    could compare successfully against nothing.
    """
    return os.environ.get("RELEASE_SHA") or "unknown"


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(_request):
    """Liveness + database readiness, used by the container healthcheck.

    ``release`` is the deployed commit SHA. It is here because container health
    cannot prove that a hostname serves the release just deployed to it, and a
    public request that names the answering release can (design §10.4). A
    commit SHA of a private repository is not a secret; nothing else about the
    configuration belongs in this unauthenticated payload.
    """
    release = _release()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        # The release is reported on failure too: an operator needs to know
        # *which* release is unhealthy, not merely that something is.
        return Response(
            {"status": "error", "database": "unavailable", "release": release},
            status=503,
        )
    return Response({"status": "ok", "database": "ok", "release": release})
