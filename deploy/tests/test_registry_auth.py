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
echo "docker $@ [DOCKER_CONFIG=${{DOCKER_CONFIG-unset}}]" >> {calls}
if [[ "$1" == "login" ]]; then
  cat >> {stdin_seen}
  # Record what a real client would persist, so a test can prove the
  # credential lands in the ephemeral config and not the default one.
  if [[ -n "${{DOCKER_CONFIG-}}" ]]; then
    printf '{{"auths":{{"ghcr.io":{{"auth":"persisted"}}}}}}' > "$DOCKER_CONFIG/config.json"
  fi
  exit "$(cat {tmp_path}/login_status 2>/dev/null || echo 0)"
fi
exit "$(cat {tmp_path}/pull_status 2>/dev/null || echo 0)"
""")
    (bin_dir / "docker").chmod(0o755)

    scratch = tmp_path / "scratch"
    scratch.mkdir()

    credential = tmp_path / "ghcr.env"
    credential.write_text(f"GHCR_USERNAME={USERNAME}\nGHCR_TOKEN={TOKEN}\n")
    credential.chmod(0o600)

    return {
        "bin": bin_dir,
        "tmp": tmp_path,
        "calls": calls,
        "stdin": stdin_seen,
        "credential": credential,
        "scratch": scratch,
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
            "TMPDIR": str(env["scratch"]),
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


def docker_configs(env) -> list[str]:
    """The DOCKER_CONFIG each docker invocation actually ran with."""
    seen = []
    for line in calls(env).splitlines():
        if "[DOCKER_CONFIG=" in line:
            seen.append(line.rsplit("[DOCKER_CONFIG=", 1)[1].rstrip("]"))
    return seen


def session(env, body: str) -> subprocess.CompletedProcess:
    """Run a full authenticate → pull → cleanup lifecycle in one shell."""
    script = f'source "{REGISTRY_LIB}"\n{body}'
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env={
            "PATH": f"{env['bin']}:/usr/bin:/bin",
            "PI_GHCR_CREDENTIAL_FILE": str(env["credential"]),
            "HOME": str(env["tmp"] / "home"),
            "TMPDIR": str(env["scratch"]),
        },
    )


class TestEphemeralDockerConfig:
    """`docker login` persists credentials into the Docker config it is given.

    Left at the default, that writes the token into /root/.docker/config.json,
    where it outlives the deploy and duplicates the secret outside the one file
    that is supposed to hold it. Each authentication therefore gets its own
    throwaway config directory, and the pulls run inside the same one.
    """

    def test_login_runs_with_an_isolated_docker_config(self, env):
        result = session(env, "pi_registry_login")
        assert result.returncode == 0, result.stderr
        configs = docker_configs(env)
        assert configs, "no docker call recorded"
        assert configs[0] not in ("unset", ""), "login used the default Docker config"

    def test_the_directory_is_private(self, env):
        result = session(
            env, 'pi_registry_login && stat -c "%a" "$DOCKER_CONFIG"'
        )
        assert result.stdout.strip().endswith("700"), result.stdout

    def test_login_and_both_pulls_share_one_config(self, env):
        session(env, (
            "pi_registry_login\n"
            "docker pull repo/api@sha256:aaa\n"
            "docker pull repo/web@sha256:bbb\n"
            "pi_registry_logout"
        ))
        configs = docker_configs(env)
        assert len(configs) == 3, configs
        assert len(set(configs)) == 1, f"the pulls did not reuse the login's config: {configs}"

    def test_the_directory_is_removed_after_success(self, env):
        result = session(env, (
            'pi_registry_login\n'
            'saved="$DOCKER_CONFIG"\n'
            'pi_registry_logout\n'
            '[[ -e "$saved" ]] && echo LEFT_BEHIND || echo REMOVED'
        ))
        assert "REMOVED" in result.stdout, result.stdout

    def test_the_directory_is_removed_when_login_fails(self, env):
        """Scoped to this test's own TMPDIR.

        Globbing /tmp instead would make the assertion depend on whatever else
        the machine has lying around -- which is exactly how this test first
        failed, on leftovers from an unrelated run.
        """
        (env["tmp"] / "login_status").write_text("1")
        result = session(env, "pi_registry_login || true")
        assert result.returncode == 0
        leftovers = list(env["scratch"].glob("pi-registry-*"))
        assert leftovers == [], f"a session directory survived a failed login: {leftovers}"

    def test_the_directory_is_removed_when_a_pull_fails_and_the_shell_exits(self, env):
        """The EXIT trap is the backstop: a deploy that aborts between login
        and logout must not leave an authenticated config on disk."""
        (env["tmp"] / "pull_status").write_text("1")
        marker = env["tmp"] / "config_path"
        session(env, (
            'pi_registry_login\n'
            f'printf "%s" "$DOCKER_CONFIG" > {marker}\n'
            'docker pull repo/api@sha256:aaa || exit 1\n'
        ))
        left = Path(marker.read_text())
        assert not left.exists(), f"an authenticated Docker config survived: {left}"

    def test_the_default_docker_config_is_never_written(self, env):
        home = env["tmp"] / "home"
        home.mkdir(exist_ok=True)
        session(env, "pi_registry_login && pi_registry_logout")
        assert not (home / ".docker").exists(), "the default Docker config was created"

    def test_the_default_docker_config_is_not_required(self, env):
        """A host that has never run `docker login` must deploy fine."""
        assert not (env["tmp"] / "home" / ".docker").exists()
        assert session(env, "pi_registry_login").returncode == 0

    def test_the_token_is_not_in_the_call_log(self, env):
        session(env, "pi_registry_login && pi_registry_logout")
        assert TOKEN not in calls(env)


class TestStrictSchema:
    """Exactly two keys, each exactly once, nothing else.

    A parser that hunts for the keys it wants and ignores the rest will
    happily accept a file with a typo'd second token, a stray assignment, or
    a duplicate — and silently use whichever line it happened to reach first.
    """

    def write(self, env, content: str):
        env["credential"].write_text(content)
        env["credential"].chmod(0o600)

    def test_the_documented_schema_is_accepted(self, env):
        self.write(env, f"GHCR_USERNAME={USERNAME}\nGHCR_TOKEN={TOKEN}\n")
        assert login(env).returncode == 0

    def test_comments_and_blank_lines_are_accepted(self, env):
        self.write(env, (
            "# GHCR read-only credential\n"
            "\n"
            f"GHCR_USERNAME={USERNAME}\n"
            "   \n"
            "# the token below is read-only\n"
            f"GHCR_TOKEN={TOKEN}\n"
        ))
        assert login(env).returncode == 0, "comments and blank lines must stay legal"

    def test_an_unknown_key_is_rejected(self, env):
        self.write(env, f"GHCR_USERNAME={USERNAME}\nGHCR_TOKEN={TOKEN}\nGHCR_REGISTRY=example.com\n")
        result = login(env)
        assert result.returncode != 0
        assert "docker login" not in calls(env)

    def test_a_duplicate_username_is_rejected(self, env):
        self.write(env, f"GHCR_USERNAME={USERNAME}\nGHCR_USERNAME=someone-else\nGHCR_TOKEN={TOKEN}\n")
        result = login(env)
        assert result.returncode != 0
        assert "docker login" not in calls(env)

    def test_a_duplicate_token_is_rejected(self, env):
        self.write(env, f"GHCR_USERNAME={USERNAME}\nGHCR_TOKEN={TOKEN}\nGHCR_TOKEN=another\n")
        result = login(env)
        assert result.returncode != 0
        assert "docker login" not in calls(env)

    def test_a_malformed_line_is_rejected(self, env):
        self.write(env, f"GHCR_USERNAME={USERNAME}\nthis line has no equals sign\nGHCR_TOKEN={TOKEN}\n")
        result = login(env)
        assert result.returncode != 0
        assert "docker login" not in calls(env)

    def test_an_indented_assignment_is_rejected(self, env):
        """Leading whitespace usually means a heredoc was pasted wrong."""
        self.write(env, f"  GHCR_USERNAME={USERNAME}\nGHCR_TOKEN={TOKEN}\n")
        assert login(env).returncode != 0

    def test_a_missing_key_is_rejected(self, env):
        self.write(env, f"GHCR_USERNAME={USERNAME}\n")
        assert login(env).returncode != 0

    def test_an_empty_value_is_rejected(self, env):
        self.write(env, f"GHCR_USERNAME=\nGHCR_TOKEN={TOKEN}\n")
        assert login(env).returncode != 0

    def test_a_rejection_never_prints_the_token(self, env):
        self.write(env, f"GHCR_USERNAME={USERNAME}\nGHCR_TOKEN={TOKEN}\nSTRAY=1\n")
        result = login(env)
        assert TOKEN not in result.stdout
        assert TOKEN not in result.stderr


class TestDocumentationMatchesTheImplementation:
    """The last review found documentation describing a mechanism that did not
    exist. These assert the reverse cannot happen quietly again."""

    def doc(self) -> str:
        return " ".join((REPO_ROOT / "docs" / "DEPLOY.md").read_text().split())

    def test_it_documents_the_ephemeral_docker_config(self):
        text = self.doc()
        assert "DOCKER_CONFIG" in text
        assert "0700" in text

    def test_it_documents_that_the_root_docker_config_is_untouched(self):
        text = self.doc()
        assert "/root/.docker/config.json" in text
        assert "never read, never written" in text

    def test_it_documents_the_strict_schema(self):
        text = self.doc()
        assert "each exactly once" in text

    def test_it_documents_the_source_of_truth(self):
        text = self.doc()
        assert "Source of truth" in text
        assert "/etc/product-intelligence/ghcr.env" in text
