"""The staging deploy entry point, end to end with everything stubbed.

The properties that matter here are about *order* and *proof*:

  * Images are pulled **by digest**, never by tag. A tag can move between the
    push and the pull; a digest cannot.
  * Postgres is up before the `--no-deps` migration runs, or the one-shot has
    no database to migrate — which is also what makes the very first production
    deploy work.
  * Migration runs before the application is recreated, so a failure never
    leaves the candidate serving.
  * A failed health gate writes **no** successful ledger entry. Production's
    entire safety argument rests on that entry meaning what it says.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "scripts" / "pi-deploy-staging"
LIB_DIR = REPO_ROOT / "deploy" / "scripts" / "lib"

SHA = "a" * 40
API_DIGEST = "sha256:" + "1" * 64
WEB_DIGEST = "sha256:" + "2" * 64
API_REPO = "ghcr.io/imiladco/product-intelligence/api"
WEB_REPO = "ghcr.io/imiladco/product-intelligence/web"

REQUIRED_ENV_KEYS = [
    "APP_DOMAIN", "APP_URL", "DJANGO_DEBUG", "DJANGO_SECRET_KEY",
    "DJANGO_ALLOWED_HOSTS", "CSRF_TRUSTED_ORIGINS", "POSTGRES_DB",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "DATABASE_URL",
    "CREDENTIAL_ENCRYPTION_KEYS", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
    "GOOGLE_OAUTH_REDIRECT_URI", "GUNICORN_WORKERS", "ACME_EMAIL",
]


@pytest.fixture
def env(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    deploy_dir = tmp_path / "staging"
    (deploy_dir / ".release").mkdir(parents=True)
    calls = tmp_path / "calls.log"

    env_file = deploy_dir / ".env"
    env_file.write_text("\n".join(f"{k}=value-for-{k}" for k in REQUIRED_ENV_KEYS) + "\n")
    env_file.chmod(0o600)
    (deploy_dir / "compose.staging.yaml").write_text("name: product-intelligence-staging\n")

    (bin_dir / "docker").write_text(f"""#!/usr/bin/env bash
echo "docker $@" >> {calls}
case "$1 $2" in
  "image inspect")
      # Label lookups: revision, then compatibility.
      if [[ "$*" == *"revision"* ]]; then
        cat {tmp_path}/revision 2>/dev/null || echo "{SHA}"
      else
        cat {tmp_path}/compat 2>/dev/null || echo "true"
      fi
      exit 0 ;;
  "compose "*)
      if [[ "$*" == *"config"* ]]; then
        printf '{{"name": "product-intelligence-staging"}}'
      fi
      exit "$(cat {tmp_path}/compose_status 2>/dev/null || echo 0)" ;;
  "pull "*) exit "$(cat {tmp_path}/pull_status 2>/dev/null || echo 0)" ;;
  *) exit 0 ;;
esac
""")
    (bin_dir / "docker").chmod(0o755)

    (bin_dir / "curl").write_text(f"""#!/usr/bin/env bash
echo "curl $@" >> {calls}
printf '{{"status":"ok","database":"ok","release":"%s"}}' \
  "$(cat {tmp_path}/served_release 2>/dev/null || echo '{SHA}')"
exit "$(cat {tmp_path}/curl_status 2>/dev/null || echo 0)"
""")
    (bin_dir / "curl").chmod(0o755)

    # Emulates `flock -n -E <code> <lockfile> <command...>`: exits with the
    # -E code when the lock is held, otherwise runs the command transparently.
    (bin_dir / "flock").write_text(f"""#!/usr/bin/env bash
code=1
while [[ "$1" == -* ]]; do
  if [[ "$1" == "-E" ]]; then code="$2"; shift 2; else shift; fi
done
shift  # the lock file
if [[ -f {tmp_path}/lock_held ]]; then exit "$code"; fi
exec "$@"
""")
    (bin_dir / "flock").chmod(0o755)

    return {"bin": bin_dir, "tmp": tmp_path, "state": state,
            "deploy": deploy_dir, "calls": calls}


def deploy(env, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, timeout=90,
        env={
            "PATH": f"{env['bin']}:/usr/bin:/bin:/usr/local/bin",
            "SSH_ORIGINAL_COMMAND": command,
            "PI_STATE_DIR": str(env["state"]),
            "PI_DEPLOY_ROOT": str(env["tmp"]),
            "PI_LIB_DIR": str(LIB_DIR),
            "PI_HEALTH_INTERVAL": "0",
            "PI_HEALTH_ATTEMPTS_CONTAINERS": "1",
            "PI_HEALTH_ATTEMPTS_INTERNAL": "1",
            "PI_HEALTH_ATTEMPTS_PUBLIC": "1",
            "PI_HEALTH_ATTEMPTS_ROUTE": "1",
            "PI_SKIP_DISK_CHECK": "1",
            "PI_LOCK_FILE": str(env["tmp"] / "deploy.lock"),
        },
    )


def calls(env) -> str:
    return env["calls"].read_text() if env["calls"].exists() else ""


def ledger(env) -> dict:
    return json.loads((env["state"] / "staging.json").read_text())


class TestGrammar:
    def test_rejects_a_command_without_digests(self, env):
        result = deploy(env, f"deploy {SHA}")
        assert result.returncode == 2
        assert "pull" not in calls(env)

    def test_rejects_an_invalid_digest(self, env):
        result = deploy(env, f"deploy {SHA} notadigest {WEB_DIGEST}")
        assert result.returncode == 2
        assert "pull" not in calls(env)

    def test_rejects_an_invalid_sha(self, env):
        assert deploy(env, f"deploy zzz {API_DIGEST} {WEB_DIGEST}").returncode == 2

    def test_status_reports_without_deploying(self, env):
        result = deploy(env, "status")
        assert result.returncode == 0
        assert "pull" not in calls(env)


class TestDigestOnly:
    def test_pulls_by_digest_not_by_tag(self, env):
        deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        pulls = [line for line in calls(env).splitlines() if " pull " in line]
        assert pulls, "nothing was pulled"
        for line in pulls:
            assert "@sha256:" in line, line
            assert f":{SHA}" not in line, f"pulled by tag: {line}"

    def test_it_pulls_both_images(self, env):
        deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")
        pulls = calls(env)
        assert f"{API_REPO}@{API_DIGEST}" in pulls
        assert f"{WEB_REPO}@{WEB_DIGEST}" in pulls

    def test_the_release_file_records_the_digests(self, env):
        deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")
        images = (env["deploy"] / ".release" / "images.env").read_text()
        assert API_DIGEST in images
        assert WEB_DIGEST in images
        assert f"RELEASE_SHA={SHA}" in images


class TestProvenance:
    def test_revision_is_verified_for_both_images(self, env):
        deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        inspects = [ln for ln in calls(env).splitlines() if "image inspect" in ln and "revision" in ln]
        assert any(API_REPO in ln for ln in inspects), "API revision not checked"
        assert any(WEB_REPO in ln for ln in inspects), "web revision not checked"

    def test_a_mismatching_revision_aborts_before_migrating(self, env):
        (env["tmp"] / "revision").write_text("b" * 40)

        result = deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        assert result.returncode != 0
        assert "migrate" not in calls(env)


class TestOrdering:
    def test_postgres_is_started_before_the_migration(self, env):
        """`run --no-deps` will not start the database for you.

        This is also what makes the first production deploy work: bootstrap
        starts postgres alone, and the ordinary release path does the same.
        """
        deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        lines = calls(env).splitlines()
        postgres_at = next(i for i, ln in enumerate(lines) if "up -d postgres" in ln)
        migrate_at = next(i for i, ln in enumerate(lines) if "manage.py migrate" in ln)
        assert postgres_at < migrate_at

    def test_migration_runs_before_the_app_is_recreated(self, env):
        deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        lines = calls(env).splitlines()
        migrate_at = next(i for i, ln in enumerate(lines) if "manage.py migrate" in ln)
        recreate_at = next(
            i for i, ln in enumerate(lines)
            if "up -d" in ln and "postgres" not in ln
        )
        assert migrate_at < recreate_at

    def test_collectstatic_runs_as_a_one_shot(self, env):
        deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")
        assert "manage.py collectstatic" in calls(env)
        assert "--rm" in calls(env)


class TestLedgerProof:
    def test_a_successful_deploy_records_digests_and_compatibility(self, env):
        result = deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        assert result.returncode == 0, result.stdout + result.stderr
        data = ledger(env)
        assert data["current"]["sha"] == SHA
        assert data["current"]["api_digest"] == API_DIGEST
        assert data["current"]["web_digest"] == WEB_DIGEST
        assert data["current"]["migrations_backward_compatible"] == "true"

    def test_a_failing_health_gate_writes_no_successful_entry(self, env):
        """The false-proof guard: production trusts this entry completely."""
        (env["tmp"] / "served_release").write_text("b" * 40)  # wrong release

        result = deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        assert result.returncode != 0
        check = subprocess.run(
            ["bash", "-c",
             f"source {LIB_DIR}/ledger.sh; pi_ledger_has_successful_release staging {SHA}"],
            capture_output=True, text=True,
            env={"PATH": "/usr/bin:/bin", "PI_STATE_DIR": str(env["state"])},
        )
        assert check.returncode != 0, "a failed deploy was recorded as successful"

    def test_incompatible_migrations_do_not_block_staging(self, env):
        """Staging is where an incompatible migration is meant to be found."""
        (env["tmp"] / "compat").write_text("false")

        result = deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        assert result.returncode == 0, result.stdout + result.stderr
        assert ledger(env)["current"]["migrations_backward_compatible"] == "false"


class TestLocking:
    def test_a_second_concurrent_deploy_exits_75(self, env):
        (env["tmp"] / "lock_held").write_text("held")

        result = deploy(env, f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")

        assert result.returncode == 75
        assert "pull" not in calls(env)


class TestSourceLevelGuarantees:
    def test_no_eval_and_no_client_string_execution(self):
        code = "\n".join(
            line for line in SCRIPT.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "eval " not in code
        assert '$SSH_ORIGINAL_COMMAND' not in code.replace('"$SSH_ORIGINAL_COMMAND"', "")

    def test_the_repository_is_never_taken_from_input(self):
        code = SCRIPT.read_text()
        assert "ghcr.io" not in code, "image references come from validate.sh constants"
