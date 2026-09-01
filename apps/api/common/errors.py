"""A single error envelope for the whole API.

Every error response has the shape:

    {"error": {"code": "...", "message": "...", "detail": {...}}}

so the frontend has exactly one thing to parse.
"""

from __future__ import annotations

from typing import Any

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

# Fixed vocabulary. Extend deliberately; the frontend switches on these.
CODE_VALIDATION = "validation_error"
CODE_NOT_AUTHENTICATED = "not_authenticated"
CODE_PERMISSION_DENIED = "permission_denied"
CODE_NOT_FOUND = "not_found"
CODE_THROTTLED = "throttled"
CODE_CONFLICT = "conflict"
CODE_SERVER_ERROR = "server_error"

_STATUS_TO_CODE = {
    status.HTTP_400_BAD_REQUEST: CODE_VALIDATION,
    status.HTTP_401_UNAUTHORIZED: CODE_NOT_AUTHENTICATED,
    status.HTTP_403_FORBIDDEN: CODE_PERMISSION_DENIED,
    status.HTTP_404_NOT_FOUND: CODE_NOT_FOUND,
    status.HTTP_405_METHOD_NOT_ALLOWED: CODE_VALIDATION,
    status.HTTP_409_CONFLICT: CODE_CONFLICT,
    status.HTTP_429_TOO_MANY_REQUESTS: CODE_THROTTLED,
}

_DEFAULT_MESSAGES = {
    CODE_VALIDATION: "The submitted data was not valid.",
    CODE_NOT_AUTHENTICATED: "Authentication is required.",
    CODE_PERMISSION_DENIED: "You do not have access to this resource.",
    CODE_NOT_FOUND: "Not found.",
    CODE_THROTTLED: "Too many requests. Please slow down.",
    CODE_CONFLICT: "That change conflicts with the current state.",
    CODE_SERVER_ERROR: "Something went wrong.",
}


def error_response(
    code: str,
    message: str | None = None,
    detail: Any = None,
    http_status: int = status.HTTP_400_BAD_REQUEST,
) -> Response:
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message or _DEFAULT_MESSAGES.get(code, "Request failed."),
        }
    }
    if detail is not None:
        body["error"]["detail"] = detail
    return Response(body, status=http_status)


def _flatten_field_errors(data: Any) -> dict[str, list[str]] | None:
    """Turn DRF's nested validation output into {field: [messages]}."""
    if not isinstance(data, dict):
        return None
    flat: dict[str, list[str]] = {}
    for field, value in data.items():
        if isinstance(value, list):
            flat[field] = [str(item) for item in value]
        elif isinstance(value, dict):
            flat[field] = [str(item) for sub in value.values() for item in _as_list(sub)]
        else:
            flat[field] = [str(value)]
    return flat


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def exception_handler(exc, context):
    """DRF exception hook that reshapes every handled error into the envelope."""
    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled exception: let Django's own 500 handling take over so the
        # traceback is logged rather than swallowed.
        return None

    code = _STATUS_TO_CODE.get(response.status_code, CODE_SERVER_ERROR)
    data = response.data
    message = _DEFAULT_MESSAGES.get(code, "Request failed.")
    detail: Any = None

    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        message = str(data["detail"])
    elif isinstance(data, dict):
        detail = _flatten_field_errors(data)
        non_field = (detail or {}).get("non_field_errors")
        if non_field:
            message = non_field[0]
    elif isinstance(data, list) and data:
        message = str(data[0])

    response.data = {"error": {"code": code, "message": message}}
    if detail:
        response.data["error"]["detail"] = detail
    return response
