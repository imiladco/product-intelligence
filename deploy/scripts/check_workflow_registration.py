#!/usr/bin/env python3
"""Assert that GitHub has actually registered this repository's workflows.

A workflow file can be committed, valid, reviewed and merged, and still never
run. Everything that decides whether it runs lives outside the file:

  * `workflow_run`, `workflow_dispatch` and `schedule` only ever fire for a
    workflow file that is present on the repository's **default branch**.
    GitHub documents this for each of them. A file on a non-default branch is
    inert for those events -- no error, no annotation, no run: the workflow
    simply does not exist as far as those triggers are concerned.
  * `push` and `pull_request` do not have that requirement, because they run
    the workflow file from the ref that triggered them. So a repository whose
    default branch is not the branch being developed on will happily run CI
    while silently ignoring every deployment workflow.

That is exactly the failure this exists to catch, and it is invisible to any
test that reads the YAML: the files were valid, the tests were green, and no
deploy workflow had ever run or could ever run.

The logic is separated from the fetching on purpose. `analyse` is pure, so the
interesting cases are unit-tested offline; `main` is the thin part that talks
to the API and is read-only (two GETs, no mutation).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

# Triggers GitHub only delivers to a workflow living on the default branch.
DEFAULT_BRANCH_TRIGGERS = frozenset({"workflow_run", "workflow_dispatch", "schedule"})


def triggers_of(path: Path) -> set[str]:
    """The event names a workflow file subscribes to.

    `on:` is parsed by PyYAML as the boolean True, which is why this is worth
    a helper rather than a dict lookup.
    """
    data = yaml.safe_load(path.read_text()) or {}
    on = data.get("on", data.get(True))
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    if isinstance(on, dict):
        return set(on)
    return set()


def analyse(
    *,
    local: dict[str, set[str]],
    registered: set[str],
    on_default_branch: set[str],
    default_branch: str,
) -> list[str]:
    """Return one message per problem; an empty list means all is well.

    `local`             workflow path -> its trigger names, as committed here
    `registered`        workflow paths GitHub has a record of
    `on_default_branch` workflow paths that exist on the default branch
    """
    problems: list[str] = []

    for path in sorted(local):
        if path not in registered:
            problems.append(
                f"{path} is not registered with GitHub Actions. "
                "A committed workflow file that GitHub has no record of will "
                "never run, and its runs URL reports that it does not exist."
            )

    for path in sorted(local):
        needs_default = sorted(DEFAULT_BRANCH_TRIGGERS & local[path])
        if not needs_default:
            continue
        if path not in on_default_branch:
            problems.append(
                f"{path} uses {', '.join(needs_default)}, which GitHub only "
                f"delivers to a workflow on the default branch, but the file "
                f"is absent from the default branch ({default_branch!r}). "
                "It will never be triggered. Either the default branch is "
                "wrong for this repository, or the file has not reached it."
            )

    return problems


def _get(url: str, token: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "product-intelligence-workflow-check",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")

    if not repository or not token:
        print(
            "workflow registration: GITHUB_REPOSITORY and GITHUB_TOKEN are "
            "required; this check only runs inside GitHub Actions.",
            file=sys.stderr,
        )
        return 2

    workflow_dir = Path(".github/workflows")
    local = {
        f".github/workflows/{p.name}": triggers_of(p)
        for p in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
    }
    if not local:
        print("workflow registration: no workflow files found", file=sys.stderr)
        return 2

    try:
        repo = _get(f"{api}/repos/{repository}", token)
        listing = _get(f"{api}/repos/{repository}/actions/workflows?per_page=100", token)
    except (urllib.error.URLError, urllib.error.HTTPError) as error:
        print(f"workflow registration: could not query GitHub: {error}", file=sys.stderr)
        return 2

    default_branch = repo["default_branch"]
    registered = {w["path"] for w in listing.get("workflows", [])}

    on_default_branch: set[str] = set()
    for path in local:
        try:
            _get(f"{api}/repos/{repository}/contents/{path}?ref={default_branch}", token)
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            continue
        on_default_branch.add(path)

    problems = analyse(
        local=local,
        registered=registered,
        on_default_branch=on_default_branch,
        default_branch=default_branch,
    )

    print(f"default branch: {default_branch}")
    print(f"workflow files here: {', '.join(sorted(local))}")
    print(f"registered with GitHub: {', '.join(sorted(registered)) or '(none)'}")
    print(f"present on the default branch: {', '.join(sorted(on_default_branch)) or '(none)'}")

    if problems:
        print()
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1

    print("every workflow is registered and reachable by its triggers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
