"""The release ledger: what the server knows about each environment.

This is the only durable record that a SHA passed staging, so production's
refusal to deploy an unproven release is exactly as trustworthy as this file's
write path. Two properties matter more than the rest:

  * **A failed deploy never leaves a successful entry.** Promotion is the only
    writer of `result: success`, and it runs after the health gate returns.
  * **A read of a missing or corrupt ledger fails closed.** It returns nothing
    and a non-zero status, so a caller that forgets to check gets no digests
    and no proof rather than an empty string it might treat as an answer.

Writes are atomic: a temporary file in the same directory, then rename. A
crash mid-write leaves the previous ledger intact, never a truncated one.

JSON is handled by python3, not jq — jq is not guaranteed on the host and no
script in this deployment requires it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = REPO_ROOT / "deploy" / "scripts" / "lib"
LEDGER_LIB = LIB_DIR / "ledger.sh"

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
API_A = "sha256:" + "1" * 64
WEB_A = "sha256:" + "2" * 64
API_B = "sha256:" + "3" * 64
WEB_B = "sha256:" + "4" * 64


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """An isolated state directory. No test touches a real deployment path."""
    directory = tmp_path / "state"
    directory.mkdir()
    return directory


def run(state_dir: Path, snippet: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f"source {LEDGER_LIB}; {snippet}"],
        capture_output=True,
        text=True,
        timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "PI_STATE_DIR": str(state_dir)},
    )


def ledger(state_dir: Path, environment: str = "staging") -> dict:
    return json.loads((state_dir / f"{environment}.json").read_text())


def history(state_dir: Path, environment: str = "staging") -> list[dict]:
    path = state_dir / "history" / f"{environment}.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class TestCandidateAndPromotion:
    def test_a_candidate_is_recorded_before_promotion(self, state_dir):
        result = run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        assert result.returncode == 0, result.stderr

        data = ledger(state_dir)
        assert data["candidate"]["sha"] == SHA_A
        assert data["candidate"]["api_digest"] == API_A
        assert data["candidate"]["web_digest"] == WEB_A
        assert data["candidate"]["migrations_backward_compatible"] == "true"
        assert data["current"] is None
        assert data["previous"] is None

    def test_promotion_moves_candidate_to_current(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        result = run(state_dir, "pi_ledger_promote_candidate staging")
        assert result.returncode == 0, result.stderr

        data = ledger(state_dir)
        assert data["current"]["sha"] == SHA_A
        assert data["candidate"] is None
        assert data["previous"] is None

    def test_a_second_promotion_moves_current_to_previous(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_B} {API_B} {WEB_B} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        data = ledger(state_dir)
        assert data["current"]["sha"] == SHA_B
        assert data["previous"]["sha"] == SHA_A
        assert data["previous"]["api_digest"] == API_A
        assert data["candidate"] is None

    def test_current_digests_are_readable(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        result = run(state_dir, "pi_ledger_read_current_digests staging")
        assert result.stdout.split() == [API_A, WEB_A]

    def test_current_sha_is_readable(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        assert run(state_dir, "pi_ledger_read_current_sha staging").stdout.strip() == SHA_A


class TestFailedAttempts:
    def test_a_failed_attempt_leaves_current_and_previous_untouched(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        run(state_dir, f'pi_ledger_set_candidate staging {SHA_B} {API_B} {WEB_B} true')
        result = run(state_dir, "pi_ledger_clear_candidate staging")
        assert result.returncode == 0, result.stderr

        data = ledger(state_dir)
        assert data["current"]["sha"] == SHA_A
        assert data["previous"] is None
        assert data["candidate"] is None

    def test_a_failed_sha_is_never_promotable(self, state_dir):
        """The false-proof guard.

        Production trusts this answer completely, so an attempt that failed
        must not be reported as a successful staging release.
        """
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_C} {API_A} {WEB_A} true')
        run(state_dir, 'pi_ledger_append_history staging ' + SHA_C + ' failure health_gate')
        run(state_dir, "pi_ledger_clear_candidate staging")

        assert run(state_dir, f"pi_ledger_has_successful_release staging {SHA_C}").returncode != 0

    def test_an_unknown_sha_is_never_promotable(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        assert run(state_dir, f"pi_ledger_has_successful_release staging {SHA_C}").returncode != 0


class TestSupersededReleasesRemainPromotable:
    def test_a_superseded_successful_sha_is_still_promotable(self, state_dir):
        """A release that passed staging and was then superseded must remain
        promotable — otherwise only the very latest SHA could ever reach
        production, and a rollback promotion would be impossible."""
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_B} {API_B} {WEB_B} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        assert run(state_dir, f"pi_ledger_has_successful_release staging {SHA_A}").returncode == 0

    def test_digests_for_a_superseded_sha_are_the_ones_it_passed_with(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_B} {API_B} {WEB_B} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        result = run(state_dir, f"pi_ledger_digests_for_sha staging {SHA_A}")
        assert result.returncode == 0
        assert result.stdout.split() == [API_A, WEB_A]

    def test_compatibility_is_recorded_and_readable_per_sha(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} false')
        run(state_dir, "pi_ledger_promote_candidate staging")

        result = run(state_dir, f"pi_ledger_compatibility_for_sha staging {SHA_A}")
        assert result.stdout.strip() == "false"


class TestFailClosedReads:
    def test_missing_ledger_reads_fail_closed(self, state_dir):
        for function in (
            "pi_ledger_read_current_sha staging",
            "pi_ledger_read_current_digests staging",
            f"pi_ledger_digests_for_sha staging {SHA_A}",
            f"pi_ledger_has_successful_release staging {SHA_A}",
        ):
            result = run(state_dir, function)
            assert result.returncode != 0, function
            assert result.stdout.strip() == "", function

    def test_corrupt_ledger_reads_fail_closed(self, state_dir):
        (state_dir / "staging.json").write_text("{")

        for function in (
            "pi_ledger_read_current_sha staging",
            f"pi_ledger_has_successful_release staging {SHA_A}",
        ):
            result = run(state_dir, function)
            assert result.returncode != 0, function
            assert result.stdout.strip() == "", function

    def test_a_truncated_ledger_does_not_report_a_release(self, state_dir):
        (state_dir / "staging.json").write_text('{"current": {"sha": "aaa')
        assert run(state_dir, f"pi_ledger_has_successful_release staging {SHA_A}").returncode != 0


class TestAtomicWrites:
    def test_no_temporary_file_remains_after_a_write(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        leftovers = [p.name for p in state_dir.iterdir() if ".tmp" in p.name]
        assert leftovers == []

    def test_the_ledger_parses_after_every_write(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        ledger(state_dir)
        run(state_dir, "pi_ledger_promote_candidate staging")
        ledger(state_dir)
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_B} {API_B} {WEB_B} true')
        ledger(state_dir)
        run(state_dir, "pi_ledger_clear_candidate staging")
        ledger(state_dir)

    def test_the_ledger_is_world_readable_but_not_world_writable(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        mode = (state_dir / "staging.json").stat().st_mode & 0o777
        assert mode == 0o644


class TestHistory:
    def test_history_is_append_only_jsonl(self, state_dir):
        run(state_dir, f"pi_ledger_append_history staging {SHA_A} success -")
        first = history(state_dir)[0]
        run(state_dir, f"pi_ledger_append_history staging {SHA_B} failure health_gate")
        run(state_dir, f"pi_ledger_append_history staging {SHA_C} rollback_failed -")

        records = history(state_dir)
        assert len(records) == 3
        assert records[0] == first, "an earlier record was rewritten"
        assert [r["result"] for r in records] == ["success", "failure", "rollback_failed"]

    def test_history_records_carry_a_timestamp(self, state_dir):
        run(state_dir, f"pi_ledger_append_history staging {SHA_A} success -")
        assert history(state_dir)[0]["deployed_at"]

    def test_history_records_the_environment(self, state_dir):
        run(state_dir, f"pi_ledger_append_history production {SHA_A} success -")
        assert history(state_dir, "production")[0]["environment"] == "production"


class TestEnvironmentIsolation:
    def test_the_two_environments_have_separate_ledgers(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        assert run(state_dir, "pi_ledger_read_current_sha production").returncode != 0

    def test_a_staging_release_is_not_a_production_release(self, state_dir):
        run(state_dir, f'pi_ledger_set_candidate staging {SHA_A} {API_A} {WEB_A} true')
        run(state_dir, "pi_ledger_promote_candidate staging")

        assert run(state_dir, f"pi_ledger_has_successful_release production {SHA_A}").returncode != 0

    def test_an_invalid_environment_is_rejected(self, state_dir):
        assert run(state_dir, "pi_ledger_read_current_sha ../../etc").returncode != 0
        assert run(state_dir, "pi_ledger_read_current_sha ''").returncode != 0


class TestSourceLevelGuarantees:
    def test_no_jq_dependency(self):
        """jq is not guaranteed on an Ubuntu host; python3 is."""
        code = "\n".join(
            line for line in LEDGER_LIB.read_text().splitlines()
            if not line.strip().startswith("#")
        )
        assert "jq " not in code
        assert "| jq" not in code

    def test_writes_go_through_a_temporary_file_and_a_rename(self):
        code = LEDGER_LIB.read_text()
        assert ".tmp" in code
        assert "mv -f" in code
