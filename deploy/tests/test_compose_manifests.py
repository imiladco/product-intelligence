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


@pytest.fixture(scope="module")
def production() -> dict:
    return load(PRODUCTION)


class TestProductionIdentity:
    def test_production_manifest_exists(self):
        assert PRODUCTION.is_file()

    def test_production_project_name_is_pinned(self, production):
        assert production["name"] == PRODUCTION_PROJECT

    def test_production_volumes_are_external_by_exact_name(self, production):
        names = external_volume_names(production)
        assert names["pgdata"] == PRODUCTION_PGDATA
        assert names["static"] == PRODUCTION_STATIC

    def test_production_networks_are_external_by_exact_name(self, production):
        names = external_network_names(production)
        assert names["internal"] == "product-intelligence-production-internal"
        assert names["edge"] == "product-intelligence-production-edge"

    def test_no_production_volume_or_network_names_a_staging_resource(self, production):
        """A copy-paste that left a staging name behind would attach production
        to the staging database — the worst outcome available in this file."""
        for spec in (production.get("volumes") or {}).values():
            assert "staging" not in spec["name"]
        for spec in (production.get("networks") or {}).values():
            assert "staging" not in spec["name"]


class TestProductionTopology:
    def test_production_edge_aliases_do_not_collide_with_staging(
        self, staging, production
    ):
        """The single most dangerous copy-paste in this milestone.

        Two projects both aliasing `staging-api` on their own edge network
        would leave shared Caddy resolving whichever it found, and production
        traffic could land on staging.
        """
        staging_aliases = set(edge_aliases(staging, "api")) | set(
            edge_aliases(staging, "web")
        )
        production_aliases = set(edge_aliases(production, "api")) | set(
            edge_aliases(production, "web")
        )
        assert staging_aliases.isdisjoint(production_aliases)
        assert production_aliases == {"production-api", "production-web"}

    def test_production_postgres_is_not_on_the_edge_network(self, production):
        assert list(production["services"]["postgres"]["networks"]) == ["internal"]

    def test_production_ssr_targets_the_internal_service_name(self, production):
        env = production["services"]["web"]["environment"]
        assert env["INTERNAL_API_BASE_URL"] == "http://api:8000"
        assert "production-api" not in str(env)

    def test_production_api_receives_the_release_sha(self, production):
        assert (
            production["services"]["api"]["environment"]["RELEASE_SHA"]
            == "${RELEASE_SHA}"
        )


class TestBothManifests:
    """Invariants that must hold for every environment, checked together so a
    new environment cannot be added without them."""

    @pytest.fixture(params=[STAGING, PRODUCTION], ids=["staging", "production"])
    def manifest(self, request) -> dict:
        return load(request.param)

    def test_neither_manifest_contains_a_build_section(self, manifest):
        for name, service in manifest["services"].items():
            assert "build" not in service, f"{name} would build on the server"

    def test_neither_manifest_publishes_ports(self, manifest):
        for name, service in manifest["services"].items():
            assert "ports" not in service, f"{name} publishes a host port"

    def test_neither_manifest_runs_caddy(self, manifest):
        assert "caddy" not in manifest["services"]

    def test_every_volume_is_external(self, manifest):
        external_volume_names(manifest)  # asserts internally

    def test_every_network_is_external(self, manifest):
        external_network_names(manifest)  # asserts internally

    def test_api_and_web_consume_image_variables(self, manifest):
        assert manifest["services"]["api"]["image"] == "${API_IMAGE}"
        assert manifest["services"]["web"]["image"] == "${WEB_IMAGE}"

    def test_api_start_period_is_the_reduced_value(self, manifest):
        assert manifest["services"]["api"]["healthcheck"]["start_period"] == "45s"


class TestEnvFileStaysRequired:
    """`env_file: [.env]` must stay mandatory in both manifests.

    Compose refuses to render a manifest whose env_file is missing, which is
    what makes a deploy with no configuration impossible. Marking it
    `required: false` would turn that hard stop into a container starting with
    no database URL, no secret key and no encryption keys.

    This is the same shape of decision as the ACME email: CI supplies a
    placeholder for the file, rather than the manifest excusing its absence.
    """

    def test_both_manifests_require_the_env_file(self):
        for path in (STAGING, PRODUCTION):
            manifest = load(path)
            for name, service in manifest["services"].items():
                declared = service.get("env_file")
                if declared is None:
                    continue
                entries = declared if isinstance(declared, list) else [declared]
                for entry in entries:
                    assert not isinstance(entry, dict) or entry.get("required") is not False, (
                        f"{path.name}: {name} makes its env_file optional"
                    )

    def test_the_api_service_reads_the_env_file(self):
        for path in (STAGING, PRODUCTION):
            manifest = load(path)
            assert manifest["services"]["api"].get("env_file"), path.name
