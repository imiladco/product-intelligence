"""Secrets must not reach the log stream.

The primary control is that no code path formats a secret into a message; this
filter is the backstop, so it is tested directly and through the logging stack.
"""

from __future__ import annotations

import logging

import pytest

from common.logging import REDACTED, RedactSecretsFilter, redact

ACCESS = "ya29.a0AfB_super_secret_access_token"
REFRESH = "1//04-super-secret-refresh-token"
CODE = "4/0AeanS0abcdefgh-authorization-code"
STATE = "kJ8sd7f6g5h4j3k2l1-oauth-state-value"
SECRET = "GOCSPX-super-secret-client-secret"


class TestRedact:
    @pytest.mark.parametrize(
        "text,secret",
        [
            (f'{{"access_token": "{ACCESS}"}}', ACCESS),
            (f'{{"refresh_token": "{REFRESH}"}}', REFRESH),
            (f'{{"id_token": "header.payload.sig"}}', "header.payload.sig"),
            (f"grant_type=authorization_code&code={CODE}", CODE),
            (f"https://example.com/callback?state={STATE}&code={CODE}", STATE),
            (f"client_secret={SECRET}&grant_type=x", SECRET),
            (f"Authorization: Bearer {ACCESS}", ACCESS),
            (f"code_verifier={'v' * 60}", "v" * 60),
        ],
    )
    def test_secret_is_removed(self, text, secret):
        result = redact(text)
        assert secret not in result
        assert REDACTED in result

    def test_key_names_survive_so_logs_stay_useful(self):
        assert "access_token" in redact(f'{{"access_token": "{ACCESS}"}}')
        assert "code" in redact(f"code={CODE}")

    def test_a_full_callback_query_string_is_scrubbed(self):
        query = f"state={STATE}&code={CODE}&scope=https://www.googleapis.com/auth/analytics.readonly"
        result = redact(query)
        assert STATE not in result
        assert CODE not in result
        # Non-sensitive context is preserved.
        assert "analytics.readonly" in result

    def test_a_token_json_response_body_is_scrubbed(self):
        body = (
            f'{{"access_token": "{ACCESS}", "expires_in": 3599, '
            f'"refresh_token": "{REFRESH}", "token_type": "Bearer"}}'
        )
        result = redact(body)
        assert ACCESS not in result
        assert REFRESH not in result
        assert "3599" in result

    def test_harmless_text_is_untouched(self):
        assert redact("Connected GA4 for project 12") == "Connected GA4 for project 12"


class TestRedactSecretsFilter:
    def make_record(self, msg, args=None):
        return logging.LogRecord(
            name="test", level=logging.INFO, pathname=__file__, lineno=1,
            msg=msg, args=args, exc_info=None,
        )

    def test_message_is_redacted(self):
        record = self.make_record(f'response={{"access_token": "{ACCESS}"}}')
        assert RedactSecretsFilter().filter(record) is True
        assert ACCESS not in record.getMessage()

    def test_positional_arguments_are_redacted(self):
        record = self.make_record("token exchange body: %s", (f"code={CODE}",))
        RedactSecretsFilter().filter(record)
        assert CODE not in record.getMessage()

    def test_dict_arguments_are_redacted(self):
        # A mapping arrives wrapped in a tuple, exactly as logger.info(msg, {...})
        # passes it; LogRecord unwraps it itself.
        record = self.make_record("body: %(body)s", ({"body": f"refresh_token={REFRESH}"},))
        RedactSecretsFilter().filter(record)
        assert REFRESH not in record.getMessage()

    def test_filter_is_installed_on_the_console_handler(self, settings):
        handler = settings.LOGGING["handlers"]["console"]
        assert "redact_secrets" in handler["filters"]

    def test_secret_does_not_survive_the_logging_stack(self, caplog):
        logger = logging.getLogger("integrations.test")
        logger.addFilter(RedactSecretsFilter())
        try:
            with caplog.at_level(logging.INFO, logger="integrations.test"):
                logger.info('exchanged {"access_token": "%s"}', ACCESS)
        finally:
            logger.filters.clear()
        assert ACCESS not in caplog.text


class TestDjangoLoggersAreFiltered:
    """Django's own loggers must not bypass the filter.

    Django's defaults give `django` and `django.server` their own handlers with
    propagate=False, so without an explicit override they would never reach the
    root handler. `django.server` logs the full request line, which for the
    OAuth callback carries `code` and `state`.
    """

    @pytest.mark.parametrize("name", ["django", "django.server"])
    def test_logger_uses_the_filtered_console_handler(self, settings, name):
        logger_config = settings.LOGGING["loggers"][name]
        assert logger_config["handlers"] == ["console"]
        assert logger_config["propagate"] is False

    def test_a_callback_request_line_is_redacted(self):
        line = (
            f'"GET /api/integrations/oauth/google/callback?state={STATE}&code={CODE} '
            'HTTP/1.1" 302 0'
        )
        result = redact(line)
        assert STATE not in result
        assert CODE not in result
        # The route stays legible for debugging.
        assert "/api/integrations/oauth/google/callback" in result
