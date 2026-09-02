"""Encrypted model fields.

Credential material is encrypted at rest with Fernet (AES-128-CBC plus
HMAC-SHA256) from the `cryptography` library — a widely used, actively
maintained implementation. No cryptographic primitive is invented here.

``MultiFernet`` gives key rotation without downtime: the first configured key
encrypts, every configured key can decrypt. To rotate, prepend a new key and
keep the old one until `rotate_credential_keys` has re-encrypted existing rows.
"""

from __future__ import annotations

import functools

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


@functools.lru_cache(maxsize=1)
def _cipher() -> MultiFernet:
    keys = getattr(settings, "CREDENTIAL_ENCRYPTION_KEYS", None) or []
    if not keys:
        raise ImproperlyConfigured(
            "CREDENTIAL_ENCRYPTION_KEYS is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"\n'
            "See .env.example. This key is separate from DJANGO_SECRET_KEY and "
            "must be backed up: without it, stored credentials are unrecoverable."
        )
    try:
        fernets = [Fernet(key) for key in keys]
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(
            "CREDENTIAL_ENCRYPTION_KEYS contains a malformed key. Each entry must "
            "be a urlsafe-base64 32-byte Fernet key."
        ) from exc
    return MultiFernet(fernets)


def reset_cipher_cache() -> None:
    """Drop the cached cipher. For key rotation and for tests."""
    _cipher.cache_clear()


class EncryptedTextField(models.TextField):
    """A TextField whose database value is Fernet ciphertext.

    The column holds base64 ciphertext, so a database dump, a replica, or a
    stray admin query never exposes the plaintext. Values are decrypted on
    attribute access, so callers see a normal string.

    Not indexable or searchable by value — ciphertext differs on every write,
    which is the point.
    """

    def get_prep_value(self, value):
        if value is None:
            return None
        if value == "":
            # Distinguish "no credential" from ciphertext of the empty string,
            # and keep blank=True columns readable.
            return ""
        return _cipher().encrypt(str(value).encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return _cipher().decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise ImproperlyConfigured(
                "Could not decrypt a stored credential. The encrypting key is "
                "missing from CREDENTIAL_ENCRYPTION_KEYS — add it back before "
                "rotating."
            ) from exc
