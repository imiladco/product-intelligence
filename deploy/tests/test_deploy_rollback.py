"""Rollback: images only, and only when that is honest.

Image rollback and database rollback are different things. This library
restores the previous images and never touches schema — no migrate, no
--fake, no dump, no restore. A source-level test asserts that, because the
temptation to "just roll the migration back too" is exactly how data is lost.

Rollback is permitted only when the candidate declared backward-compatible
migrations. Otherwise the previous images would be started against a schema
they cannot read, so the deploy stops and demands manual recovery instead of
pretending to have fixed something.

If the rollback's own health gate fails, everything stops. No retry, no second
rollback, no loop: diagnostics are collected and a human decides.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "deploy" / "scripts" / "lib"
ROLLBACK_LIB = LIB_DIR / "rollback.sh"

SHA_PREV = "a" * 40
SHA_NEW = "b" * 40
API_PREV = "sha256:" + "1" * 64
WEB_PREV = "sha256:" + "2" * 64


@pytest.fixture
def harness(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    calls = tmp_path / "calls.log"

    (bin_dir / "docker").write_text(f"""#!/usr/bin/env bash
echo "docker $@" >> {calls}
if [[ "$*" == *"logs"* ]]; then
  echo "SECRET_VALUE=hunter2 appeared in a log line"
fi
exit "$(cat {tmp_path}/docker_status 2>/dev/null || echo 0)"
""")
    (bin_dir / "docker").chmod(0o755)

    # Reports whichever release the test says the host is serving, so the
    # health gate inside pi_rollback_execute can actually be satisfied.
    (bin_dir / "curl").write_text(f"""#!/usr/bin/env bash
echo "curl $@" >> {calls}
printf '{{"status":"ok","database":"ok","release":"%s"}}' \
  "$(cat {tmp_path}/served_release 2>/dev/null || echo '{SHA_PREV}')"
exit "$(cat {tmp_path}/curl_status 2>/dev/null || echo 0)"
""")
    (bin_dir / "curl").chmod(0o755)

    return {"bin": bin_dir, "tmp": tmp_path, "state": state,
            "release": release_dir, "calls": calls}


def run(harness, snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c",
         # The real entry points source every library; so does this.
         f"source {LIB_DIR}/validate.sh; source {LIB_DIR}/ledger.sh; "
         f"source {LIB_DIR}/healthgate.sh; source {ROLLBACK_LIB}; {snippet}"],
        capture_output=True, text=True, timeout=60,
        env={
            "PATH": f"{harness['bin']}:/usr/bin:/bin:/usr/local/bin",
            "PI_STATE_DIR": str(harness["state"]),
            "PI_RELEASE_DIR": str(harness["release"]),
            "PI_COMPOSE_MANIFEST": str(harness["tmp"] / "compose.yaml"),
            "PI_HEALTH_INTERVAL": "0",
            "PI_HEALTH_ATTEMPTS_CONTAINERS": "1",
            "PI_HEALTH_ATTEMPTS_INTERNAL": "1",
            "PI_HEALTH_ATTEMPTS_PUBLIC": "1",
            "PI_HEALTH_ATTEMPTS_ROUTE": "1",
        },
    )


def ledger_with_previous(harness, *, previous: bool = True):
    data = {
        "environment": "staging",
        "current": {"sha": SHA_NEW, "api_digest": "sha256:" + "9" * 64,
                    "web_digest": "sha256:" + "8" * 64},
        "previous": ({"sha": SHA_PREV, "api_digest": API_PREV, "web_digest": WEB_PREV}
                     if previous else None),
        "candidate": None,
    }
    (harness["state"] / "staging.json").write_text(json.dumps(data))


class TestRollbackPermission:
    def test_permitted_when_compatible_and_a_previous_release_exists(self, harness):
        ledger_with_previous(harness)
        assert run(harness, 'pi_rollback_is_permitted staging "true"').returncode == 0

    def test_refused_when_migrations_are_incompatible(self, harness):
        """The previous images cannot read the new schema."""
        ledger_with_previous(harness)
        assert run(harness, 'pi_rollback_is_permitted staging "false"').returncode != 0

    @pytest.mark.parametrize("compat", ["", "unknown", "True", "1", "yes"])
    def test_refused_on_anything_that_is_not_exactly_true(self, harness, compat):
        ledger_with_previous(harness)
        assert run(harness, f'pi_rollback_is_permitted staging "{compat}"').returncode != 0

    def test_refused_without_a_previous_release(self, harness):
        """The first release of an environment has nothing to fall back to."""
        ledger_with_previous(harness, previous=False)
        assert run(harness, 'pi_rollback_is_permitted staging "true"').returncode != 0

    def test_refused_when_the_ledger_is_missing(self, harness):
        assert run(harness, 'pi_rollback_is_permitted staging "true"').returncode != 0


class TestRollbackExecution:
    def test_it_rewrites_the_release_file_with_the_previous_digests(self, harness):
        ledger_with_previous(harness)
        (harness["tmp"] / "curl_status").write_text("0")

        result = run(harness, 'pi_rollback_execute staging "staging.arkav.lol"')

        assert result.returncode == 0, result.stdout + result.stderr
        images = (harness["release"] / "images.env").read_text()
        assert API_PREV in images
        assert WEB_PREV in images
        assert f"RELEASE_SHA={SHA_PREV}" in images

    def test_it_recreates_the_application(self, harness):
        ledger_with_previous(harness)
        result = run(harness, 'pi_rollback_execute staging "staging.arkav.lol"')
        assert "up -d" in harness["calls"].read_text()

    def test_a_failing_rollback_health_gate_does_not_retry(self, harness):
        """One attempt. A loop here turns a bad deploy into an outage that
        keeps restarting itself."""
        ledger_with_previous(harness)
        (harness["tmp"] / "curl_status").write_text("7")

        result = run(harness, 'pi_rollback_execute staging "staging.arkav.lol"')

        assert result.returncode != 0
        assert "CRITICAL" in result.stdout + result.stderr
        assert harness["calls"].read_text().count("up -d") == 1


class TestRollbackNeverTouchesTheDatabase:
    def test_no_schema_operation_appears_in_the_library(self):
        code = "\n".join(
            line for line in ROLLBACK_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        for forbidden in ("migrate", "psql", "pg_restore", "pg_dump", "--fake", "sqlmigrate"):
            assert forbidden not in code, f"rollback must not {forbidden}"

    def test_execution_issues_no_database_command(self, harness):
        ledger_with_previous(harness)
        run(harness, 'pi_rollback_execute staging "staging.arkav.lol"')
        calls = harness["calls"].read_text()
        assert "migrate" not in calls
        assert "psql" not in calls


class TestDiagnostics:
    def test_it_captures_compose_state_and_logs(self, harness):
        outdir = harness["tmp"] / "diag"
        result = run(harness, f'pi_collect_diagnostics staging "{outdir}"')

        assert result.returncode == 0, result.stderr
        assert (outdir / "ps.txt").exists()
        assert (outdir / "logs.txt").exists()

    def test_it_is_scoped_to_the_project(self, harness):
        outdir = harness["tmp"] / "diag"
        run(harness, f'pi_collect_diagnostics staging "{outdir}"')

        for line in harness["calls"].read_text().splitlines():
            if line.startswith("docker "):
                assert "compose -f" in line, f"unscoped docker call: {line}"

    def test_it_never_dumps_the_environment(self):
        """Logs are captured; configuration is not.

        `docker inspect` of .Config.Env or a cat of .env would put every
        secret in a diagnostics directory that outlives the deploy.
        """
        code = "\n".join(
            line for line in ROLLBACK_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert ".Config.Env" not in code
        assert "cat .env" not in code
        assert "docker inspect" not in code


class TestSourceLevelGuarantees:
    def test_no_retry_loop(self):
        code = "\n".join(
            line for line in ROLLBACK_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "while true" not in code
        assert "until true" not in code

    def test_no_eval(self):
        code = "\n".join(
            line for line in ROLLBACK_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "eval " not in code
