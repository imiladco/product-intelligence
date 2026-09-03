"""Defence-in-depth secret redaction for application logs.

The primary control is that no code path formats a token, authorization code,
or OAuth state into a log message. This filter is the backstop for the paths
nobody thought about — a third-party library logging a request URL, or a
traceback carrying a query string.
"""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

#: Sensitive names wherever they appear as key=value, "key": "value",
#: key: value, or a query parameter.
_SENSITIVE_KEYS = (
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "code_verifier",
    "code_challenge",
    "authorization",
    "state",
    "code",
)

_KEY_ALTERNATION = "|".join(re.escape(key) for key in _SENSITIVE_KEYS)

_PATTERNS = (
    # JSON: "access_token": "ya29...."
    re.compile(rf'("(?:{_KEY_ALTERNATION})"\s*:\s*")[^"]*(")', re.IGNORECASE),
    # Query string or form body: access_token=ya29....
    re.compile(rf"\b((?:{_KEY_ALTERNATION})=)[^&\s\"'}}]+", re.IGNORECASE),
    # Header style, with or without a scheme: Authorization: Bearer ya29....
    re.compile(
        rf"\b((?:{_KEY_ALTERNATION})\s*:\s*)(?!\s*[\"{{])"
        r"(?:Bearer\s+|Basic\s+)?\S+",
        re.IGNORECASE,
    ),
)


def redact(text: str) -> str:
    """Replace sensitive values in a string, leaving the key names visible."""
    redacted = _PATTERNS[0].sub(rf"\1{REDACTED}\2", text)
    redacted = _PATTERNS[1].sub(rf"\1{REDACTED}", redacted)
    return _PATTERNS[2].sub(rf"\1{REDACTED}", redacted)


class RedactSecretsFilter(logging.Filter):
    """Redacts secrets from log records.

    Works on the *formatted* message rather than the template, for two reasons:
    a secret passed as an argument (``logger.info("token %s", tok)``) is only
    recognizable once it sits next to its key name, and redacting the template
    directly would delete ``%s`` placeholders and break formatting.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - malformed record; let it through
            return True

        redacted = redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True
