"""verify-image-provenance.sh — the Git SHA → OCI label link, checked on a real image.

Every other provenance assertion in this repository reads a Dockerfile or stubs
`docker image inspect`. None of them can show that the label a Dockerfile
*declares* is the label a built image actually *carries*. Only a real build
can, and only CI has a daemon — so this script exists to be run there, on both
images, on pull requests and on main.

It verifies and never mutates: no pull, no run, no push, no tag, no build. That
matters because it is invoked in CI with whatever image reference it is handed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "scripts" / "verify-image-provenance.sh"

SHA = "a" * 40
OTHER_SHA = "b" * 40
IMAGE = "ghcr.io/imiladco/product-intelligence/api@sha256:" + "1" * 64

REVISION_LABEL = "org.opencontainers.image.revision"
COMPAT_LABEL = "org.arkav.pi.migrations-backward-compatible"


@pytest.fixture
def fake_docker(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"

    (bin_dir / "docker").write_text(f"""#!/usr/bin/env bash
echo "$@" >> {calls}
if [[ "$1 $2" != "image inspect" ]]; then exit 0; fi
if [[ "$*" == *"revision"* ]]; then
  cat {tmp_path}/revision 2>/dev/null || echo "{SHA}"
else
  cat {tmp_path}/compat 2>/dev/null || echo "true"
fi
exit "$(cat {tmp_path}/inspect_status 2>/dev/null || echo 0)"
""")
    (bin_dir / "docker").chmod(0o755)
    return {"bin": bin_dir, "tmp": tmp_path, "calls": calls}


def verify(fake, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, timeout=30,
        env={"PATH": f"{fake['bin']}:/usr/bin:/bin"},
    )


class TestRevision:
    def test_matching_revision_passes(self, fake_docker):
        assert verify(fake_docker, IMAGE, SHA).returncode == 0

    def test_mismatching_revision_fails_and_names_the_label(self, fake_docker):
        (fake_docker["tmp"] / "revision").write_text(OTHER_SHA)

        result = verify(fake_docker, IMAGE, SHA)

        assert result.returncode != 0
        assert REVISION_LABEL in result.stdout + result.stderr

    @pytest.mark.parametrize("value", ["", "unknown", "<no value>"])
    def test_missing_or_default_revision_fails(self, fake_docker, value):
        (fake_docker["tmp"] / "revision").write_text(value)
        assert verify(fake_docker, IMAGE, SHA).returncode != 0

    def test_inspect_failure_fails_closed(self, fake_docker):
        (fake_docker["tmp"] / "inspect_status").write_text("1")
        assert verify(fake_docker, IMAGE, SHA).returncode != 0


class TestCompatibility:
    def test_it_is_checked_when_an_expected_value_is_given(self, fake_docker):
        (fake_docker["tmp"] / "compat").write_text("true")
        assert verify(fake_docker, IMAGE, SHA, "true").returncode == 0

    def test_a_mismatch_fails(self, fake_docker):
        (fake_docker["tmp"] / "compat").write_text("false")

        result = verify(fake_docker, IMAGE, SHA, "true")

        assert result.returncode != 0
        assert COMPAT_LABEL in result.stdout + result.stderr

    def test_it_is_not_checked_when_omitted(self, fake_docker):
        """The web image has no compatibility label and must still pass."""
        (fake_docker["tmp"] / "compat").write_text("")
        assert verify(fake_docker, IMAGE, SHA).returncode == 0

    def test_an_expected_false_is_honoured(self, fake_docker):
        """CI checks the image matches the repository's declaration, whatever
        that declaration says — this script reports drift, it does not decide
        policy. Refusing to deploy `false` is production's job."""
        (fake_docker["tmp"] / "compat").write_text("false")
        assert verify(fake_docker, IMAGE, SHA, "false").returncode == 0


class TestUsage:
    def test_it_requires_an_image_and_a_sha(self, fake_docker):
        assert verify(fake_docker).returncode != 0
        assert verify(fake_docker, IMAGE).returncode != 0


class TestItMutatesNothing:
    def test_it_issues_only_inspect_calls(self, fake_docker):
        verify(fake_docker, IMAGE, SHA, "true")

        for line in fake_docker["calls"].read_text().splitlines():
            assert line.startswith("image inspect"), f"unexpected docker call: {line}"

    def test_the_source_contains_no_mutating_verb(self):
        code = "\n".join(
            line for line in SCRIPT.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        for forbidden in ("docker pull", "docker run", "docker push", "docker rm",
                          "docker tag", "docker build", "compose up"):
            assert forbidden not in code, forbidden
