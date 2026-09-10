"""The control plane: what only a root human may install, and why.

The privileged deployment code is what enforces every other rule in this
system. If automatic staging CI could replace it, a compromised staging
credential — or an ordinary mistake merged to main — would rewrite the very
guardrails it is bound by, including production's refusal to deploy an
incompatible release and the environment binding on the SSH keys.

So the separation is by *what may write*, not by intent:

  Tier A, control plane, admin-installed only:
      authorized_keys forced commands, sudoers, the wrapper, the deploy
      scripts, the privileged libraries, compose manifests, the Caddyfile.

  Tier B, release data, all an automatic deploy may supply or write:
      a SHA, two digests, the compatibility label read off the image, and
      ledger entries.

The last test in this file is the one that matters most: no workflow may
install anything in Tier A.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "deploy" / "scripts"
WRAPPER = SCRIPTS / "pi-deploy-wrapper"
INSTALLER = SCRIPTS / "pi-install-control-plane"
SUDOERS = REPO_ROOT / "deploy" / "sudoers" / "pi-deploy"
AUTHORIZED_KEYS = REPO_ROOT / "deploy" / "ssh" / "authorized_keys.template"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

SHA = "a" * 40
API_DIGEST = "sha256:" + "1" * 64
WEB_DIGEST = "sha256:" + "2" * 64


def executable_lines(path: Path) -> str:
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.strip().startswith("#")
    )


@pytest.fixture
def harness(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"

    # sudo records what it was asked to run and does not run it.
    (bin_dir / "sudo").write_text(f"""#!/usr/bin/env bash
echo "sudo $@" >> {calls}
exit 0
""")
    (bin_dir / "sudo").chmod(0o755)
    return {"bin": bin_dir, "calls": calls, "tmp": tmp_path}


def run_wrapper(harness, environment: str, client_command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(WRAPPER), environment],
        capture_output=True, text=True, timeout=30,
        env={
            "PATH": f"{harness['bin']}:/usr/bin:/bin",
            "SSH_ORIGINAL_COMMAND": client_command,
            "PI_LIB_DIR": str(SCRIPTS / "lib"),
        },
    )


def calls(harness) -> str:
    return harness["calls"].read_text() if harness["calls"].exists() else ""


class TestWrapperBindsTheEnvironment:
    def test_the_environment_comes_from_the_argument_not_the_client(self, harness):
        """A staging key stays a staging key whatever it sends."""
        result = run_wrapper(harness, "staging", f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        assert result.returncode == 0, result.stderr
        assert "pi-deploy-staging" in calls(harness)
        assert "pi-deploy-production" not in calls(harness)

    def test_a_client_naming_production_still_gets_staging(self, harness):
        run_wrapper(harness, "staging", f"production deploy {SHA}")
        assert "pi-deploy-production" not in calls(harness)

    def test_a_client_cannot_smuggle_an_environment_switch(self, harness):
        for payload in (
            f"deploy {SHA} {API_DIGEST} {WEB_DIGEST} production",
            f"--environment production deploy {SHA}",
            f"deploy {SHA}; /usr/local/sbin/pi-deploy-production deploy {SHA}",
        ):
            harness["calls"].write_text("")
            run_wrapper(harness, "staging", payload)
            assert "pi-deploy-production" not in calls(harness), payload

    def test_an_unknown_environment_is_rejected(self, harness):
        for environment in ("", "prod", "PRODUCTION", "../production", "staging production"):
            result = run_wrapper(harness, environment, f"deploy {SHA}")
            assert result.returncode != 0, environment
            assert "sudo" not in calls(harness)


class TestWrapperNeverExecutesTheClientString:
    def test_no_eval_and_no_shell_interpretation(self):
        code = executable_lines(WRAPPER)
        assert "eval " not in code
        assert "bash -c" not in code
        assert "sh -c" not in code

    def test_the_client_string_is_never_run_as_a_command(self):
        code = executable_lines(WRAPPER)
        # It may be READ and passed on; it must never be the command itself.
        assert not re.search(r'^\s*\$SSH_ORIGINAL_COMMAND', code, re.MULTILINE)
        assert not re.search(r'^\s*"\$SSH_ORIGINAL_COMMAND"', code, re.MULTILINE)

    def test_injection_reaches_no_shell(self, harness):
        run_wrapper(harness, "staging", "deploy $(touch /tmp/pi_wrapper_pwn)")
        assert not Path("/tmp/pi_wrapper_pwn").exists()


class TestSudoers:
    def test_it_grants_exactly_two_commands(self):
        line = SUDOERS.read_text().strip()
        assert line.count("/usr/local/sbin/pi-deploy-staging") == 1
        assert line.count("/usr/local/sbin/pi-deploy-production") == 1

    def test_it_grants_nothing_else(self):
        text = SUDOERS.read_text()
        assert "NOPASSWD: ALL" not in text
        assert "(ALL) ALL" not in text
        assert "*" not in text, "a wildcard would widen this grant unpredictably"

    def test_it_targets_the_deploy_user_only(self):
        assert SUDOERS.read_text().strip().startswith("deploy ")

    def test_it_is_syntactically_valid(self):
        """visudo if available; exact-text comparison otherwise."""
        import shutil
        if shutil.which("visudo"):
            result = subprocess.run(
                ["visudo", "-cf", str(SUDOERS)], capture_output=True, text=True
            )
            assert result.returncode == 0, result.stdout + result.stderr
        else:
            expected = (
                "deploy ALL=(root) NOPASSWD: /usr/local/sbin/pi-deploy-staging, "
                "/usr/local/sbin/pi-deploy-production"
            )
            assert SUDOERS.read_text().strip() == expected


class TestAuthorizedKeysTemplate:
    def test_both_lines_bind_an_environment_in_the_forced_command(self):
        text = AUTHORIZED_KEYS.read_text()
        assert 'command="/usr/local/bin/pi-deploy-wrapper staging"' in text
        assert 'command="/usr/local/bin/pi-deploy-wrapper production"' in text

    def test_both_lines_restrict_the_session(self):
        for line in AUTHORIZED_KEYS.read_text().splitlines():
            if line.startswith("command="):
                assert "restrict" in line, line
                assert "no-pty" in line, line

    def test_no_forwarding_is_re_enabled(self):
        text = AUTHORIZED_KEYS.read_text()
        for reenabled in ("port-forwarding", "agent-forwarding", "X11-forwarding", "pty"):
            assert f",{reenabled}" not in text

    def test_it_contains_no_real_key(self):
        text = AUTHORIZED_KEYS.read_text()
        assert "PUBLIC_KEY_HERE" in text
        for line in text.splitlines():
            if line.startswith("command="):
                blobs = [w for w in line.split() if len(w) > 40 and w.startswith("AAAA")]
                assert blobs == [], "a real public key was committed"


class TestInstaller:
    def test_it_requires_root(self):
        code = executable_lines(INSTALLER)
        assert "EUID" in code

    def test_it_validates_the_sudoers_file_before_installing_it(self):
        """An invalid sudoers file locks everyone out of sudo on the host."""
        code = executable_lines(INSTALLER)
        assert "visudo -cf" in code

    def test_it_records_the_installed_commit(self):
        assert "control-plane.json" in INSTALLER.read_text()

    def test_it_never_touches_env_files_or_the_ledger(self):
        """Scoped to file operations, not to mentions.

        The installer prints what it deliberately does *not* install, and that
        message names .env and the ledger. What must not exist is an install,
        copy, move, delete or redirect that targets them.
        """
        operations = [
            line for line in executable_lines(INSTALLER).splitlines()
            if re.search(r"\b(install|cp|mv|rm|tee|truncate)\b", line) or ">" in line
        ]
        for line in operations:
            assert ".env" not in line, f"installer writes an env file: {line.strip()}"
            assert "staging.json" not in line, f"installer writes the ledger: {line.strip()}"
            assert "production.json" not in line, f"installer writes the ledger: {line.strip()}"

    def test_it_installs_every_tier_a_artifact(self):
        code = INSTALLER.read_text()
        for artifact in (
            "pi-deploy-wrapper",
            "pi-deploy-staging",
            "pi-deploy-production",
            "compose.staging.yaml",
            "compose.production.yaml",
            "Caddyfile",
        ):
            assert artifact in code, artifact

    def test_it_does_not_install_the_provenance_script(self):
        """That one runs in CI, never on the VPS. Installing it would give the
        control plane a component CI also controls."""
        assert "verify-image-provenance" not in INSTALLER.read_text()


class TestCiCannotTouchTheControlPlane:
    """The guard this whole file exists for.

    Re-run by Task 19 once the workflows are real. Until then the directory may
    not exist, and the assertion is vacuously true — which is honest: there is
    no workflow yet that could violate it.
    """

    def test_no_workflow_installs_the_control_plane(self):
        if not WORKFLOWS.is_dir():
            pytest.skip("no workflows yet; re-run at Task 19")

        forbidden = [
            "pi-install-control-plane",
            "authorized_keys",
            "sudoers",
            "/usr/local/sbin",
            "/usr/local/lib/pi-deploy",
            "/usr/local/bin/pi-deploy",
            "scp ",
            "rsync ",
        ]
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            text = workflow.read_text()
            for token in forbidden:
                assert token not in text, f"{workflow.name} touches the control plane: {token}"
