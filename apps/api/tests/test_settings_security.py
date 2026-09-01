"""Production security configuration.

These settings are derived from DJANGO_DEBUG, which makes them easy to break
without noticing: nothing in day-to-day development exercises the DEBUG=False
branch. This module loads the settings module under production-like
environment variables and asserts the result.
"""

from __future__ import annotations

import importlib
import sys

import pytest

PROD_ENV = {
    "DJANGO_DEBUG": "false",
    "DJANGO_SECRET_KEY": "a" * 64,
    "DJANGO_ALLOWED_HOSTS": "app.example.com",
    "CSRF_TRUSTED_ORIGINS": "https://app.example.com",
}


def load_settings(env: dict[str, str], monkeypatch):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module = importlib.import_module("config.settings")
    return importlib.reload(module)


@pytest.fixture
def production_settings(monkeypatch):
    module = load_settings(PROD_ENV, monkeypatch)
    yield module
    # Restore the module to the ambient (development) configuration so later
    # tests are unaffected.
    for key in PROD_ENV:
        monkeypatch.delenv(key, raising=False)
    importlib.reload(sys.modules["config.settings"])


class TestProductionSettings:
    def test_debug_is_off(self, production_settings):
        assert production_settings.DEBUG is False

    def test_cookies_are_secure(self, production_settings):
        assert production_settings.SESSION_COOKIE_SECURE is True
        assert production_settings.CSRF_COOKIE_SECURE is True

    def test_session_cookie_is_httponly_and_lax(self, production_settings):
        assert production_settings.SESSION_COOKIE_HTTPONLY is True
        # Lax, not Strict: the Google OAuth callback is a top-level cross-site
        # GET that must still carry the session.
        assert production_settings.SESSION_COOKIE_SAMESITE == "Lax"

    def test_csrf_cookie_stays_readable_by_the_frontend(self, production_settings):
        assert production_settings.CSRF_COOKIE_HTTPONLY is False

    def test_hsts_and_hardening_headers(self, production_settings):
        assert production_settings.SECURE_HSTS_SECONDS >= 31_536_000
        assert production_settings.SECURE_HSTS_INCLUDE_SUBDOMAINS is True
        assert production_settings.SECURE_CONTENT_TYPE_NOSNIFF is True
        assert production_settings.X_FRAME_OPTIONS == "DENY"

    def test_proxy_ssl_header_is_trusted(self, production_settings):
        assert production_settings.SECURE_PROXY_SSL_HEADER == (
            "HTTP_X_FORWARDED_PROTO",
            "https",
        )

    def test_allowed_hosts_and_csrf_origins_come_from_the_environment(
        self, production_settings
    ):
        assert production_settings.ALLOWED_HOSTS == ["app.example.com"]
        assert production_settings.CSRF_TRUSTED_ORIGINS == ["https://app.example.com"]

    def test_missing_secret_key_fails_loudly(self, monkeypatch):
        # settings.py calls load_dotenv() at import, which would put the
        # developer's local .env value back. Disable it for this test so the
        # missing-variable path is what actually runs.
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        monkeypatch.setenv("DJANGO_DEBUG", "false")
        monkeypatch.delenv("DJANGO_SECRET_KEY", raising=False)
        with pytest.raises(RuntimeError, match="DJANGO_SECRET_KEY"):
            importlib.reload(sys.modules["config.settings"])
        # Leave the module in its development configuration for other tests:
        # undo the patches first, or the restoring reload hits the same error.
        monkeypatch.undo()
        importlib.reload(sys.modules["config.settings"])


class TestDevelopmentSettings:
    def test_secure_cookies_are_relaxed_only_when_debug_is_on(self, monkeypatch):
        module = load_settings({"DJANGO_DEBUG": "true"}, monkeypatch)
        assert module.DEBUG is True
        assert module.SESSION_COOKIE_SECURE is False
        monkeypatch.delenv("DJANGO_DEBUG", raising=False)
        importlib.reload(sys.modules["config.settings"])
