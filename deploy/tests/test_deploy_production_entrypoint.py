"""The production deploy entry point.

Production is the environment that refuses things. Three refusals matter, and
each is asserted to happen *before* anything is pulled or migrated:

  * A SHA with no successful staging release. The staging ledger is the only
    evidence, and an unproven SHA never reaches an image pull.
  * A client-supplied digest. The grammar has no room for one; production reads
    digests from the ledger entry that passed.
  * A release that does not declare exactly `true` for backward-compatible
    migrations. Refused before the migration runs — because the previous
    application is still serving traffic while migrations execute, so an
    incompatible one breaks production *during* the deploy, not merely
    afterwards.

There is no override, no force flag and no confirmation token anywhere.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "scripts" / "pi-deploy-production"
LIB_DIR = REPO_ROOT / "deploy" / "scripts" / "lib"

SHA = "a" * 40
API_DIGEST = "sha256:" + "1" * 64
WEB_DIGEST = "sha256:" + "2" * 64
OTHER_API = "sha256:" + "9" * 64
OTHER_WEB = "sha256:" + "8" * 64
GHCR_TOKEN = "ghp_EntrypointHarnessSecret0123456789"
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
    (state / "history").mkdir(parents=True)
    deploy_dir = tmp_path / "production"
    (deploy_dir / ".release").mkdir(parents=True)
    calls = tmp_path / "calls.log"

    env_file = deploy_dir / ".env"
    env_file.write_text("\n".join(f"{k}=value-for-{k}" for k in REQUIRED_ENV_KEYS) + "\n")
    env_file.chmod(0o600)
    credential = tmp_path / "ghcr.env"
    credential.write_text("GHCR_USERNAME=pi-deploy-bot\nGHCR_TOKEN=" + GHCR_TOKEN + "\n")
    credential.chmod(0o600)

    (deploy_dir / "compose.production.yaml").write_text("name: product-intelligence-production\n")

    (bin_dir / "docker").write_text(f"""#!/usr/bin/env bash
echo "docker $@" >> {calls}
case "$1 $2" in
  "login "*|"login")
      # Consumes stdin exactly as the real client does with --password-stdin,
      # so a token sent that way never reaches the call log.
      cat >/dev/null
      exit "$(cat {tmp_path}/login_status 2>/dev/null || echo 0)" ;;
  "image inspect")
      if [[ "$*" == *"revision"* ]]; then
        cat {tmp_path}/revision 2>/dev/null || echo "{SHA}"
      else
        cat {tmp_path}/compat 2>/dev/null || echo "true"
      fi
      exit 0 ;;
  "compose "*)
      if [[ "$*" == *"config"* ]]; then
        printf '{{"name": "product-intelligence-production"}}'
      fi
      exit 0 ;;
  *) exit 0 ;;
esac
""")
    (bin_dir / "docker").chmod(0o755)

    (bin_dir / "curl").write_text(f"""#!/usr/bin/env bash
echo "curl $@" >> {calls}
printf '{{"status":"ok","database":"ok","release":"%s"}}' \
  "$(cat {tmp_path}/served_release 2>/dev/null || echo '{SHA}')"
exit 0
""")
    (bin_dir / "curl").chmod(0o755)

    (bin_dir / "flock").write_text(f"""#!/usr/bin/env bash
code=1
while [[ "$1" == -* ]]; do
  if [[ "$1" == "-E" ]]; then code="$2"; shift 2; else shift; fi
done
shift
if [[ -f {tmp_path}/lock_held ]]; then exit "$code"; fi
exec "$@"
""")
    (bin_dir / "flock").chmod(0o755)

    return {"bin": bin_dir, "tmp": tmp_path, "state": state,
            "deploy": deploy_dir, "calls": calls}


def staging_passed(env, sha=SHA, api=API_DIGEST, web=WEB_DIGEST, compat="true"):
    """Record a successful staging release, as pi-deploy-staging would."""
    (env["state"] / "staging.json").write_text(json.dumps({
        "environment": "staging",
        "current": {"sha": sha, "api_digest": api, "web_digest": web,
                    "migrations_backward_compatible": compat},
        "previous": None,
        "candidate": None,
    }))


def promote(env, command: str) -> subprocess.CompletedProcess:
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
            "PI_GHCR_CREDENTIAL_FILE": str(env["tmp"] / "ghcr.env"),
        },
    )


def calls(env) -> str:
    return env["calls"].read_text() if env["calls"].exists() else ""


class TestStagingProofIsRequired:
    def test_refuses_a_sha_with_no_staging_release(self, env):
        result = promote(env, f"deploy {SHA}")

        assert result.returncode != 0
        assert "pull" not in calls(env), "an unproven SHA reached an image pull"
        assert "migrate" not in calls(env)

    def test_refuses_a_sha_whose_staging_release_failed(self, env):
        (env["state"] / "staging.json").write_text(json.dumps({
            "environment": "staging", "current": None, "previous": None, "candidate": None,
        }))
        (env["state"] / "history" / "staging.jsonl").write_text(
            json.dumps({"sha": SHA, "result": "failure"}) + "\n"
        )

        result = promote(env, f"deploy {SHA}")

        assert result.returncode != 0
        assert "pull" not in calls(env)

    def test_refuses_a_different_sha_than_the_one_that_passed(self, env):
        staging_passed(env, sha="c" * 40)
        result = promote(env, f"deploy {SHA}")
        assert result.returncode != 0
        assert "pull" not in calls(env)


class TestDigestsComeOnlyFromTheLedger:
    def test_uses_the_digests_recorded_by_staging(self, env):
        staging_passed(env, api=API_DIGEST, web=WEB_DIGEST)

        promote(env, f"deploy {SHA}")

        pulls = calls(env)
        assert f"{API_REPO}@{API_DIGEST}" in pulls
        assert f"{WEB_REPO}@{WEB_DIGEST}" in pulls

    def test_ignores_client_supplied_digests(self, env):
        """The grammar has no room for them, so this never reaches a pull."""
        staging_passed(env)

        result = promote(env, f"deploy {SHA} {OTHER_API} {OTHER_WEB}")

        assert result.returncode == 2
        assert OTHER_API not in calls(env)
        assert "pull" not in calls(env)

    def test_never_pulls_by_tag(self, env):
        staging_passed(env)
        promote(env, f"deploy {SHA}")

        for line in calls(env).splitlines():
            if " pull " in line:
                assert "@sha256:" in line, line
                assert f":{SHA}" not in line


class TestMigrationCompatibilityFailsClosed:
    @pytest.mark.parametrize(
        "label", ["false", "", "True", "TRUE", "1", "yes", "<no value>", "unknown"],
    )
    def test_refuses_anything_that_is_not_exactly_true(self, env, label):
        staging_passed(env)
        (env["tmp"] / "compat").write_text(label)

        result = promote(env, f"deploy {SHA}")

        assert result.returncode != 0, f"accepted {label!r}"
        assert "migrate" not in calls(env), "refused too late — the migration ran"
        assert not (env["state"] / "production.json").exists(), "a candidate was written"

    def test_accepts_exactly_true(self, env):
        staging_passed(env)
        (env["tmp"] / "compat").write_text("true")

        result = promote(env, f"deploy {SHA}")

        assert result.returncode == 0, result.stdout + result.stderr
        assert "manage.py migrate" in calls(env)

    def test_the_refusal_names_the_reason(self, env):
        staging_passed(env)
        (env["tmp"] / "compat").write_text("false")
        result = promote(env, f"deploy {SHA}")
        assert "backward-compatible" in (result.stdout + result.stderr)


class TestProvenance:
    def test_revision_mismatch_is_refused(self, env):
        staging_passed(env)
        (env["tmp"] / "revision").write_text("b" * 40)

        result = promote(env, f"deploy {SHA}")

        assert result.returncode != 0
        assert "migrate" not in calls(env)

    def test_both_images_are_revision_checked(self, env):
        staging_passed(env)
        promote(env, f"deploy {SHA}")

        inspects = [ln for ln in calls(env).splitlines()
                    if "image inspect" in ln and "revision" in ln]
        assert any(API_REPO in ln for ln in inspects)
        assert any(WEB_REPO in ln for ln in inspects)


class TestOrderingAndLedger:
    def test_postgres_is_started_before_the_migration(self, env):
        staging_passed(env)
        promote(env, f"deploy {SHA}")

        lines = calls(env).splitlines()
        postgres_at = next(i for i, ln in enumerate(lines) if "up -d postgres" in ln)
        migrate_at = next(i for i, ln in enumerate(lines) if "manage.py migrate" in ln)
        assert postgres_at < migrate_at

    def test_a_successful_promotion_records_the_same_digests(self, env):
        staging_passed(env)
        promote(env, f"deploy {SHA}")

        data = json.loads((env["state"] / "production.json").read_text())
        assert data["current"]["sha"] == SHA
        assert data["current"]["api_digest"] == API_DIGEST
        assert data["current"]["web_digest"] == WEB_DIGEST

    def test_a_failing_health_gate_does_not_mark_production_current(self, env):
        staging_passed(env)
        (env["tmp"] / "served_release").write_text("b" * 40)

        result = promote(env, f"deploy {SHA}")

        assert result.returncode != 0
        data = json.loads((env["state"] / "production.json").read_text())
        assert data["current"] is None


class TestLocking:
    def test_a_second_concurrent_promotion_exits_75(self, env):
        staging_passed(env)
        (env["tmp"] / "lock_held").write_text("held")

        assert promote(env, f"deploy {SHA}").returncode == 75


class TestNoOverrideExists:
    def test_no_force_or_confirmation_token_in_the_script(self):
        """Scoped to executable lines.

        The script's header documents that no confirmation token exists, and a
        whole-text scan would flag that sentence as the very thing it promises
        not to contain.
        """
        code = "\n".join(
            line for line in SCRIPT.read_text().splitlines()
            if not line.strip().startswith("#")
        ).lower()
        for forbidden in ("--force", "force_deploy", "--yes", "confirm", "override", "skip-compat"):
            assert forbidden not in code, f"an override path exists: {forbidden}"

    def test_no_eval(self):
        code = "\n".join(
            line for line in SCRIPT.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "eval " not in code

    def test_the_repository_is_never_taken_from_input(self):
        assert "ghcr.io" not in SCRIPT.read_text()


class TestRegistryAuthenticationPrecedesPulls:
    """Both images are private, so a pull without a login is a pull that only
    works by accident -- off whatever /root/.docker/config.json remembers from
    an admin's manual login. That works until the remembered token expires,
    then fails as "could not pull" with nothing naming the real cause.
    """

    def test_it_authenticates_before_pulling(self, env):
        staging_passed(env)
        promote(env, f"deploy {SHA}")
        log = calls(env)
        assert "docker login" in log, "no registry authentication happened"
        assert log.index("docker login") < log.index("docker pull"), (
            "pulled before authenticating"
        )

    def test_a_missing_credential_stops_before_any_pull(self, env):
        (env["tmp"] / "ghcr.env").unlink()
        staging_passed(env)
        result = promote(env, f"deploy {SHA}")
        assert result.returncode != 0
        assert "pull" not in calls(env), "pulled without a credential"

    def test_a_loose_credential_mode_stops_before_any_pull(self, env):
        (env["tmp"] / "ghcr.env").chmod(0o644)
        staging_passed(env)
        result = promote(env, f"deploy {SHA}")
        assert result.returncode != 0
        assert "pull" not in calls(env)

    def test_a_rejected_credential_stops_before_any_pull(self, env):
        (env["tmp"] / "login_status").write_text("1")
        staging_passed(env)
        result = promote(env, f"deploy {SHA}")
        assert result.returncode != 0
        assert "pull" not in calls(env)

    def test_the_token_never_reaches_the_call_log(self, env):
        staging_passed(env)
        promote(env, f"deploy {SHA}")
        assert GHCR_TOKEN not in calls(env), "the token was passed as an argument"

    def test_the_token_never_reaches_the_ledger(self, env):
        staging_passed(env)
        promote(env, f"deploy {SHA}")
        assert GHCR_TOKEN not in (env["state"] / "production.json").read_text()

    def test_it_still_pulls_only_the_validated_digests(self, env):
        """Authentication must not disturb digest-only deployment identity."""
        staging_passed(env)
        promote(env, f"deploy {SHA}")
        log = calls(env)
        assert f"docker pull {API_REPO}@{API_DIGEST}" in log
        assert f"docker pull {WEB_REPO}@{WEB_DIGEST}" in log
        assert f"{API_REPO}:" not in log, "pulled by tag"
        assert f"{WEB_REPO}:" not in log, "pulled by tag"
