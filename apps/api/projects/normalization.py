"""Website URL normalization.

A project's website is stored in one canonical form (scheme + host, no path) so
that later milestones can compare it against a Search Console property without
guessing. Users type it in many shapes, so normalize on the way in.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

_ALLOWED_SCHEMES = {"http", "https"}
# Labels are alphanumeric with internal hyphens; the final label (the TLD) must
# be alphabetic, which also rejects bare IPv4 literals such as 192.168.0.1.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)"
    r"((?!-)[a-z0-9-]{1,63}(?<!-)\.)+"
    r"[a-z]{2,63}$"
)
_DEFAULT_PORTS = {"http": 80, "https": 443}


def normalize_website_url(raw: str) -> str:
    """Return ``scheme://host[:port]`` or raise ``ValidationError``."""
    value = (raw or "").strip()
    if not value:
        raise ValidationError("A website address is required.")

    # "example.com" has no scheme; urlsplit would read it as a path.
    if "//" not in value:
        value = f"https://{value}"

    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValidationError("The website address must start with http:// or https://.")

    if not parts.hostname:
        raise ValidationError("Enter a valid website address, for example example.com.")

    try:
        host = parts.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValidationError("That website address contains invalid characters.") from exc

    if not _HOSTNAME_RE.match(host):
        raise ValidationError(
            "Enter a valid domain, for example example.com. IP addresses and "
            "single-label hosts are not supported."
        )

    port = parts.port
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"
