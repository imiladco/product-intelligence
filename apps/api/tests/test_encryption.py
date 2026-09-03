"""Credential encryption at rest, and key rotation."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.db import connection

from common.fields import reset_cipher_cache
from integrations.models import IntegrationConnection, IntegrationCredential
from integrations.providers import ProviderKey

pytestmark = pytest.mark.django_db

SECRET = "1//0-super-secret-refresh-token-value"


@pytest.fixture
def credential(make_user_with_workspace, make_project):
    _user, workspace = make_user_with_workspace()
    conn = IntegrationConnection.objects.create(
        project=make_project(workspace), provider=ProviderKey.GA4
    )
    return IntegrationCredential.objects.create(
        connection=conn, access_token="access-token-value", refresh_token=SECRET
    )


def raw_column(table: str, column: str, pk: int) -> str:
    """Read the column bypassing Django, so no decryption happens."""
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {table} WHERE id = %s", [pk])
        return cursor.fetchone()[0]


class TestEncryptionAtRest:
    def test_ciphertext_in_the_database_differs_from_the_plaintext(self, credential):
        stored = raw_column("integrations_integrationcredential", "refresh_token", credential.pk)
        assert stored != SECRET
        assert SECRET not in stored
        assert stored.startswith("gAAAAA")  # Fernet token prefix

    def test_value_decrypts_on_model_access(self, credential):
        credential.refresh_from_db()
        assert credential.refresh_token == SECRET
        assert credential.access_token == "access-token-value"

    def test_each_write_produces_different_ciphertext(self, credential):
        first = raw_column("integrations_integrationcredential", "refresh_token", credential.pk)
        credential.refresh_token = SECRET
        credential.save()
        second = raw_column("integrations_integrationcredential", "refresh_token", credential.pk)
        assert first != second  # Fernet includes a timestamp and random IV

    def test_empty_value_is_not_encrypted(self, credential):
        """Blank means "no credential", not ciphertext of an empty string."""
        credential.refresh_token = ""
        credential.save()
        assert raw_column(
            "integrations_integrationcredential", "refresh_token", credential.pk
        ) == ""

    def test_oauth_code_verifier_is_encrypted_too(self, make_user_with_workspace, make_project):
        from django.utils import timezone
        from datetime import timedelta
        from integrations.models import OAuthAuthorizationRequest

        user, workspace = make_user_with_workspace()
        request = OAuthAuthorizationRequest.objects.create(
            state_hash="a" * 64,
            project=make_project(workspace),
            provider=ProviderKey.GA4,
            user=user,
            code_verifier="verifier-secret-value",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        stored = raw_column(
            "integrations_oauthauthorizationrequest", "code_verifier", request.pk
        )
        assert "verifier-secret-value" not in stored
        request.refresh_from_db()
        assert request.code_verifier == "verifier-secret-value"


class TestKeyRotation:
    def test_old_key_still_decrypts_after_a_new_key_is_prepended(
        self, credential, settings
    ):
        original = settings.CREDENTIAL_ENCRYPTION_KEYS
        new_key = Fernet.generate_key().decode()

        # Rotate: new key first (encrypts), old key retained (still decrypts).
        settings.CREDENTIAL_ENCRYPTION_KEYS = [new_key, *original]
        reset_cipher_cache()
        try:
            credential.refresh_from_db()
            assert credential.refresh_token == SECRET
        finally:
            settings.CREDENTIAL_ENCRYPTION_KEYS = original
            reset_cipher_cache()

    def test_new_writes_use_the_first_key(self, credential, settings):
        original = settings.CREDENTIAL_ENCRYPTION_KEYS
        new_key = Fernet.generate_key().decode()
        settings.CREDENTIAL_ENCRYPTION_KEYS = [new_key, *original]
        reset_cipher_cache()
        try:
            credential.refresh_token = "rotated-secret"
            credential.save()
            stored = raw_column(
                "integrations_integrationcredential", "refresh_token", credential.pk
            )
            # Decryptable with the new key alone: it did the encrypting.
            assert Fernet(new_key).decrypt(stored.encode()).decode() == "rotated-secret"
        finally:
            settings.CREDENTIAL_ENCRYPTION_KEYS = original
            reset_cipher_cache()

    def test_dropping_the_encrypting_key_fails_loudly(self, credential, settings):
        """Silent data loss is worse than an error."""
        from django.core.exceptions import ImproperlyConfigured

        original = settings.CREDENTIAL_ENCRYPTION_KEYS
        settings.CREDENTIAL_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
        reset_cipher_cache()
        try:
            with pytest.raises(ImproperlyConfigured, match="Could not decrypt"):
                credential.refresh_from_db()
        finally:
            settings.CREDENTIAL_ENCRYPTION_KEYS = original
            reset_cipher_cache()

    def test_missing_key_configuration_fails_loudly(self, settings):
        from django.core.exceptions import ImproperlyConfigured

        original = settings.CREDENTIAL_ENCRYPTION_KEYS
        settings.CREDENTIAL_ENCRYPTION_KEYS = []
        reset_cipher_cache()
        try:
            with pytest.raises(ImproperlyConfigured, match="CREDENTIAL_ENCRYPTION_KEYS"):
                IntegrationCredential(access_token="x")._meta.get_field(
                    "access_token"
                ).get_prep_value("x")
        finally:
            settings.CREDENTIAL_ENCRYPTION_KEYS = original
            reset_cipher_cache()
