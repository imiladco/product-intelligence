"""The deployment manifests, checked for the things that silently destroy data.

Three failure modes drive this file, all of them quiet:

**A renamed volume.** The staging database lives in
`product-intelligence-staging_pgdata_staging` — note the doubled suffix, which
comes from a volume key of `pgdata_staging` inside a project named
`product-intelligence-staging`. Tidying that key to `pgdata` keeps the project
name identical and still points at a *different* volume, which Docker creates
empty on the spot. Postgres then initialises a fresh cluster and staging comes
up healthy with no users, no projects and no integrations. Declaring every
volume `external: true` by exact name turns that into a startup error instead.

**A renamed project.** Compose resolves the project name from `-p`, then
`COMPOSE_PROJECT_NAME`, then the `name:` key. The key is set, so relocating the
directory is safe — but an inherited environment variable would still redirect
every volume lookup. The manifests pin the name; the deploy scripts unset the
variable and never pass `-p`.

**A crossed environment.** Both projects contain services literally named `api`
and `web`. A proxy joined to both edge networks would find the name ambiguous,
so each service carries an explicit alias on its edge network only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGING = REPO_ROOT / "compose.staging.yaml"
PRODUCTION = REPO_ROOT / "compose.production.yaml"

STAGING_PROJECT = "product-intelligence-staging"
PRODUCTION_PROJECT = "product-intelligence-production"

#: The four volumes that already exist on the server. Legacy names, kept on
#: purpose: renaming one means copying a live database or a certificate.
STAGING_PGDATA = "product-intelligence-staging_pgdata_staging"
STAGING_STATIC = "product-intelligence-staging_static"
PRODUCTION_PGDATA = "product-intelligence-production_pgdata"
PRODUCTION_STATIC = "product-intelligence-production_static"


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


@pytest.fixture(scope="module")
def staging() -> dict:
    return load(STAGING)


def external_volume_names(manifest: dict) -> dict[str, str]:
    """Map each declared volume key to the external name it pins."""
    names = {}
    for key, spec in (manifest.get("volumes") or {}).items():
        assert isinstance(spec, dict), f"volume {key!r} must declare external: true"
        assert spec.get("external") is True, f"volume {key!r} is not external"
        assert "name" in spec, f"volume {key!r} does not pin an exact name"
        names[key] = spec["name"]
    return names


def external_network_names(manifest: dict) -> dict[str, str]:
    names = {}
    for key, spec in (manifest.get("networks") or {}).items():
        assert isinstance(spec, dict), f"network {key!r} must declare external: true"
        assert spec.get("external") is True, f"network {key!r} is not external"
        assert "name" in spec, f"network {key!r} does not pin an exact name"
        names[key] = spec["name"]
    return names


def edge_aliases(manifest: dict, service: str) -> list[str]:
    spec = manifest["services"][service]["networks"]["edge"] or {}
    return list(spec.get("aliases") or [])


class TestStagingIdentity:
    def test_staging_project_name_is_pinned(self, staging):
        assert staging["name"] == STAGING_PROJECT

    def test_staging_volumes_are_external_by_exact_name(self, staging):
        names = external_volume_names(staging)
        assert names["pgdata_staging"] == STAGING_PGDATA
        assert names["static"] == STAGING_STATIC

    def test_the_legacy_database_volume_key_is_untouched(self, staging):
        """`pgdata_staging`, not `pgdata`.

        The doubled name is ugly and deliberate. Renaming the key would point
        the manifest at a volume that does not exist, which — were it not
        external — Docker would create empty.
        """
        assert "pgdata_staging" in (staging.get("volumes") or {})
        assert "pgdata" not in (staging.get("volumes") or {})

    def test_staging_networks_are_external_by_exact_name(self, staging):
        names = external_network_names(staging)
        assert names["internal"] == "product-intelligence-staging-internal"
        assert names["edge"] == "product-intelligence-staging-edge"


class TestStagingTopology:
    def test_staging_has_no_build_sections(self, staging):
        for name, service in staging["services"].items():
            assert "build" not in service, f"{name} would build on the server"

    def test_staging_consumes_images_from_variables(self, staging):
        assert staging["services"]["api"]["image"] == "${API_IMAGE}"
        assert staging["services"]["web"]["image"] == "${WEB_IMAGE}"

    def test_staging_publishes_no_ports(self, staging):
        for name, service in staging["services"].items():
            assert "ports" not in service, f"{name} publishes a host port"

    def test_staging_has_no_caddy_service(self, staging):
        """Caddy is shared and independent; deploying an app must not recreate it."""
        assert "caddy" not in staging["services"]

    def test_staging_postgres_is_not_on_the_edge_network(self, staging):
        networks = staging["services"]["postgres"]["networks"]
        assert list(networks) == ["internal"]

    def test_staging_api_and_web_expose_disambiguated_edge_aliases(self, staging):
        assert edge_aliases(staging, "api") == ["staging-api"]
        assert edge_aliases(staging, "web") == ["staging-web"]

    def test_staging_ssr_targets_the_internal_service_name(self, staging):
        """Server-side rendering stays on the internal network.

        Pointing it at the edge alias would require DJANGO_ALLOWED_HOSTS to
        gain `staging-api` in lockstep, and forgetting that returns 400
        DisallowedHost — the failure .env.staging.example already warns about
        at length. Do not "simplify" this.
        """
        env = staging["services"]["web"]["environment"]
        assert env["INTERNAL_API_BASE_URL"] == "http://api:8000"
        assert "staging-api" not in str(env)

    def test_staging_api_receives_the_release_sha(self, staging):
        assert staging["services"]["api"]["environment"]["RELEASE_SHA"] == "${RELEASE_SHA}"

    def test_staging_api_start_period_is_reduced(self, staging):
        """120s was sized for entrypoint migrations, which no longer run."""
        assert staging["services"]["api"]["healthcheck"]["start_period"] == "45s"
