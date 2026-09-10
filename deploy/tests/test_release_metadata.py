"""Release metadata, and the labels that carry it into an image.

The deploy path must be able to answer two questions about an image it is
about to run, without trusting whoever asked for the deploy:

  1. Which commit is this?          org.opencontainers.image.revision
  2. Are its migrations backward    org.arkav.pi.migrations-backward-compatible
     compatible?                    (API only)

Both answers ride on the image, so they are fixed by the same digest the deploy
pulls. A caller cannot assert either one: the client supplies a SHA and (for
staging) digests, and the server reads the labels back from what it actually
pulled. That is the whole point of putting them here rather than in a request.

This file holds the *declaration* links of the chain — the metadata file and
the Dockerfile instructions. Task 09 verifies a pulled image, Tasks 12 and 13
verify both images before any release step, and Task 16B proves the chain
against really-built, really-pushed artifacts in CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
METADATA = REPO_ROOT / "deploy" / "release-metadata.json"
API_DOCKERFILE = REPO_ROOT / "apps" / "api" / "Dockerfile"
WEB_DOCKERFILE = REPO_ROOT / "apps" / "web" / "Dockerfile"

REVISION_LABEL = "org.opencontainers.image.revision"
COMPAT_LABEL = "org.arkav.pi.migrations-backward-compatible"


class TestMetadataFile:
    def test_metadata_file_exists_and_is_valid_json(self):
        assert METADATA.is_file(), f"{METADATA} is the source of the compatibility claim"
        json.loads(METADATA.read_text())

    def test_metadata_declares_only_the_compatibility_field(self):
        """One field. This is a declaration, not a config file.

        Anything else here would become something the deploy path is tempted to
        read, and the deploy path reads labels, not repository files.
        """
        data = json.loads(METADATA.read_text())
        assert set(data) == {"migrations_backward_compatible"}

    def test_compatibility_value_is_a_json_boolean(self):
        """A JSON boolean, never the string "true".

        A string is truthy in Python and ambiguous in shell, and the whole
        contract depends on `true` meaning exactly one thing.
        """
        value = json.loads(METADATA.read_text())["migrations_backward_compatible"]
        assert isinstance(value, bool), f"expected a boolean, got {type(value).__name__}"


class TestApiImageLabels:
    def test_api_dockerfile_declares_both_args_and_both_labels(self):
        text = API_DOCKERFILE.read_text()

        assert "ARG GIT_SHA=" in text
        assert f"LABEL {REVISION_LABEL}=" in text
        assert "ARG MIGRATIONS_BACKWARD_COMPATIBLE=" in text
        assert f"LABEL {COMPAT_LABEL}=" in text

    def test_api_compatibility_arg_defaults_to_false(self):
        """Fail closed at the earliest possible moment.

        A build that forgets --build-arg produces an image that declares
        `false`, which production refuses (design §11.2). The alternative — a
        `true` default — would make forgetting the argument silently produce a
        deployable artifact, which is the failure mode this whole gate exists
        to prevent.
        """
        assert "ARG MIGRATIONS_BACKWARD_COMPATIBLE=false" in API_DOCKERFILE.read_text()

    def test_api_revision_arg_defaults_to_a_value_that_is_not_a_sha(self):
        """`unknown` can never equal a requested 40-hex SHA, so an unstamped
        image fails the Task 09 revision check rather than passing it."""
        match = re.search(r"^ARG GIT_SHA=(.*)$", API_DOCKERFILE.read_text(), re.MULTILINE)
        assert match, "GIT_SHA must have an explicit default"
        default = match.group(1).strip()
        assert not re.fullmatch(r"[0-9a-f]{40}", default)
        assert default == "unknown"

    def test_api_labels_are_stamped_after_the_last_copy_or_run(self):
        """Layer caching: the labels change every build, the dependencies do not.

        Placing them above a COPY or RUN would invalidate that layer on every
        commit and make each CI build reinstall the dependency set.
        """
        lines = API_DOCKERFILE.read_text().splitlines()
        last_build_step = max(
            i for i, line in enumerate(lines) if re.match(r"^\s*(COPY|RUN)\b", line)
        )
        first_label = min(
            i for i, line in enumerate(lines) if re.match(r"^\s*LABEL\b", line)
        )
        assert first_label > last_build_step, (
            "LABEL instructions must follow the last COPY/RUN so they do not "
            "invalidate the dependency layers"
        )


class TestWebImageLabels:
    def test_web_dockerfile_declares_the_revision_arg_and_label(self):
        text = WEB_DOCKERFILE.read_text()

        assert "ARG GIT_SHA=" in text
        assert f"LABEL {REVISION_LABEL}=" in text

    def test_web_revision_arg_defaults_to_unknown(self):
        match = re.search(r"^ARG GIT_SHA=(.*)$", WEB_DOCKERFILE.read_text(), re.MULTILINE)
        assert match
        assert match.group(1).strip() == "unknown"

    def test_web_does_not_claim_migration_compatibility(self):
        """Only the API has migrations. A compatibility label on the web image
        would be a second, unsynchronised answer to a question it cannot have."""
        assert COMPAT_LABEL not in WEB_DOCKERFILE.read_text()

    def test_web_labels_are_stamped_in_the_final_stage(self):
        """A label on a builder stage does not reach the published image."""
        text = WEB_DOCKERFILE.read_text()
        lines = text.splitlines()
        last_from = max(i for i, line in enumerate(lines) if line.startswith("FROM "))
        label_lines = [i for i, line in enumerate(lines) if re.match(r"^\s*LABEL\b", line)]
        assert label_lines, "no LABEL found"
        assert min(label_lines) > last_from, "labels must be in the final stage"


class TestBothImagesCarryTheRevision:
    """The chain is only as strong as its weaker image.

    A web image built from another commit is exactly as wrong as an API image
    built from another commit, so neither Dockerfile is allowed to omit the
    revision label.
    """

    def test_every_deployed_image_declares_the_revision_label(self):
        for dockerfile in (API_DOCKERFILE, WEB_DOCKERFILE):
            text = dockerfile.read_text()
            assert f"LABEL {REVISION_LABEL}=$GIT_SHA" in text, dockerfile
