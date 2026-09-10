"""What the API container does when it starts, and what it must not do.

Two contracts live here.

**Startup never migrates.** Until M7 the entrypoint ran collectstatic and
migrate before gunicorn on every start, so a container restarted by the Docker
daemon at 3am applied database migrations. Schema changes are now an explicit
one-shot release step (design §9.2), and this file is what stops them creeping
back into the startup path.

**Arguments are honoured.** The pre-M7 entrypoint ended in an unconditional
`exec gunicorn` and never referenced "$@", so `docker compose run api python
manage.py migrate` silently ran the server instead — appearing to succeed while
doing something else entirely. The whole release model depends on that being
false, so it is tested by running the real script, not by reading it.

These tests execute `docker/api/entrypoint.sh` directly with bash. They never
build an image and never need a Docker daemon.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "docker" / "api" / "entrypoint.sh"
DOCKERFILE = REPO_ROOT / "apps" / "api" / "Dockerfile"
API_DIR = REPO_ROOT / "apps" / "api"

#: A production-like environment, mirroring tests/test_settings_security.py, so
#: a management command can start without a real deployment behind it.
PROD_ENV = {
    "DJANGO_DEBUG": "false",
    "DJANGO_SECRET_KEY": "a" * 64,
    "DJANGO_ALLOWED_HOSTS": "app.example.com",
    "CSRF_TRUSTED_ORIGINS": "https://app.example.com",
}


def run_entrypoint(*args: str, extra_env: dict[str, str] | None = None, timeout: int = 60):
    """Run the real entrypoint, never touching the ambient environment."""
    env = {**os.environ, **PROD_ENV, **(extra_env or {})}
    # The venv's python must be the one on PATH: the entrypoint invokes bare
    # `python`, exactly as the container does.
    env["PATH"] = f"{Path(sys.executable).parent}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(ENTRYPOINT), *args],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestStartupDoesNotMutateSchema:
    def test_startup_path_does_not_migrate(self):
        assert "manage.py migrate" not in ENTRYPOINT.read_text()

    def test_startup_path_does_not_collectstatic(self):
        assert "manage.py collectstatic" not in ENTRYPOINT.read_text()

    def test_the_entrypoint_still_fails_fast(self):
        """set -euo pipefail is not optional in a script that execs a server."""
        assert "set -euo pipefail" in ENTRYPOINT.read_text()


class TestArgumentPassthrough:
    def test_entrypoint_passes_through_arguments(self):
        assert 'exec "$@"' in ENTRYPOINT.read_text()

    def test_dockerfile_declares_the_serve_command(self):
        """An explicit default beats a default implied by an empty argv."""
        assert 'CMD ["serve"]' in DOCKERFILE.read_text()

    def test_an_explicit_command_runs_instead_of_the_server(self):
        result = run_entrypoint("python", "-c", "print('RAN_COMMAND')")

        assert result.returncode == 0, result.stderr
        assert "RAN_COMMAND" in result.stdout
        assert "gunicorn" not in (result.stdout + result.stderr).lower()

    def test_a_management_command_runs_instead_of_the_server(self):
        result = run_entrypoint("python", "manage.py", "check")

        combined = result.stdout + result.stderr
        assert "Starting gunicorn" not in combined
        assert "System check identified no issues" in combined, combined

    def test_the_command_receives_its_arguments_intact(self):
        """A command is exec'd as given — not re-split, quoted or mangled."""
        result = run_entrypoint("python", "-c", "import sys; print('|'.join(sys.argv[1:]))", "a b", "c")

        assert result.returncode == 0, result.stderr
        assert "a b|c" in result.stdout


class TestServeStartsTheServer:
    def test_serve_reaches_gunicorn(self):
        """`serve` is the one argument that means "start the app server".

        An unparseable GUNICORN_WORKERS makes gunicorn reject its own arguments
        and exit at once, so the branch is proven without leaving a server
        running in the suite. (`0` was tried first: gunicorn accepts it and
        idles forever, which would hang the suite rather than fail it.)
        """
        result = run_entrypoint("serve", extra_env={"GUNICORN_WORKERS": "abc"})

        combined = (result.stdout + result.stderr).lower()
        assert "gunicorn" in combined, combined
        assert "invalid int value" in combined, combined

    def test_serve_does_not_migrate_first(self):
        result = run_entrypoint("serve", extra_env={"GUNICORN_WORKERS": "abc"})

        combined = result.stdout + result.stderr
        assert "Operations to perform" not in combined  # migrate's banner
        assert "static files copied" not in combined  # collectstatic's summary


class TestAccessLogContractIsPreserved:
    """The M3 query-string redaction, pinned again from this side.

    tests/test_access_log.py parses the entrypoint with an anchored regex and
    asserts a literal flag. Re-asserting it here means an edit that breaks it
    fails with a name that says why it mattered.
    """

    def test_the_format_assignment_still_matches_the_anchored_regex(self):
        match = re.search(r"^ACCESS_LOG_FORMAT='(.*)'$", ENTRYPOINT.read_text(), re.MULTILINE)
        assert match, "test_access_log.py parses this exact line shape"
        assert "%(r)s" not in match.group(1)
        assert "%(q)s" not in match.group(1)

    def test_gunicorn_is_still_given_the_explicit_format(self):
        assert '--access-logformat "$ACCESS_LOG_FORMAT"' in ENTRYPOINT.read_text()
