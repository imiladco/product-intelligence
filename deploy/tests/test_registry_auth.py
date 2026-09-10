"""Authenticating to GHCR from a root-only credential file.

Both images are private, so every deploy must authenticate before it pulls.
The credential lives at /etc/product-intelligence/ghcr.env, 0600 root:root,
readable by the root-owned deploy scripts and by nothing the `deploy` user can
reach.

Two failure modes this file exists to prevent.

**Relying on ambient state.** A `docker login` typed once by an admin persists
in /root/.docker/config.json, so pulls keep working and the credential file is
never exercised. The day that token expires, deploys fail with "could not pull"
and nothing on the box explains why. Authentication must therefore happen on
every deploy, from the file, not from whatever the daemon happens to remember.

**Leaking the token.** A token passed as an argument is visible in `ps` to
every user on the host for the lifetime of the call, and lands in any shell
trace. It goes in on stdin, never in argv, and is never echoed.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "deploy" / "scripts" / "lib"
REGISTRY_LIB = LIB_DIR / "registry.sh"

def executable_lines(path: Path) -> str:
    """The script with comment-only lines stripped."""
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.strip().startswith("#")
    )


TOKEN = "ghp_ThisIsTheSecretTokenValue0123456789"
USERNAME = "pi-deploy-bot"


@pytest.fixture
def env(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    stdin_seen = tmp_path / "stdin.log"

    # Records argv and whatever arrived on stdin, so a test can prove the token
    # travelled by stdin and never as an argument.
    (bin_dir / "docker").write_text(f"""#!/usr/bin/env bash
echo "docker $@" >> {calls}
if [[ "$1" == "login" ]]; then
  cat >> {stdin_seen}
  exit "$(cat {tmp_path}/login_status 2>/dev/null || echo 0)"
fi
exit 0
""")
    (bin_dir / "docker").chmod(0o755)

    credential = tmp_path / "ghcr.env"
    credential.write_text(f"GHCR_USERNAME={USERNAME}\nGHCR_TOKEN={TOKEN}\n")
    credential.chmod(0o600)

    return {
        "bin": bin_dir,
        "tmp": tmp_path,
        "calls": calls,
        "stdin": stdin_seen,
        "credential": credential,
    }


def login(env, *, credential: Path | None = None) -> subprocess.CompletedProcess:
    path = credential if credential is not None else env["credential"]
    script = f'source "{REGISTRY_LIB}"; pi_registry_login'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": f"{env['bin']}:/usr/bin:/bin",
            "PI_GHCR_CREDENTIAL_FILE": str(path),
        },
    )


def calls(env) -> str:
    return env["calls"].read_text() if env["calls"].exists() else ""


class TestSuccessfulAuthentication:
    def test_it_logs_in_to_ghcr(self, env):
        result = login(env)
        assert result.returncode == 0, result.stderr
        assert "docker login" in calls(env)
        assert "ghcr.io" in calls(env)

    def test_it_sends_the_token_on_stdin(self, env):
        login(env)
        assert env["stdin"].read_text().strip() == TOKEN

    def test_it_uses_password_stdin(self, env):
        login(env)
        assert "--password-stdin" in calls(env)

    def test_the_token_never_appears_in_argv(self, env):
        """Arguments are world-readable in `ps` for the life of the call."""
        login(env)
        assert TOKEN not in calls(env), "the token was passed as an argument"

    def test_the_token_never_appears_in_output(self, env):
        result = login(env)
        assert TOKEN not in result.stdout
        assert TOKEN not in result.stderr

    def test_the_username_is_passed(self, env):
        login(env)
        assert USERNAME in calls(env)


class TestFailsClosed:
    def test_a_missing_credential_file_fails(self, env):
        result = login(env, credential=env["tmp"] / "absent.env")
        assert result.returncode != 0
        assert "docker login" not in calls(env)

    def test_a_world_readable_credential_fails(self, env):
        env["credential"].chmod(0o644)
        result = login(env)
        assert result.returncode != 0
        assert "docker login" not in calls(env), "authenticated with a readable secret"

    def test_a_group_readable_credential_fails(self, env):
        env["credential"].chmod(0o640)
        assert login(env).returncode != 0

    @pytest.mark.skipif(os.geteuid() != 0, reason="needs root to change ownership")
    def test_a_credential_not_owned_by_root_fails(self, env):
        os.chown(env["credential"], 65534, 65534)  # nobody
        result = login(env)
        assert result.returncode != 0
        assert "docker login" not in calls(env)

    def test_a_missing_token_fails(self, env):
        env["credential"].write_text(f"GHCR_USERNAME={USERNAME}\n")
        env["credential"].chmod(0o600)
        result = login(env)
        assert result.returncode != 0
        assert "docker login" not in calls(env)

    def test_a_missing_username_fails(self, env):
        env["credential"].write_text(f"GHCR_TOKEN={TOKEN}\n")
        env["credential"].chmod(0o600)
        assert login(env).returncode != 0

    def test_an_empty_token_fails(self, env):
        env["credential"].write_text(f"GHCR_USERNAME={USERNAME}\nGHCR_TOKEN=\n")
        env["credential"].chmod(0o600)
        result = login(env)
        assert result.returncode != 0
        assert "docker login" not in calls(env)

    def test_a_malformed_file_fails(self, env):
        env["credential"].write_text("this is not a key value file\n")
        env["credential"].chmod(0o600)
        assert login(env).returncode != 0

    def test_a_rejected_credential_fails(self, env):
        """A wrong token must stop the deploy, not fall through to the pull."""
        (env["tmp"] / "login_status").write_text("1")
        assert login(env).returncode != 0

    def test_a_failure_does_not_print_the_token(self, env):
        (env["tmp"] / "login_status").write_text("1")
        result = login(env)
        assert TOKEN not in result.stdout
        assert TOKEN not in result.stderr


class TestItNeverSourcesTheCredential:
    def test_the_file_is_parsed_rather_than_executed(self, env):
        """Sourcing runs arbitrary code as root.

        The file is root-owned, so this is defence in depth rather than the
        only thing standing in the way — but a credential file is data, and
        parsing it as data costs nothing.
        """
        marker = env["tmp"] / "sourced"
        env["credential"].write_text(
            f"GHCR_USERNAME={USERNAME}\n"
            f"GHCR_TOKEN={TOKEN}\n"
            f"touch {marker}\n"
        )
        env["credential"].chmod(0o600)
        login(env)
        assert not marker.exists(), "the credential file was executed"

    def test_the_library_does_not_source_it(self):
        code = REGISTRY_LIB.read_text()
        for line in code.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith("source "), line
            assert not stripped.startswith(". "), line


class TestNoRelianceOnAmbientDockerLogin:
    def test_it_authenticates_every_time(self, env):
        """No cache, no "already logged in" short-circuit.

        A short-circuit would reintroduce exactly the bug this replaces: pulls
        working off /root/.docker/config.json until the day that token expires.
        """
        login(env)
        login(env)
        assert calls(env).count("docker login") == 2

    def test_it_does_not_read_the_docker_config(self):
        """Executable lines only.

        The comments in this library explain the ambient-login bug it exists
        to prevent, and naming /root/.docker/config.json there is the point.
        A scan that read its own rationale back as a finding would report the
        explanation as the defect.
        """
        code = executable_lines(REGISTRY_LIB)
        assert "config.json" not in code
        assert ".docker" not in code


class TestCredentialRemainsOutOfReachOfTheDeployUser:
    """0600 root:root is what makes it unreadable by `deploy`.

    The helper refuses to use a credential that is not exactly that, so the
    property is enforced at every deploy rather than only at bootstrap.
    """

    def test_ownership_is_checked_against_the_deploying_user(self):
        """On a deploy host sudo makes that root, so this is "owned by root"
        in production. Written as EUID rather than a literal 0 so the check is
        the real invariant -- no account but the one performing the deploy can
        read it -- and so the tests exercise the same code path CI runs."""
        code = executable_lines(REGISTRY_LIB)
        assert '"$EUID"' in code

    def test_there_is_no_override_for_the_ownership_or_mode_checks(self):
        """A knob to relax these would be a test-only escape hatch that ships.

        Only the file path and the registry may come from the environment.
        """
        code = executable_lines(REGISTRY_LIB)
        overridable = {
            line.split("=", 1)[0].strip()
            for line in code.splitlines()
            if ":-" in line and line.strip().startswith("PI_")
        }
        assert overridable <= {"PI_GHCR_REGISTRY", "PI_GHCR_CREDENTIAL_FILE"}, overridable

    def test_the_helper_requires_root_ownership_and_0600(self):
        code = executable_lines(REGISTRY_LIB)
        assert "'%u'" in code, "ownership is never checked"
        assert "'%a'" in code, "mode is never checked"
        assert '"600"' in code

    def test_the_documentation_states_the_ownership_and_mode(self):
        text = " ".join((REPO_ROOT / "docs" / "DEPLOY.md").read_text().split())
        assert "/etc/product-intelligence/ghcr.env" in text
        assert "0600 root:root" in text
        assert "`deploy` user cannot read it" in text

    def test_the_documentation_states_the_schema(self):
        """Without the key names, a correctly-permissioned file is still useless."""
        text = (REPO_ROOT / "docs" / "DEPLOY.md").read_text()
        assert "GHCR_USERNAME" in text
        assert "GHCR_TOKEN" in text


class TestTheControlPlaneShipsTheHelper:
    def test_the_installer_installs_the_library(self):
        installer = (REPO_ROOT / "deploy" / "scripts" / "pi-install-control-plane").read_text()
        assert "lib/*.sh" in installer, "registry.sh would not reach the control plane"

    def test_both_deploy_scripts_source_it(self):
        for name in ("pi-deploy-staging", "pi-deploy-production"):
            code = (REPO_ROOT / "deploy" / "scripts" / name).read_text()
            assert "registry.sh" in code, name
            assert "pi_registry_login" in code, name


class TestTheBootstrapProcedureIsValid:
    def test_the_plan_does_not_instruct_an_empty_password_login(self):
        """`--password-stdin < /dev/null` sends an empty password and proves
        nothing -- on a host with a cached login it can even appear to succeed.

        The phrase survives in the plan only inside the warning that says not
        to use it, so this checks instruction lines rather than prose.
        """
        plan = REPO_ROOT / "docs" / "superpowers" / "plans"
        for path in plan.glob("*.md"):
            for line in path.read_text().splitlines():
                stripped = line.strip()
                if not stripped.startswith(("docker login", "sudo docker login")):
                    continue
                assert "/dev/null" not in stripped, f"{path.name}: {stripped}"

    def test_the_plan_verifies_through_the_real_code_path(self):
        text = " ".join(
            p.read_text() for p in (REPO_ROOT / "docs" / "superpowers" / "plans").glob("*.md")
        )
        assert "pi_registry_login" in text, "L06 never exercises the deploy path's own helper"
