"""The gunicorn access log must never contain a query string.

The OAuth callback receives ?code=...&state=... in its query string, so
gunicorn's default access log format — which logs the raw request line via
%(r)s — writes both secrets to the log on every callback. This was found in a
real staging deployment, not in theory.

Django's LOGGING filters cannot cover this: gunicorn configures the
gunicorn.access logger itself with propagate=False, so those records never
reach the root handler or its redaction filter. The fix therefore lives in the
gunicorn invocation, and these tests read the format out of the entrypoint the
container actually runs, so the test and the deployment cannot drift apart.
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "docker" / "api" / "entrypoint.sh"
API_DIR = REPO_ROOT / "apps" / "api"

# Obviously fake. Never use a real code or state in a test.
PROBE_CODE = "LEAK_PROBE_CODE"
PROBE_STATE = "LEAK_PROBE_STATE"
CALLBACK_PATH = "/api/integrations/oauth/google/callback"


def access_log_format() -> str:
    """The format string the container actually passes to gunicorn."""
    match = re.search(
        r"^ACCESS_LOG_FORMAT='(.*)'$", ENTRYPOINT.read_text(), re.MULTILINE
    )
    assert match, "ACCESS_LOG_FORMAT not found in entrypoint.sh"
    return match.group(1)


class TestAccessLogFormatDefinition:
    def test_entrypoint_passes_an_explicit_format(self):
        """Relying on gunicorn's default is the bug."""
        text = ENTRYPOINT.read_text()
        assert "--access-logformat" in text
        assert '--access-logformat "$ACCESS_LOG_FORMAT"' in text

    def test_format_excludes_the_raw_request_line(self):
        fmt = access_log_format()
        # %(r)s is the raw request target: method, full path WITH query, protocol.
        assert "%(r)s" not in fmt

    def test_format_excludes_the_query_string(self):
        assert "%(q)s" not in access_log_format()

    def test_format_still_logs_the_useful_parts(self):
        fmt = access_log_format()
        for placeholder in ("%(h)s", "%(t)s", "%(m)s", "%(U)s", "%(H)s", "%(s)s", "%(b)s"):
            assert placeholder in fmt, f"{placeholder} missing — log would lose value"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def gunicorn_log() -> str:
    """Run gunicorn exactly as the container does, then return its output.

    Requests are made for the OAuth callback with probe values, and for an
    unrelated route, so the assertions cover the callback and every other path.
    """
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable, "-m", "gunicorn", "config.wsgi:application",
            "--bind", f"127.0.0.1:{port}",
            "--workers", "1",
            "--access-logfile", "-",
            "--access-logformat", access_log_format(),
            "--error-logfile", "-",
            "--capture-output",
        ],
        cwd=API_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2)
                break
            except urllib.error.HTTPError:
                break  # responding, whatever the status
            except Exception:
                time.sleep(0.4)
        else:
            pytest.fail("gunicorn did not start")

        for url in (
            f"http://127.0.0.1:{port}{CALLBACK_PATH}"
            f"?code={PROBE_CODE}&state={PROBE_STATE}&scope=openid",
            f"http://127.0.0.1:{port}/api/projects?secret_param={PROBE_CODE}",
        ):
            try:
                urllib.request.urlopen(url, timeout=5)
            except urllib.error.HTTPError:
                pass  # 403/404 is expected and fine; the log line is the point
        time.sleep(1.5)
    finally:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate()
    return output


def access_lines(log: str, path: str) -> list[str]:
    """Only gunicorn access lines, identified by the protocol they log.

    The output also carries Django's own django.request records, which mention
    the same path; those are not what this format controls.
    """
    return [l for l in log.splitlines() if path in l and "HTTP/1.1" in l]


class TestNoSecretsInAccessLog:
    def test_the_callback_path_is_still_logged(self, gunicorn_log):
        """Path logging YES — the log must stay useful."""
        assert access_lines(gunicorn_log, CALLBACK_PATH), gunicorn_log

    def test_method_and_status_are_still_logged(self, gunicorn_log):
        line = access_lines(gunicorn_log, CALLBACK_PATH)[0]
        assert "GET" in line
        assert re.search(r'" \d{3} ', line), f"no status code in: {line}"

    def test_the_oauth_code_never_appears(self, gunicorn_log):
        assert PROBE_CODE not in gunicorn_log

    def test_the_oauth_state_never_appears(self, gunicorn_log):
        assert PROBE_STATE not in gunicorn_log

    def test_no_query_string_survives_on_the_callback(self, gunicorn_log):
        line = access_lines(gunicorn_log, CALLBACK_PATH)[0]
        assert "?" not in line, f"query string leaked: {line}"

    def test_query_strings_are_dropped_on_every_route_not_just_the_callback(
        self, gunicorn_log
    ):
        line = access_lines(gunicorn_log, "/api/projects")[0]
        assert "secret_param" not in line
        assert "?" not in line, f"query string leaked: {line}"

    def test_djangos_own_request_logger_also_logs_no_query_string(self, gunicorn_log):
        """The second logger in the output, checked rather than assumed.

        django.request records the path on a 4xx. It must not carry the query
        string either, or the fix would be incomplete.
        """
        django_lines = [
            l for l in gunicorn_log.splitlines()
            if "django.request" in l and CALLBACK_PATH in l
        ]
        assert django_lines, "expected a django.request line for the probe"
        for line in django_lines:
            assert "?" not in line, f"query string leaked: {line}"
            assert PROBE_CODE not in line
            assert PROBE_STATE not in line
