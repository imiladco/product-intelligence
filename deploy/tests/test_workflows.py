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


PROVENANCE_SCRIPT = "deploy/scripts/verify-image-provenance.sh"


def provenance_blocks(workflow: dict) -> list[str]:
    """Every run-step that invokes the provenance verifier.

    Whole blocks, not lines: the staging step pulls, verifies and compares
    digests across several lines, and each assertion here needs the block.
    """
    return [
        str(step["run"])
        for job in workflow.get("jobs", {}).values()
        for step in (job.get("steps") or [])
        if "run" in step and PROVENANCE_SCRIPT in str(step["run"])
    ]


def provenance_refs(block: str) -> list[str]:
    """The image reference each invocation of the verifier is pointed at.

    Shell variables assigned earlier in the same block are substituted, so a
    step that builds `API_REF` and then verifies `"$API_REF"` is reported as
    verifying the reference it actually resolves to. Without that the digest
    assertions below would pass on the literal string `$API_REF`, which proves
    nothing.
    """
    assignments: dict[str, str] = {}
    refs = []
    for raw in block.splitlines():
        line = raw.strip()
        if line.startswith(PROVENANCE_SCRIPT):
            parts = line.split()
            assert len(parts) >= 3, f"verifier called without an expected sha: {line}"
            refs.append(expand(parts[1].strip('"'), assignments))
            continue
        name, sep, value = line.partition("=")
        if sep and name.isidentifier():
            assignments[name] = expand(value.strip().strip('"'), assignments)
    return refs


def expand(value: str, assignments: dict[str, str]) -> str:
    for name, resolved in assignments.items():
        value = value.replace("${" + name + "}", resolved).replace("$" + name, resolved)
    return value


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
        text = all_run_steps(ci)
        assert "caddy validate" in text
        assert "docker/caddy/Caddyfile" in text
        assert "deploy/caddy/Caddyfile.staging-only" in text

    def test_the_caddy_validation_supplies_an_acme_email(self, ci):
        """The Caddyfiles read `email {$ACME_EMAIL}`, which Caddy expands at
        parse time. Unset, it becomes a bare `email` with no argument and the
        file does not parse -- so validation fails on a perfectly good config.

        The placeholder belongs in CI rather than as a default in the
        Caddyfile: a Caddy with no ACME contact should refuse to start on the
        server, and a default would quietly take that guard away.
        """
        steps = [
            step
            for job in ci["jobs"].values()
            for step in (job.get("steps") or [])
            if "caddy validate" in str(step.get("run", ""))
        ]
        assert steps, "no caddy validation step"
        for step in steps:
            assert "ACME_EMAIL" in (step.get("env") or {}), step.get("name")

    def test_no_caddyfile_defaults_the_acme_email(self):
        """A `{$ACME_EMAIL:something}` default would make a misconfigured
        server start with the wrong ACME contact instead of failing."""
        for caddyfile in (
            REPO_ROOT / "docker" / "caddy" / "Caddyfile",
            REPO_ROOT / "deploy" / "caddy" / "Caddyfile.staging-only",
        ):
            assert "{$ACME_EMAIL}" in caddyfile.read_text(), caddyfile
            assert "{$ACME_EMAIL:" not in caddyfile.read_text(), caddyfile

    def test_it_checks_that_github_registered_every_workflow(self, ci):
        """The gap that let two inert deployment workflows look healthy.

        Reading the YAML proves what a file says, never that GitHub has a
        record of it or will ever deliver an event to it.
        """
        assert "check_workflow_registration.py" in all_run_steps(ci)

    def test_the_registration_check_gets_a_token(self, ci):
        """It queries the API; without credentials it exits 2 rather than
        reporting success it cannot establish."""
        steps = [
            step
            for job in ci["jobs"].values()
            for step in (job.get("steps") or [])
            if "check_workflow_registration.py" in str(step.get("run", ""))
        ]
        assert steps, "no registration check step"
        for step in steps:
            assert "GITHUB_TOKEN" in (step.get("env") or {}), step.get("name")

    def test_it_validates_workflows_against_githubs_own_schema(self, ci):
        """PyYAML parsing is not GitHub's schema. A workflow can parse cleanly
        here and still be rejected or ignored there."""
        assert "actionlint" in all_run_steps(ci)

    def test_it_has_no_registry_login_and_no_ssh(self, ci):
        text = all_run_steps(ci)
        assert "docker/login-action" not in text
        assert "ssh " not in text

    def test_its_permissions_are_read_only(self, ci):
        assert ci["permissions"] == {"contents": "read"}

    def test_it_verifies_provenance_for_both_images(self, ci):
        """The first link of the chain, checked on a real build.

        Every other provenance test in this suite reads a Dockerfile or stubs
        `docker image inspect`. Only this step can show that the label a
        Dockerfile declares is the label a built image carries — so it must
        cover both images, not just the API one.
        """
        blocks = provenance_blocks(ci)
        assert blocks, "no provenance verification step in ci.yml"
        refs = [ref for block in blocks for ref in provenance_refs(block)]
        assert len(refs) == 2, f"expected an api and a web verification, got {refs}"
        assert "pi-api:ci" in refs, refs
        assert "pi-web:ci" in refs, refs

    def test_it_checks_the_api_image_compatibility_label(self, ci):
        """The web image has no such label; the API image's is a deploy gate."""
        text = "\n".join(provenance_blocks(ci))
        assert "release-metadata.json" in text, text


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

    def test_deployment_is_gated_on_an_explicit_readiness_variable(self, staging):
        """Deploying must be armed deliberately, not by merging something.

        The gate is a repository variable rather than a file in this tree: a
        variable is set in repository settings by a human with admin rights,
        outside the merge path. That is the same principle the server-side
        control plane follows -- what authorises deployment is not modifiable
        by the automatic path it authorises.
        """
        deploy_jobs = {
            name: job
            for name, job in staging["jobs"].items()
            for step in (job.get("steps") or [])
            if "ssh " in str(step.get("run", ""))
        }
        assert deploy_jobs, "no deploy job found"
        for name, job in deploy_jobs.items():
            condition = str(job.get("if", ""))
            assert "STAGING_DEPLOY_READY" in condition, f"{name} is not gated: {condition!r}"
            assert "vars." in condition, f"{name}'s gate is not a repository variable"

    def test_the_readiness_gate_is_fail_closed(self, staging):
        """Only the exact string `true` may arm it.

        An unset variable is the empty string, so a truthiness test or a `!=
        'false'` would arm staging by default -- which is the failure mode
        this gate exists to remove.
        """
        for job in staging["jobs"].values():
            condition = str(job.get("if", ""))
            if "STAGING_DEPLOY_READY" not in condition:
                continue
            assert "== 'true'" in condition or '== "true"' in condition, condition
            assert "!=" not in condition, f"a negated gate arms by default: {condition}"

    def test_the_deploy_job_requires_publish_to_have_succeeded(self, staging):
        """The success requirement is written out, not left to the default.

        GitHub already skips a job whose `needs` dependency failed, so this is
        redundant today. It is stated because the default is silently lost the
        moment a job-level `if` uses a status function -- see the test below,
        which is the other half of this guard.
        """
        for name, job in staging["jobs"].items():
            if not any("ssh " in str(step.get("run", "")) for step in (job.get("steps") or [])):
                continue
            condition = str(job.get("if", ""))
            assert "needs.publish.result == 'success'" in condition, (
                f"{name} does not explicitly require a successful publish: {condition!r}"
            )

    def test_no_job_level_status_function_can_defeat_the_needs_gate(self, staging):
        """A job-level always(), failure() or cancelled() overrides `needs`.

        This is the specific edit that would turn the whole gate into
        decoration: with always() on the deploy job, a *failed* publish -- one
        whose provenance verification did not pass, or which never pushed the
        images at all -- would still be followed by an SSH deploy.

        Checked on every job in this workflow, not only the deploy job: which
        job holds the ssh step is an implementation detail that has already
        changed once.
        """
        for name, job in staging["jobs"].items():
            condition = str(job.get("if", ""))
            for function in ("always()", "failure()", "cancelled()"):
                assert function not in condition, (
                    f"job-level {function} on {name!r} overrides its needs gate: {condition!r}"
                )

    def test_step_level_always_stays_allowed(self, staging):
        """The guard above must not over-reach.

        Cleanup that has to run even when the step before it failed is exactly
        what step-level always() is for -- removing the deploy key is the case
        here -- and step conditions have no bearing on job gating. A guard that
        banned always() everywhere would force that key to be left behind on a
        failed deploy, trading a real secret-hygiene property for nothing.
        """
        cleanup = [
            step
            for job in staging["jobs"].values()
            for step in (job.get("steps") or [])
            if str(step.get("if", "")) == "always()"
        ]
        assert cleanup, "expected at least one step-level always() to remain permitted"
        assert any("rm -f ~/.ssh/id_ed25519" in str(step.get("run", "")) for step in cleanup), (
            "the deploy key removal must still run unconditionally"
        )

    def test_publishing_is_not_gated_on_readiness(self, staging):
        """Building, pushing and verifying provenance are quality gates.

        They must keep running on every green main long before a server
        exists -- otherwise a broken Dockerfile or a broken provenance chain
        stays hidden until infrastructure bootstrap, which is the worst moment
        to discover it.
        """
        publishing = [
            (name, job)
            for name, job in staging["jobs"].items()
            for step in (job.get("steps") or [])
            if "build-push-action" in str(step.get("uses", ""))
        ]
        assert publishing, "nothing publishes images"
        for name, job in publishing:
            assert "STAGING_DEPLOY_READY" not in str(job.get("if", "")), (
                f"{name} must not be gated on deployment readiness"
            )

    def test_the_deploy_job_checks_its_secrets_are_present(self, staging):
        """Arming the gate does not conjure the secrets.

        Without this the first armed run fails inside `ssh` against an empty
        host, which says nothing about the real cause.
        """
        text = all_run_steps(staging)
        assert "STAGING_DEPLOY_READY is true but these secrets are not set" in text

    def test_the_run_says_why_a_deploy_did_not_happen(self, staging):
        """A skipped job with no explanation is the silent failure itself."""
        text = all_run_steps(staging)
        assert "GITHUB_STEP_SUMMARY" in text
        assert "STAGING_DEPLOY_READY" in text

    def test_it_uses_only_the_staging_key(self, staging):
        text = str(staging)
        assert "STAGING_DEPLOY_KEY" in text
        assert "PRODUCTION_DEPLOY_KEY" not in text

    def test_it_verifies_the_host_key(self, staging):
        text = all_run_steps(staging)
        assert "StrictHostKeyChecking=yes" in text
        assert "DEPLOY_KNOWN_HOSTS" in str(staging)

    def test_it_verifies_provenance_by_digest_for_both_images(self, staging):
        """Labels are read off the digest reference, never off a tag.

        A tag can be moved between the push and the check; a digest cannot.
        This is the only place in the repository where the digest half of the
        chain can be established, so it is asserted precisely.
        """
        blocks = provenance_blocks(staging)
        assert blocks, "no provenance verification step in deploy-staging.yml"
        refs = [ref for block in blocks for ref in provenance_refs(block)]
        assert len(refs) == 2, f"expected one api and one web verification, got {refs}"
        for ref in refs:
            assert "@" in ref, f"provenance checked against a non-digest reference: {ref}"
            assert ":" not in ref.split("@", 1)[0], f"tag reference: {ref}"
        assert any(ref.startswith("ghcr.io/") and "/api@" in ref for ref in refs), refs
        assert any(ref.startswith("ghcr.io/") and "/web@" in ref for ref in refs), refs

    def test_it_pulls_each_digest_before_verifying(self, staging):
        """Pulling proves the digest is real and fetchable, not merely reported."""
        for block in provenance_blocks(staging):
            assert "docker pull" in block, block

    def test_it_compares_the_reported_digest_against_the_stored_one(self, staging):
        text = "\n".join(provenance_blocks(staging))
        assert "RepoDigests" in text, "the reported digest is never checked against the registry"

    def test_the_provenance_check_precedes_the_deploy_step(self, staging):
        """An unverified image must never reach the server.

        Publishing and deploying are separate jobs, so ordering is expressed
        by `needs` rather than by step order. Asserted across jobs on purpose:
        the same-job version of this test would pass vacuously now, proving
        nothing at all.
        """
        verify_jobs = {
            name
            for name, job in staging["jobs"].items()
            for step in (job.get("steps") or [])
            if PROVENANCE_SCRIPT in str(step.get("run", ""))
        }
        deploy_jobs = {
            name
            for name, job in staging["jobs"].items()
            for step in (job.get("steps") or [])
            if "ssh " in str(step.get("run", ""))
        }
        assert verify_jobs, "nothing verifies provenance"
        assert deploy_jobs, "nothing deploys"

        for name in deploy_jobs:
            job = staging["jobs"][name]
            if name in verify_jobs:
                steps = job["steps"]
                verify = next(
                    i
                    for i, step in enumerate(steps)
                    if PROVENANCE_SCRIPT in str(step.get("run", ""))
                )
                deploy = next(
                    i for i, step in enumerate(steps) if "ssh " in str(step.get("run", ""))
                )
                assert verify < deploy, f"{name}: provenance is verified after deploying"
                continue
            needs = job.get("needs") or []
            needs = [needs] if isinstance(needs, str) else needs
            assert verify_jobs & set(needs), (
                f"{name} deploys without depending on a job that verifies provenance"
            )

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

    def test_the_provenance_script_is_not_part_of_the_control_plane(self):
        """It runs in CI, on the runner, against images CI itself built.

        That is only safe because it is a read-only helper living in the
        repository like any other tested script — not something installed on,
        or copied to, a deploy host.
        """
        script = REPO_ROOT / PROVENANCE_SCRIPT
        assert script.exists(), PROVENANCE_SCRIPT
        control_plane = (REPO_ROOT / "deploy" / "scripts" / "pi-install-control-plane").read_text()
        assert "verify-image-provenance" not in control_plane

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
