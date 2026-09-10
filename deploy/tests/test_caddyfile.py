"""Shared Caddy: two hostnames, one proxy, and no way for them to cross.

Caddy joins both edge networks, which is exactly the arrangement in which an
ambiguous upstream name becomes a production request served by staging. Every
assertion here is about keeping the two site blocks unable to reach each
other's containers or each other's static files.

Two Caddyfiles exist on purpose. The one-time handoff happens before
app.arkav.lol has a DNS record, and a site block for a hostname that does not
resolve makes Caddy retry ACME against it indefinitely. So the handoff installs
the staging-only file, and the full file is installed later by an explicit
control-plane update once DNS is valid.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
FULL_CADDYFILE = REPO_ROOT / "docker" / "caddy" / "Caddyfile"
STAGING_ONLY_CADDYFILE = REPO_ROOT / "deploy" / "caddy" / "Caddyfile.staging-only"
CADDY_COMPOSE = REPO_ROOT / "deploy" / "caddy" / "compose.yaml"

STAGING_HOST = "staging.arkav.lol"
PRODUCTION_HOST = "app.arkav.lol"


def site_blocks(path: Path) -> dict[str, str]:
    """Split a Caddyfile into {hostname: block body}.

    Deliberately simple: site headers in these files are a bare hostname
    followed by `{`, and the closing brace of a site block is at column 0.
    """
    text = path.read_text()
    blocks: dict[str, str] = {}
    current: str | None = None
    collected: list[str] = []
    for line in text.splitlines():
        header = re.match(r"^(\S+)\s*\{\s*$", line)
        if header and "." in header.group(1):
            current = header.group(1)
            collected = []
            continue
        if current is not None and line.startswith("}"):
            blocks[current] = "\n".join(collected)
            current = None
            continue
        if current is not None:
            collected.append(line)
    return blocks


@pytest.fixture(scope="module")
def full() -> dict[str, str]:
    return site_blocks(FULL_CADDYFILE)


@pytest.fixture(scope="module")
def staging_only() -> dict[str, str]:
    return site_blocks(STAGING_ONLY_CADDYFILE)


class TestSiteCoverage:
    def test_full_caddyfile_serves_both_hosts(self, full):
        assert set(full) == {STAGING_HOST, PRODUCTION_HOST}

    def test_staging_only_caddyfile_omits_production(self, staging_only):
        """Installed for the handoff, before app.arkav.lol resolves.

        A production site block at that point produces repeated ACME failures
        against a hostname with no DNS record.
        """
        assert set(staging_only) == {STAGING_HOST}
        # Scoped to directives, not to the whole text: the file's header names
        # app.arkav.lol while explaining why it is absent, and a comment starts
        # no ACME challenge. What must not appear is a site block or an
        # upstream, and the site-block assertion above already covers the first.
        assert "production-api" not in STAGING_ONLY_CADDYFILE.read_text()
        assert "production-web" not in STAGING_ONLY_CADDYFILE.read_text()
        assert not re.search(
            rf"^{re.escape(PRODUCTION_HOST)}\s*\{{", STAGING_ONLY_CADDYFILE.read_text(), re.MULTILINE
        )

    def test_both_files_configure_the_acme_account(self):
        for path in (FULL_CADDYFILE, STAGING_ONLY_CADDYFILE):
            assert "{$ACME_EMAIL}" in path.read_text(), path


class TestNoCrossEnvironmentRouting:
    def test_each_site_proxies_only_its_own_upstreams(self, full):
        staging_block = full[STAGING_HOST]
        production_block = full[PRODUCTION_HOST]

        assert "staging-api:8000" in staging_block
        assert "staging-web:3000" in staging_block
        assert "production-api" not in staging_block
        assert "production-web" not in staging_block

        assert "production-api:8000" in production_block
        assert "production-web:3000" in production_block
        assert "staging-api" not in production_block
        assert "staging-web" not in production_block

    def test_no_site_uses_the_ambiguous_bare_service_name(self):
        """`reverse_proxy api:8000` resolves to whichever environment answers
        first when the proxy is on both edge networks."""
        for path in (FULL_CADDYFILE, STAGING_ONLY_CADDYFILE):
            text = path.read_text()
            assert "reverse_proxy api:" not in text, path
            assert "reverse_proxy web:" not in text, path


class TestStaticRootsCannotOverlap:
    def test_each_site_roots_its_own_static_directory(self, full):
        assert "/srv/static/staging" in full[STAGING_HOST]
        assert "/srv/static/production" not in full[STAGING_HOST]

        assert "/srv/static/production" in full[PRODUCTION_HOST]
        assert "/srv/static/staging" not in full[PRODUCTION_HOST]

    def test_neither_site_roots_the_shared_parent_directory(self, full):
        """Rooting at /srv/static would expose both environments' assets under
        one prefix, which is the collision the split mounts exist to avoid."""
        for host, block in full.items():
            assert not re.search(r"root \* /srv/static\s*$", block, re.MULTILINE), host


class TestNoQueryStringLeak:
    def test_neither_caddyfile_enables_access_logging(self):
        """Caddy's access log records the full URI, query string included.

        The OAuth callback carries ?code=…&state=… there. M3 removed exactly
        that leak from gunicorn's access log; re-introducing it at the proxy
        would undo the fix one layer up. Logging here needs its own design.
        """
        for path in (FULL_CADDYFILE, STAGING_ONLY_CADDYFILE):
            text = path.read_text()
            assert not re.search(r"^\s*log\s*\{", text, re.MULTILINE), path
            assert not re.search(r"^\s*log\s+", text, re.MULTILINE), path


class TestSharedCaddyCompose:
    @pytest.fixture(scope="module")
    def compose(self) -> dict:
        return yaml.safe_load(CADDY_COMPOSE.read_text())

    def test_shared_caddy_is_its_own_project(self, compose):
        """Independent of both app deployments: deploying staging or production
        must never recreate the proxy."""
        assert compose["name"] == "product-intelligence-shared"

    def test_shared_caddy_adopts_the_legacy_certificate_volumes(self, compose):
        """Legacy staging names, kept deliberately.

        They hold the ACME account and a live certificate. Renaming them means
        copying certificate state; reusing them means the handoff issues
        nothing and stays inside Let's Encrypt's rate limits.
        """
        volumes = compose["volumes"]
        assert volumes["caddy_data"]["name"] == "product-intelligence-staging_caddy_data"
        assert volumes["caddy_config"]["name"] == "product-intelligence-staging_caddy_config"
        for spec in volumes.values():
            assert spec["external"] is True

    def test_shared_caddy_mounts_both_static_volumes_at_distinct_paths(self, compose):
        mounts = compose["services"]["caddy"]["volumes"]
        assert any("/srv/static/staging:ro" in m for m in mounts)
        assert any("/srv/static/production:ro" in m for m in mounts)

    def test_static_mounts_are_read_only(self, compose):
        for mount in compose["services"]["caddy"]["volumes"]:
            if "/srv/static/" in mount:
                assert mount.endswith(":ro"), mount

    def test_shared_caddy_joins_both_edges_and_neither_internal(self, compose):
        networks = compose["services"]["caddy"]["networks"]
        assert set(networks) == {"staging_edge", "production_edge"}

        declared = {key: spec["name"] for key, spec in compose["networks"].items()}
        assert declared["staging_edge"] == "product-intelligence-staging-edge"
        assert declared["production_edge"] == "product-intelligence-production-edge"
        assert not any("internal" in name for name in declared.values())

    def test_shared_caddy_owns_the_public_ports(self, compose):
        assert set(compose["services"]["caddy"]["ports"]) == {"80:80", "443:443"}

    def test_shared_caddy_does_not_depend_on_application_services(self, compose):
        """A depends_on would couple the proxy's lifecycle to a deployment."""
        assert "depends_on" not in compose["services"]["caddy"]

    def test_the_production_static_volume_is_required_before_caddy_starts(self, compose):
        """It is external, so it must exist before the handoff — which is why
        the storage skeleton creates it empty, ahead of production bootstrap."""
        assert (
            compose["volumes"]["production_static"]["name"]
            == "product-intelligence-production_static"
        )
        assert compose["volumes"]["production_static"]["external"] is True


class TestStructuralValidity:
    """A cheap local guard.

    `caddy validate` is the real check and needs a Docker daemon, which this
    implementation environment does not have. It runs in CI (Task 15) and is
    mandatory before the live cutover (L09). These assertions catch the gross
    structural mistakes — an unbalanced brace, a site block with no upstream —
    that would otherwise reach that point unnoticed.
    """

    @pytest.mark.parametrize(
        "path", [FULL_CADDYFILE, STAGING_ONLY_CADDYFILE], ids=["full", "staging-only"]
    )
    def test_braces_balance(self, path):
        text = "\n".join(
            line for line in path.read_text().splitlines() if not line.strip().startswith("#")
        )
        assert text.count("{") == text.count("}"), f"unbalanced braces in {path.name}"

    @pytest.mark.parametrize(
        "path", [FULL_CADDYFILE, STAGING_ONLY_CADDYFILE], ids=["full", "staging-only"]
    )
    def test_every_site_block_has_a_catch_all_upstream(self, path):
        for host, block in site_blocks(path).items():
            assert "handle {" in block, f"{host} has no catch-all handler"
            assert "reverse_proxy" in block, f"{host} proxies nothing"

    @pytest.mark.parametrize(
        "path", [FULL_CADDYFILE, STAGING_ONLY_CADDYFILE], ids=["full", "staging-only"]
    )
    def test_every_site_block_serves_static_before_falling_through(self, path):
        """handle_path /static/* must come before the catch-all, or Django's
        admin CSS would be proxied to Next.js and 404."""
        for host, block in site_blocks(path).items():
            static_at = block.index("handle_path /static/*")
            catch_all_at = block.index("handle {")
            assert static_at < catch_all_at, host
