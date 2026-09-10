"""GitHub Actions: what may build, what may push, and what may deploy.

The workflows are where the two most dangerous mistakes in this system would
live: a production path that rebuilds instead of promoting, and any path by
which CI reaches the privileged control plane.

Three separations are asserted here.

  * Only the staging publish workflow may write packages.
  * Only the production workflow may deploy production, and it takes a SHA and
    nothing else — no digests, no confirmation, no force.
  * No workflow may install, copy or otherwise touch Tier A.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
STAGING = WORKFLOWS / "deploy-staging.yml"
PRODUCTION = WORKFLOWS / "deploy-production.yml"


def load(path: Path) -> dict:
    # `on:` is parsed by PyYAML as the boolean True; normalise it back.
    data = yaml.safe_load(path.read_text())
    if True in data:
        data["on"] = data.pop(True)
    return data


def all_run_steps(workflow: dict) -> str:
    """Everything a step actually carries: its command, its action, its inputs
    and its environment.

    `env` matters as much as the rest — a digest passed to a step arrives that
    way, and a helper that skipped it would report the workflow as sending no
    digest at all.
    """
    lines = []
    for job in workflow.get("jobs", {}).values():
        for step in job.get("steps", []) or []:
            if "run" in step:
                lines.append(str(step["run"]))
            if "uses" in step:
                lines.append(str(step["uses"]))
            for value in (step.get("with") or {}).values():
                lines.append(str(value))
            for key, value in (step.get("env") or {}).items():
                lines.append(f"{key}: {value}")
    return "\n".join(lines)


class TestCiGates:
    @pytest.fixture(scope="class")
    def ci(self) -> dict:
        return load(CI)

    def test_it_runs_on_pull_requests_and_main(self, ci):
        assert "pull_request" in ci["on"]
        assert "push" in ci["on"]

    def test_it_runs_every_backend_gate(self, ci):
        text = all_run_steps(ci)
        assert "pytest" in text
        assert "makemigrations --check --dry-run" in text

    def test_it_runs_the_deploy_script_tests(self, ci):
        assert "deploy/tests" in all_run_steps(ci)

    def test_it_runs_every_frontend_gate(self, ci):
        text = all_run_steps(ci)
        assert "npm run test" in text
        assert "tsc --noEmit" in text
        assert "npm run lint" in text
        assert "npm run build" in text

    def test_it_builds_both_images_without_pushing(self, ci):
        pushes = [
            step
            for job in ci["jobs"].values()
            for step in (job.get("steps") or [])
            if "build-push-action" in str(step.get("uses", ""))
        ]
        assert len(pushes) == 2, "expected an api and a web build"
        for step in pushes:
            assert step["with"]["push"] in (False, "false")

    def test_it_validates_the_caddyfiles(self, ci):
        """`caddy validate` needs a daemon, which CI has and the dev box does not."""
        assert "caddy validate" in all_run_steps(ci)

    def test_it_has_no_registry_login_and_no_ssh(self, ci):
        text = all_run_steps(ci)
        assert "docker/login-action" not in text
        assert "ssh " not in text

    def test_its_permissions_are_read_only(self, ci):
        assert ci["permissions"] == {"contents": "read"}


class TestStagingPublishAndDeploy:
    @pytest.fixture(scope="class")
    def staging(self) -> dict:
        return load(STAGING)

    def test_it_never_runs_on_a_pull_request(self, staging):
        assert "pull_request" not in staging["on"]

    def test_it_pushes_by_full_sha_tag(self, staging):
        """Either github.sha or workflow_run.head_sha.

        On a workflow_run trigger the second is the correct one: github.sha is
        main's head when the event fired, which can already have moved past the
        commit CI actually validated.
        """
        text = all_run_steps(staging)
        assert "github.sha" in text or "workflow_run.head_sha" in text

    def test_it_passes_both_build_args(self, staging):
        text = all_run_steps(staging)
        assert "GIT_SHA=" in text
        assert "MIGRATIONS_BACKWARD_COMPATIBLE=" in text

    def test_the_compatibility_arg_comes_from_the_metadata_file(self, staging):
        assert "release-metadata.json" in all_run_steps(staging)

    def test_it_sends_both_digests_to_the_deploy_command(self, staging):
        text = all_run_steps(staging)
        assert text.count("outputs.digest") >= 2, "both digests must be sent"
        assert "deploy ${SHA}" in text or "deploy $SHA" in text

    def test_it_never_deploys_by_tag(self, staging):
        """The deploy command carries digests, not a tag reference.

        Checked per run-step, not per line: the ssh invocation is wrapped
        across several lines, so a line-based grep would miss it entirely.
        """
        ssh_steps = [
            str(step["run"])
            for job in staging["jobs"].values()
            for step in (job.get("steps") or [])
            if "run" in step and "ssh " in str(step["run"])
        ]
        assert ssh_steps, "no ssh deploy step found"
        for block in ssh_steps:
            assert "deploy ${SHA}" in block, block
            assert "${API_DIGEST}" in block and "${WEB_DIGEST}" in block, block
            # A tag reference would be repo:sha rather than repo@sha256:.
            assert "/api:" not in block and "/web:" not in block, block

    def test_it_uses_only_the_staging_key(self, staging):
        text = str(staging)
        assert "STAGING_DEPLOY_KEY" in text
        assert "PRODUCTION_DEPLOY_KEY" not in text

    def test_it_verifies_the_host_key(self, staging):
        text = all_run_steps(staging)
        assert "StrictHostKeyChecking=yes" in text
        assert "DEPLOY_KNOWN_HOSTS" in str(staging)

    def test_it_is_the_only_workflow_with_packages_write(self, staging):
        assert staging["permissions"]["packages"] == "write"
        for other in (CI, PRODUCTION):
            if other.exists():
                assert "packages" not in (load(other).get("permissions") or {}), other.name


class TestProductionPromotion:
    @pytest.fixture(scope="class")
    def production(self) -> dict:
        return load(PRODUCTION)

    def test_it_is_dispatch_only(self, production):
        """No push, no schedule, no workflow_run. Production cannot be reached
        by merging anything."""
        assert set(production["on"]) == {"workflow_dispatch"}

    def test_it_takes_only_a_sha_input(self, production):
        inputs = production["on"]["workflow_dispatch"]["inputs"]
        assert set(inputs) == {"sha"}
        assert inputs["sha"]["required"] is True

    def test_it_has_no_confirmation_or_force_input(self, production):
        text = str(production["on"]).lower()
        for forbidden in ("confirm", "force", "override", "skip"):
            assert forbidden not in text, forbidden

    def test_it_validates_the_sha_shape_before_contacting_the_server(self, production):
        assert "[0-9a-f]{40}" in all_run_steps(production)

    def test_it_never_builds_or_pushes(self, production):
        text = all_run_steps(production)
        assert "build-push-action" not in text
        assert "docker build" not in text
        assert "packages" not in str(production.get("permissions", {}))

    def test_it_sends_no_digest(self, production):
        assert "digest" not in all_run_steps(production)

    def test_it_uses_only_the_production_key(self, production):
        text = str(production)
        assert "PRODUCTION_DEPLOY_KEY" in text
        assert "STAGING_DEPLOY_KEY" not in text

    def test_it_verifies_the_host_key(self, production):
        assert "StrictHostKeyChecking=yes" in all_run_steps(production)


class TestNoWorkflowTouchesTheControlPlane:
    """The separation that makes every other guarantee hold."""

    def test_no_workflow_installs_the_control_plane(self):
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

    def test_no_workflow_writes_a_compose_manifest_to_the_server(self):
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            text = workflow.read_text()
            assert "/opt/product-intelligence" not in text, workflow.name

    def test_deploy_workflows_send_only_the_deploy_grammar(self):
        """The SSH command is `deploy …`, never a shell command.

        The forced command on the server would reject anything else, but a
        workflow that tried is a design error worth catching here.
        """
        for path in (STAGING, PRODUCTION):
            if not path.exists():
                continue
            workflow = load(path)
            ssh_steps = [
                str(step["run"])
                for job in workflow["jobs"].values()
                for step in (job.get("steps") or [])
                if "run" in step and "ssh " in str(step["run"])
            ]
            for block in ssh_steps:
                assert '"deploy ' in block, block
                for forbidden in ("&&", ";", "|", "$(", "`"):
                    remote = block.split('"deploy ', 1)[1].split('"', 1)[0]
                    assert forbidden not in remote, f"{path.name}: {remote}"
