"""Preflight: everything that must be true before anything is mutated.

Every function here is read-only. If one fails, the current release is still
running and untouched — that is the whole point of doing these checks before
the pull, the migration and the recreate.

Docker is stubbed by putting a fake `docker` on PATH, so these tests run with
no daemon. The fake records its argv and environment to files the tests read
back, which is how assertions like "the compose check ran without
COMPOSE_PROJECT_NAME" are made about the call itself rather than its result.

The compatibility matrix is the sharpest thing in this file. Exactly one label
value may pass, and eight ways of nearly saying yes must not.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "deploy" / "scripts" / "lib"
PREFLIGHT_LIB = LIB_DIR / "preflight.sh"

SHA = "a" * 40
OTHER_SHA = "b" * 40
API_IMAGE = "ghcr.io/imiladco/product-intelligence/api@sha256:" + "1" * 64

COMPAT_LABEL = "org.arkav.pi.migrations-backward-compatible"
REVISION_LABEL = "org.opencontainers.image.revision"

REQUIRED_ENV_KEYS = [
    "APP_DOMAIN", "APP_URL", "DJANGO_DEBUG", "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "POSTGRES_DB",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL",
    "CREDENTIAL_ENCRYPTION_KEYS", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI", "GUNICORN_WORKERS", "ACME_EMAIL",
]


@pytest.fixture
def fake_docker(tmp_path: Path):
    """A stub `docker` on PATH that records how it was called.

    Behaviour is driven by files the test writes:
      inspect_output  — what `image inspect --format …` prints
      inspect_status  — its exit status
      info_status     — what `docker info` returns
      config_name     — the project name `compose config` reports
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    env_log = tmp_path / "env.log"

    (bin_dir / "docker").write_text(f"""#!/usr/bin/env bash
echo "$@" >> {calls}
env | grep -E '^COMPOSE_PROJECT_NAME=' >> {env_log} || true
case "$1 $2" in
  "image inspect")
      cat {tmp_path}/inspect_output 2>/dev/null || true
      exit "$(cat {tmp_path}/inspect_status 2>/dev/null || echo 0)"
      ;;
  "info "*|"info")
      exit "$(cat {tmp_path}/info_status 2>/dev/null || echo 0)"
      ;;
  "compose "*)
      # `compose -f X config --format json`
      printf '{{"name": "%s"}}' "$(cat {tmp_path}/config_name 2>/dev/null || echo product-intelligence-staging)"
      exit 0
      ;;
  "volume "*|"network "*)
      exit "$(cat {tmp_path}/resource_status 2>/dev/null || echo 0)"
      ;;
  *) exit 0 ;;
esac
""")
    (bin_dir / "docker").chmod(0o755)
    return {"bin": bin_dir, "calls": calls, "env_log": env_log, "tmp": tmp_path}


def run(fake, snippet: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    env = {
        "PATH": f"{fake['bin']}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(fake["tmp"]),
    }
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", "-c", f"source {PREFLIGHT_LIB}; {snippet}"],
        capture_output=True, text=True, timeout=30, env=env,
    )


def set_label(fake, value: str | None, status: int = 0):
    if value is None:
        (fake["tmp"] / "inspect_output").write_text("")
    else:
        (fake["tmp"] / "inspect_output").write_text(value + "\n")
    (fake["tmp"] / "inspect_status").write_text(str(status))


def write_env_file(path: Path, *, omit: str | None = None, empty: str | None = None,
                   mode: int = 0o600):
    lines = []
    for key in REQUIRED_ENV_KEYS:
        if key == omit:
            continue
        value = "" if key == empty else f"value-for-{key}"
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")
    path.chmod(mode)


class TestMigrationCompatibilityFailsClosed:
    """Exactly one value may pass. Everything else refuses.

    `false` is an honest no. The rest are ways of nearly saying yes — a
    capitalised boolean, a shell-style 1, an empty string from a missing
    label, an inspect that could not run at all. Treating any of them as
    consent would deploy a release whose migrations may break the previous
    application while it is still serving traffic.
    """

    def test_exactly_true_is_accepted(self, fake_docker):
        set_label(fake_docker, "true")
        result = run(fake_docker, f'pi_preflight_migration_compatibility "{API_IMAGE}"')
        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        "label,status",
        [
            ("false", 0),
            ("", 0),
            ("True", 0),
            ("TRUE", 0),
            ("1", 0),
            ("yes", 0),
            ("<no value>", 0),   # docker's rendering of an absent label
            ("true ", 0),        # trailing whitespace is not the same string
            (None, 1),           # inspect itself failed
        ],
        ids=["false", "empty", "True", "TRUE", "one", "yes", "absent", "padded", "inspect-fails"],
    )
    def test_everything_else_is_refused(self, fake_docker, label, status):
        set_label(fake_docker, label, status)
        result = run(fake_docker, f'pi_preflight_migration_compatibility "{API_IMAGE}"')
        assert result.returncode != 0, f"accepted {label!r}"


class TestImageRevision:
    def test_matching_revision_is_accepted(self, fake_docker):
        set_label(fake_docker, SHA)
        assert run(fake_docker, f'pi_preflight_image_revision "{API_IMAGE}" "{SHA}"').returncode == 0

    def test_revision_mismatch_is_rejected(self, fake_docker):
        """A digest from another commit must not be promoted under this SHA."""
        set_label(fake_docker, OTHER_SHA)
        assert run(fake_docker, f'pi_preflight_image_revision "{API_IMAGE}" "{SHA}"').returncode != 0

    def test_unstamped_image_is_rejected(self, fake_docker):
        """`unknown` is the Dockerfile default and can never equal a SHA."""
        set_label(fake_docker, "unknown")
        assert run(fake_docker, f'pi_preflight_image_revision "{API_IMAGE}" "{SHA}"').returncode != 0

    def test_inspect_failure_is_rejected(self, fake_docker):
        set_label(fake_docker, None, status=1)
        assert run(fake_docker, f'pi_preflight_image_revision "{API_IMAGE}" "{SHA}"').returncode != 0


class TestEnvFile:
    def test_a_complete_file_with_the_right_mode_passes(self, fake_docker, tmp_path):
        env_file = tmp_path / ".env"
        write_env_file(env_file)
        assert run(fake_docker, f'pi_preflight_env_file "{env_file}"').returncode == 0

    def test_a_missing_file_is_rejected(self, fake_docker, tmp_path):
        assert run(fake_docker, f'pi_preflight_env_file "{tmp_path}/.env"').returncode != 0

    def test_a_world_readable_file_is_rejected(self, fake_docker, tmp_path):
        env_file = tmp_path / ".env"
        write_env_file(env_file, mode=0o644)
        result = run(fake_docker, f'pi_preflight_env_file "{env_file}"')
        assert result.returncode != 0
        assert "0600" in result.stdout + result.stderr

    def test_a_missing_key_is_reported_by_name(self, fake_docker, tmp_path):
        env_file = tmp_path / ".env"
        write_env_file(env_file, omit="CREDENTIAL_ENCRYPTION_KEYS")
        result = run(fake_docker, f'pi_preflight_env_file "{env_file}"')
        assert result.returncode != 0
        assert "CREDENTIAL_ENCRYPTION_KEYS" in result.stdout + result.stderr

    def test_an_empty_value_is_rejected(self, fake_docker, tmp_path):
        env_file = tmp_path / ".env"
        write_env_file(env_file, empty="DJANGO_SECRET_KEY")
        result = run(fake_docker, f'pi_preflight_env_file "{env_file}"')
        assert result.returncode != 0
        assert "DJANGO_SECRET_KEY" in result.stdout + result.stderr

    def test_no_value_is_ever_printed(self, fake_docker, tmp_path):
        """Names and booleans only. A preflight that echoed a value would put
        the database password in a CI log."""
        env_file = tmp_path / ".env"
        write_env_file(env_file, empty="DJANGO_SECRET_KEY")
        result = run(fake_docker, f'pi_preflight_env_file "{env_file}"')
        assert "value-for-" not in result.stdout + result.stderr


class TestComposeProjectName:
    def test_a_matching_project_name_passes(self, fake_docker, tmp_path):
        (fake_docker["tmp"] / "config_name").write_text("product-intelligence-staging")
        result = run(fake_docker, f'pi_preflight_compose_project "{tmp_path}/compose.yaml" "product-intelligence-staging"')
        assert result.returncode == 0, result.stderr

    def test_a_mismatched_project_name_is_rejected(self, fake_docker, tmp_path):
        (fake_docker["tmp"] / "config_name").write_text("something-else")
        result = run(fake_docker, f'pi_preflight_compose_project "{tmp_path}/compose.yaml" "product-intelligence-staging"')
        assert result.returncode != 0

    def test_the_check_runs_without_compose_project_name(self, fake_docker, tmp_path):
        """COMPOSE_PROJECT_NAME overrides the manifest's `name:` key.

        An inherited value would redirect every volume lookup to a project that
        does not exist, which — were the volumes not external — is how an empty
        database appears. The check must strip it, not merely hope it is unset.
        """
        (fake_docker["tmp"] / "config_name").write_text("product-intelligence-staging")
        run(
            fake_docker,
            f'pi_preflight_compose_project "{tmp_path}/compose.yaml" "product-intelligence-staging"',
            extra_env={"COMPOSE_PROJECT_NAME": "hijacked"},
        )
        recorded = fake_docker["env_log"]
        assert not recorded.exists() or recorded.read_text().strip() == ""

    def test_the_check_never_passes_dash_p(self, fake_docker, tmp_path):
        (fake_docker["tmp"] / "config_name").write_text("product-intelligence-staging")
        run(fake_docker, f'pi_preflight_compose_project "{tmp_path}/compose.yaml" "product-intelligence-staging"')
        calls = fake_docker["calls"].read_text()
        assert " -p " not in calls


class TestDiskAndDaemon:
    def test_docker_daemon_available_passes(self, fake_docker):
        (fake_docker["tmp"] / "info_status").write_text("0")
        assert run(fake_docker, "pi_preflight_docker_available").returncode == 0

    def test_docker_daemon_unavailable_is_rejected(self, fake_docker):
        (fake_docker["tmp"] / "info_status").write_text("1")
        assert run(fake_docker, "pi_preflight_docker_available").returncode != 0

    def test_ample_disk_passes(self, fake_docker):
        assert run(fake_docker, "pi_preflight_disk_free 1").returncode == 0

    def test_an_impossible_floor_is_rejected(self, fake_docker):
        """A pull that fills the disk can damage unrelated services on this
        host, so the floor is checked before anything is fetched."""
        assert run(fake_docker, "pi_preflight_disk_free 999999").returncode != 0


class TestSourceLevelGuarantees:
    def test_preflight_mutates_nothing(self):
        """No pull, no run, no up, no rm anywhere in this library."""
        code = "\n".join(
            line for line in PREFLIGHT_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        for forbidden in ("docker pull", "docker run", "docker rm", "compose up", "compose down"):
            assert forbidden not in code, forbidden

    def test_no_eval(self):
        code = "\n".join(
            line for line in PREFLIGHT_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "eval " not in code
