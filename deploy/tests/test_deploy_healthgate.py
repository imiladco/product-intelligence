"""The health gate: what "the release worked" is allowed to mean.

Three things this file pins hardest.

**A 200 is not enough.** The public check requires the response to name the
release just deployed. A hostname that answers 200 with a *different* release
is a crossed environment — production served by staging — and that must fail
the deploy rather than pass it.

**The cross-environment check is not applicable until the other environment
exists.** Production does not exist during the first staging deploys, and a
gate that required it would make the very first deployment impossible. The
ledger's `current` entry is the *only* evidence that the other environment
exists: not DNS, not a reachable port, not a certificate. All three can be true
for an environment that has never had a successful release.

**Nothing loops forever.** Every check has a fixed attempt count, and a test
counts the stub's invocations to prove it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "deploy" / "scripts" / "lib"
HEALTHGATE_LIB = LIB_DIR / "healthgate.sh"

SHA = "a" * 40
OTHER_SHA = "b" * 40
STAGING_HOST = "staging.arkav.lol"
PRODUCTION_HOST = "app.arkav.lol"


@pytest.fixture
def harness(tmp_path: Path):
    """Stubs for curl and docker, plus an isolated ledger directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    calls = tmp_path / "calls.log"

    # curl: prints the body for the host it was asked about, from files the
    # test writes as body_<host>. Exit status from status_<host>.
    (bin_dir / "curl").write_text(f"""#!/usr/bin/env bash
url="${{@: -1}}"
echo "curl $url" >> {calls}
host="$(sed -E 's#https?://([^/]+).*#\\1#' <<< "$url")"
status_file="{tmp_path}/status_${{host}}"
body_file="{tmp_path}/body_${{host}}"
[[ -f "$body_file" ]] && cat "$body_file"
exit "$(cat "$status_file" 2>/dev/null || echo 0)"
""")
    (bin_dir / "curl").chmod(0o755)

    (bin_dir / "docker").write_text(f"""#!/usr/bin/env bash
echo "docker $@" >> {calls}
exit "$(cat {tmp_path}/docker_status 2>/dev/null || echo 0)"
""")
    (bin_dir / "docker").chmod(0o755)

    return {"bin": bin_dir, "tmp": tmp_path, "state": state, "calls": calls}


def run(harness, snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"source {LIB_DIR}/ledger.sh; source {HEALTHGATE_LIB}; {snippet}"],
        capture_output=True, text=True, timeout=60,
        env={
            "PATH": f"{harness['bin']}:/usr/bin:/bin:/usr/local/bin",
            "PI_STATE_DIR": str(harness["state"]),
            "PI_HEALTH_INTERVAL": "0",  # keep tests fast; bounds still apply
        },
    )


def set_host(harness, host: str, *, release: str | None = SHA, status: int = 0):
    body = json.dumps({"status": "ok", "database": "ok", "release": release or ""})
    (harness["tmp"] / f"body_{host}").write_text(body)
    (harness["tmp"] / f"status_{host}").write_text(str(status))


def give_environment_a_current_release(harness, environment: str, sha: str = OTHER_SHA):
    ledger = harness["state"] / f"{environment}.json"
    ledger.write_text(json.dumps({
        "environment": environment,
        "current": {"sha": sha, "api_digest": "sha256:" + "1" * 64,
                    "web_digest": "sha256:" + "2" * 64},
        "previous": None,
        "candidate": None,
    }))


class TestPublicHealthChecksTheRelease:
    def test_matching_release_passes(self, harness):
        set_host(harness, STAGING_HOST, release=SHA)
        assert run(harness, f'pi_health_public "{STAGING_HOST}" "{SHA}"').returncode == 0

    def test_a_different_release_fails(self, harness):
        """A 200 from the wrong release is a crossed hostname, not a success."""
        set_host(harness, STAGING_HOST, release=OTHER_SHA)
        result = run(harness, f'pi_health_public "{STAGING_HOST}" "{SHA}"')
        assert result.returncode != 0

    def test_a_non_200_fails_even_with_the_right_release(self, harness):
        set_host(harness, STAGING_HOST, release=SHA, status=22)
        assert run(harness, f'pi_health_public "{STAGING_HOST}" "{SHA}"').returncode != 0

    def test_an_unknown_release_fails(self, harness):
        """`unknown` is what an unstamped container reports; it proves nothing."""
        set_host(harness, STAGING_HOST, release="unknown")
        assert run(harness, f'pi_health_public "{STAGING_HOST}" "{SHA}"').returncode != 0

    def test_a_malformed_body_fails(self, harness):
        (harness["tmp"] / f"body_{STAGING_HOST}").write_text("<html>gateway error</html>")
        (harness["tmp"] / f"status_{STAGING_HOST}").write_text("0")
        assert run(harness, f'pi_health_public "{STAGING_HOST}" "{SHA}"').returncode != 0


class TestCrossEnvironmentRule:
    def test_required_when_the_other_environment_has_a_current_release(self, harness):
        give_environment_a_current_release(harness, "production", OTHER_SHA)
        set_host(harness, PRODUCTION_HOST, release=OTHER_SHA, status=7)  # unreachable

        result = run(harness, f'pi_health_cross_environment production "{PRODUCTION_HOST}"')
        assert result.returncode != 0, "a broken sibling must fail the deploy"

    def test_passes_when_the_other_environment_is_healthy(self, harness):
        give_environment_a_current_release(harness, "production", OTHER_SHA)
        set_host(harness, PRODUCTION_HOST, release=OTHER_SHA)

        assert run(harness, f'pi_health_cross_environment production "{PRODUCTION_HOST}"').returncode == 0

    def test_not_applicable_when_the_other_ledger_is_missing(self, harness):
        """The first staging deploys happen before production exists."""
        result = run(harness, f'pi_health_cross_environment production "{PRODUCTION_HOST}"')
        assert result.returncode == 0
        assert "not_applicable" in result.stdout

    def test_not_applicable_when_current_is_null(self, harness):
        (harness["state"] / "production.json").write_text(json.dumps({
            "environment": "production", "current": None, "previous": None, "candidate": None,
        }))
        result = run(harness, f'pi_health_cross_environment production "{PRODUCTION_HOST}"')
        assert result.returncode == 0
        assert "not_applicable" in result.stdout

    def test_existence_is_not_inferred_from_dns_or_a_reachable_host(self, harness):
        """The hostname answers perfectly well. It still does not count.

        DNS, a listening port and a valid certificate can all be true for an
        environment that has never had a successful release — during the
        production bootstrap, for one. Only the ledger is evidence.
        """
        set_host(harness, PRODUCTION_HOST, release=OTHER_SHA, status=0)

        result = run(harness, f'pi_health_cross_environment production "{PRODUCTION_HOST}"')

        assert result.returncode == 0
        assert "not_applicable" in result.stdout
        calls = harness["calls"].read_text() if harness["calls"].exists() else ""
        assert PRODUCTION_HOST not in calls, "the other host must not even be contacted"

    def test_a_corrupt_sibling_ledger_is_not_applicable(self, harness):
        (harness["state"] / "production.json").write_text("{")
        result = run(harness, f'pi_health_cross_environment production "{PRODUCTION_HOST}"')
        assert result.returncode == 0
        assert "not_applicable" in result.stdout


class TestBounded:
    def test_a_failing_check_stops_after_the_documented_attempts(self, harness):
        set_host(harness, STAGING_HOST, release=SHA, status=7)

        result = run(harness, f'pi_health_public "{STAGING_HOST}" "{SHA}"')

        assert result.returncode != 0
        attempts = harness["calls"].read_text().count(f"curl https://{STAGING_HOST}")
        assert 1 <= attempts <= 20, f"unbounded or excessive retries: {attempts}"

    def test_login_and_static_checks_are_bounded_too(self, harness):
        set_host(harness, STAGING_HOST, status=7)
        for function in ("pi_health_login", "pi_health_static"):
            harness["calls"].write_text("")
            result = run(harness, f'{function} "{STAGING_HOST}"')
            assert result.returncode != 0
            assert harness["calls"].read_text().count("curl") <= 20


class TestGateOrdering:
    def test_the_gate_stops_at_the_first_failure(self, harness):
        """Containers unhealthy: no HTTP check should run at all."""
        (harness["tmp"] / "docker_status").write_text("1")
        set_host(harness, STAGING_HOST, release=SHA)

        result = run(harness, f'pi_health_gate staging "{STAGING_HOST}" "{SHA}"')

        assert result.returncode != 0
        assert "curl" not in (harness["calls"].read_text() if harness["calls"].exists() else "")

    def test_a_full_pass_runs_every_check(self, harness):
        (harness["tmp"] / "docker_status").write_text("0")
        set_host(harness, STAGING_HOST, release=SHA)

        result = run(harness, f'pi_health_gate staging "{STAGING_HOST}" "{SHA}"')

        assert result.returncode == 0, result.stdout + result.stderr
        calls = harness["calls"].read_text()
        assert "/api/health" in calls
        assert "/login" in calls
        assert "/static/admin/css/base.css" in calls


class TestSourceLevelGuarantees:
    def test_no_unbounded_loop(self):
        code = "\n".join(
            line for line in HEALTHGATE_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "while true" not in code
        assert "until true" not in code

    def test_no_eval(self):
        code = "\n".join(
            line for line in HEALTHGATE_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "eval " not in code
