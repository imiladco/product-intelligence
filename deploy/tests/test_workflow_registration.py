"""The check that would have caught a committed workflow GitHub never ran.

Every other test in this suite reads the workflow YAML and asserts what it
says. None of them could notice that GitHub had no record of the file at all --
which is precisely what happened: `deploy-staging.yml` and
`deploy-production.yml` were valid, reviewed, merged, and inert, because the
repository's default branch was not the branch they were merged to.

`workflow_run`, `workflow_dispatch` and `schedule` are only ever delivered to a
workflow file on the default branch. `push` and `pull_request` are not, because
they run the file from the ref that triggered them. So CI kept passing on every
commit while the two deployment workflows had never run once.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "scripts" / "check_workflow_registration.py"

CI = ".github/workflows/ci.yml"
STAGING = ".github/workflows/deploy-staging.yml"
PRODUCTION = ".github/workflows/deploy-production.yml"


def load_module():
    spec = importlib.util.spec_from_file_location("check_workflow_registration", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def check():
    return load_module()


class TestTheObservedFailure:
    """The exact state this repository was found in, as a standing regression.

    Default branch `claude/product-plan-v1-scope-gt0a5r` held no `.github/`
    directory; all three workflows were on `main`; GitHub had registered only
    CI; and no `workflow_run` event had ever fired.
    """

    def test_it_reports_both_deployment_workflows(self, check):
        problems = check.analyse(
            local={
                CI: {"pull_request", "push"},
                STAGING: {"workflow_run"},
                PRODUCTION: {"workflow_dispatch"},
            },
            registered={CI},
            on_default_branch=set(),
            default_branch="claude/product-plan-v1-scope-gt0a5r",
        )
        joined = "\n".join(problems)
        assert STAGING in joined
        assert PRODUCTION in joined

    def test_it_names_the_default_branch_in_the_message(self, check):
        """An operator reading this must not have to guess what to change."""
        problems = check.analyse(
            local={STAGING: {"workflow_run"}},
            registered=set(),
            on_default_branch=set(),
            default_branch="claude/product-plan-v1-scope-gt0a5r",
        )
        assert any("claude/product-plan-v1-scope-gt0a5r" in p for p in problems)
        assert any("default branch" in p for p in problems)

    def test_ci_alone_would_not_have_flagged_anything(self, check):
        """Why the repository looked healthy.

        A push/pull_request workflow does not need to be on the default
        branch, so CI passing proves nothing about the others.
        """
        problems = check.analyse(
            local={CI: {"pull_request", "push"}},
            registered={CI},
            on_default_branch=set(),
            default_branch="whatever",
        )
        assert problems == []


class TestRegistration:
    def test_a_healthy_repository_reports_nothing(self, check):
        problems = check.analyse(
            local={
                CI: {"pull_request", "push"},
                STAGING: {"workflow_run"},
                PRODUCTION: {"workflow_dispatch"},
            },
            registered={CI, STAGING, PRODUCTION},
            on_default_branch={CI, STAGING, PRODUCTION},
            default_branch="main",
        )
        assert problems == []

    def test_an_unregistered_workflow_is_reported(self, check):
        problems = check.analyse(
            local={CI: {"push"}},
            registered=set(),
            on_default_branch={CI},
            default_branch="main",
        )
        assert len(problems) == 1
        assert "not registered" in problems[0]


class TestDefaultBranchTriggers:
    @pytest.mark.parametrize("trigger", ["workflow_run", "workflow_dispatch", "schedule"])
    def test_each_default_branch_trigger_is_required_to_be_there(self, check, trigger):
        problems = check.analyse(
            local={STAGING: {trigger}},
            registered={STAGING},
            on_default_branch=set(),
            default_branch="main",
        )
        assert len(problems) == 1, problems
        assert trigger in problems[0]

    @pytest.mark.parametrize("trigger", ["push", "pull_request"])
    def test_ref_triggered_workflows_are_not_required_to_be_there(self, check, trigger):
        """These run the file from the triggering ref, so absence is fine."""
        problems = check.analyse(
            local={CI: {trigger}},
            registered={CI},
            on_default_branch=set(),
            default_branch="main",
        )
        assert problems == []

    def test_a_mixed_workflow_still_needs_the_default_branch(self, check):
        """One default-branch trigger is enough to require it."""
        problems = check.analyse(
            local={STAGING: {"push", "workflow_run"}},
            registered={STAGING},
            on_default_branch=set(),
            default_branch="main",
        )
        assert len(problems) == 1
        assert "workflow_run" in problems[0]


class TestTriggerParsing:
    """`on:` is the YAML boolean True, which has bitten this suite before."""

    def test_it_reads_the_real_workflow_files(self, check):
        staging = check.triggers_of(REPO_ROOT / STAGING)
        production = check.triggers_of(REPO_ROOT / PRODUCTION)
        ci = check.triggers_of(REPO_ROOT / CI)

        assert staging == {"workflow_run"}
        assert production == {"workflow_dispatch"}
        assert ci == {"pull_request", "push"}

    def test_every_repository_workflow_is_covered_by_the_check(self, check):
        """No workflow may be invisible to this check by using a shape the
        trigger parser does not understand."""
        for path in sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml")):
            assert check.triggers_of(path), f"{path.name}: no triggers parsed"


class TestTheCheckOnlyReads:
    def test_it_issues_no_mutating_api_call(self):
        """It runs with GITHUB_TOKEN in CI, so it must be incapable of writes."""
        code = SCRIPT.read_text()
        for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "method="):
            assert verb not in code, verb

    def test_it_refuses_to_run_without_credentials_rather_than_passing(self, check):
        """Absent a token the answer is unknown, and unknown is not success."""
        code = SCRIPT.read_text()
        assert "return 2" in code
        assert "only runs inside GitHub Actions" in code
