"""The deploy command grammar — the only surface a client can reach.

Everything a caller sends arrives as one string in SSH_ORIGINAL_COMMAND. These
tests are adversarial about that string: the injection battery below fires
command substitution, chained commands and pipes at the parser and asserts that
no file appears anywhere as a result.

Two structural properties do most of the work, and both are asserted here:

  * The **environment** comes from the parser's first argument, which the forced
    command in authorized_keys supplies. It is never read from the client
    string, so a staging key cannot name production however it asks.
  * The **repository** is a hard-coded constant. No function accepts a registry
    or repository from a caller, so a deploy cannot be pointed at another
    account's image — not because the prefix is validated, but because none is
    ever accepted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "deploy" / "scripts" / "lib" / "validate.sh"

SHA = "a" * 40
API_DIGEST = "sha256:" + "b" * 64
WEB_DIGEST = "sha256:" + "c" * 64

API_REPO = "ghcr.io/imiladco/product-intelligence/api"
WEB_REPO = "ghcr.io/imiladco/product-intelligence/web"

#: The canary the injection battery tries to create.
PWN = Path("/tmp/pi_pwn")


def call(snippet: str) -> subprocess.CompletedProcess:
    """Source the library and run one snippet. No shell interpolation of input:
    the snippet is authored by the test, never by a simulated client."""
    return subprocess.run(
        ["bash", "-c", f"source {LIB}; {snippet}"],
        capture_output=True,
        text=True,
        timeout=30,
    )


def parse(environment: str, client_string: str) -> subprocess.CompletedProcess:
    """Invoke pi_parse_command with the client string passed as an argv element.

    The string reaches bash as a single argument, exactly as
    SSH_ORIGINAL_COMMAND does — it is never spliced into the snippet text.
    """
    return subprocess.run(
        ["bash", "-c", f'source {LIB}; pi_parse_command "$1" "$2"', "_", environment, client_string],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture(autouse=True)
def remove_canary():
    PWN.unlink(missing_ok=True)
    yield
    existed = PWN.exists()
    PWN.unlink(missing_ok=True)
    assert not existed, "the parser executed injected input"


class TestShaValidation:
    def test_accepts_a_valid_sha(self):
        assert call(f'pi_validate_sha "{SHA}"').returncode == 0

    @pytest.mark.parametrize(
        "value,why",
        [
            ("a" * 39, "too short"),
            ("a" * 41, "too long"),
            ("A" * 40, "uppercase"),
            ("g" * 40, "non-hex"),
            ("", "empty"),
            ("a" * 39 + " ", "trailing space"),
            (" " + "a" * 40, "leading space"),
        ],
        ids=["short", "long", "uppercase", "nonhex", "empty", "trailing-space", "leading-space"],
    )
    def test_rejects_malformed_sha(self, value, why):
        assert call(f'pi_validate_sha "{value}"').returncode != 0, why

    def test_rejects_a_sha_with_a_trailing_newline(self):
        result = subprocess.run(
            ["bash", "-c", f'source {LIB}; pi_validate_sha "$1"', "_", SHA + "\n"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode != 0


class TestDigestValidation:
    def test_accepts_a_valid_digest(self):
        assert call(f'pi_validate_digest "{API_DIGEST}"').returncode == 0

    @pytest.mark.parametrize(
        "value,why",
        [
            ("b" * 64, "no algorithm prefix"),
            ("sha256:" + "b" * 63, "63 hex"),
            ("sha256:" + "b" * 65, "65 hex"),
            ("sha512:" + "b" * 64, "wrong algorithm"),
            ("sha256:" + "B" * 64, "uppercase"),
            ("sha256:" + "g" * 64, "non-hex"),
            ("", "empty"),
            ("sha256:", "prefix only"),
        ],
        ids=["no-prefix", "short", "long", "sha512", "uppercase", "nonhex", "empty", "prefix-only"],
    )
    def test_rejects_malformed_digest(self, value, why):
        assert call(f'pi_validate_digest "{value}"').returncode != 0, why


class TestEnvironmentValidation:
    @pytest.mark.parametrize("value", ["staging", "production"])
    def test_accepts_the_two_known_environments(self, value):
        assert call(f'pi_validate_environment "{value}"').returncode == 0

    @pytest.mark.parametrize("value", ["", "prod", "Staging", "PRODUCTION", "staging2", "../production"])
    def test_rejects_anything_else(self, value):
        assert call(f'pi_validate_environment "{value}"').returncode != 0


class TestImageReferencesAreServerControlled:
    def test_api_reference_is_built_from_a_constant_prefix(self):
        result = call(f'pi_api_image_ref "{API_DIGEST}"')
        assert result.returncode == 0
        assert result.stdout.strip() == f"{API_REPO}@{API_DIGEST}"

    def test_web_reference_is_built_from_a_constant_prefix(self):
        result = call(f'pi_web_image_ref "{WEB_DIGEST}"')
        assert result.returncode == 0
        assert result.stdout.strip() == f"{WEB_REPO}@{WEB_DIGEST}"

    def test_reference_builders_reject_a_malformed_digest(self):
        """A caller must not be able to append arbitrary text to the repository."""
        assert call('pi_api_image_ref "latest"').returncode != 0
        assert call('pi_api_image_ref "@evil.example/x"').returncode != 0

    def test_the_registry_appears_only_as_a_literal_constant(self):
        """No variable registry, no caller-supplied repository."""
        text = LIB.read_text()
        assert text.count("ghcr.io") == 2, "expected exactly the two literal repositories"
        assert API_REPO in text
        assert WEB_REPO in text


class TestGrammar:
    def test_staging_accepts_a_sha_and_two_digests(self):
        result = parse("staging", f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")
        assert result.returncode == 0
        assert result.stdout.split() == ["deploy", SHA, API_DIGEST, WEB_DIGEST]

    def test_staging_requires_both_digests(self):
        assert parse("staging", f"deploy {SHA}").returncode == 2
        assert parse("staging", f"deploy {SHA} {API_DIGEST}").returncode == 2

    def test_production_accepts_a_bare_sha(self):
        result = parse("production", f"deploy {SHA}")
        assert result.returncode == 0
        assert result.stdout.split() == ["deploy", SHA]

    def test_production_grammar_rejects_client_digests(self):
        """The production digest-injection guard.

        Production takes its digests from the staging ledger. A grammar that
        accepted them from the client would make the whole staging-proof chain
        decorative.
        """
        result = parse("production", f"deploy {SHA} {API_DIGEST} {WEB_DIGEST}")
        assert result.returncode == 2
        assert API_DIGEST not in result.stdout

    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_status_is_accepted_everywhere(self, environment):
        result = parse(environment, "status")
        assert result.returncode == 0
        assert result.stdout.strip() == "status"

    def test_environment_comes_from_the_first_argument(self):
        """A staging key that asks for production still gets staging's grammar.

        The forced command supplies the environment; the client string cannot
        change it, so this parses as staging and rejects the word `production`
        as a malformed SHA.
        """
        result = parse("staging", f"deploy production {API_DIGEST} {WEB_DIGEST}")
        assert result.returncode == 2

    @pytest.mark.parametrize(
        "client_string",
        ["", "deploy", "status extra", "restart", "deploy --force " + SHA,
         f"deploy {SHA} --force", f"DEPLOY {SHA}", f"deploy  {SHA}  extra"],
    )
    def test_rejects_anything_outside_the_grammar(self, client_string):
        for environment in ("staging", "production"):
            assert parse(environment, client_string).returncode == 2

    def test_rejection_does_not_echo_the_input(self):
        """Rejected input may carry control characters; it is never repeated."""
        result = parse("staging", "deploy \x1b[2J;rm -rf /")
        assert result.returncode == 2
        assert "rm -rf" not in result.stdout + result.stderr
        assert "invalid command" in (result.stdout + result.stderr)


class TestInjectionBattery:
    """Every one of these must be rejected, and none may execute anything.

    The autouse fixture fails the test if the canary file exists afterwards, so
    a parser that shelled out would be caught even if it also returned 2.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            "deploy $(touch /tmp/pi_pwn)",
            "deploy `touch /tmp/pi_pwn`",
            f"deploy {SHA}; touch /tmp/pi_pwn",
            f"deploy {SHA} && touch /tmp/pi_pwn",
            f"deploy {SHA} | touch /tmp/pi_pwn",
            f"deploy {SHA}\ntouch /tmp/pi_pwn",
            "deploy ${IFS}touch${IFS}/tmp/pi_pwn",
            "status; touch /tmp/pi_pwn",
            "deploy $(( 1 )) touch /tmp/pi_pwn",
        ],
        ids=["cmdsub", "backtick", "semicolon", "and", "pipe", "newline", "ifs", "status-chain", "arith"],
    )
    @pytest.mark.parametrize("environment", ["staging", "production"])
    def test_injection_is_rejected_and_executes_nothing(self, environment, payload):
        result = parse(environment, payload)
        assert result.returncode == 2, f"accepted: {payload!r}"


def executable_lines(path: Path) -> str:
    """Source with comments stripped.

    These guards are about what the shell runs, not about what the file says.
    The library's own header documents that it uses no eval and no `bash -c`,
    and a whole-text scan would flag that sentence as the very thing it
    promises not to do.
    """
    return "\n".join(
        line for line in path.read_text().splitlines() if not line.strip().startswith("#")
    )


class TestSourceLevelGuarantees:
    def test_library_contains_no_eval(self):
        code = executable_lines(LIB)
        assert "eval " not in code
        assert "eval\t" not in code

    def test_library_never_shells_out_to_interpret_input(self):
        code = executable_lines(LIB)
        assert "bash -c" not in code
        assert "sh -c" not in code

    def test_library_fails_fast(self):
        assert "set -euo pipefail" in LIB.read_text()

    def test_parsing_uses_an_array_not_word_splitting_of_an_expansion(self):
        """`read -r -a` keeps the client string as data.

        Splitting it with an unquoted expansion would let glob characters in
        the input match filenames on the server.
        """
        assert "read -r -a" in LIB.read_text()
