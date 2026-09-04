"""The provider boundary itself: what the shared layer may know, and what it may not.

Two things are guarded here.

First, containment: provider vocabulary must stay inside its own module. This
is asserted by reading the source, because it is the kind of leak that a code
review catches only when someone remembers to look for it.

Second, the catalog contract: a provider without a catalog has no resource
selection, and both endpoints say so with a 404 rather than a 500.
"""

from __future__ import annotations

import pathlib

import pytest

from integrations.providers import CATALOG, get_provider
from integrations.resources import RemoteResource, ResourceCatalog, ResourceListing

INTEGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "integrations"

#: Words that belong to exactly one provider, and the file each is allowed in.
PROVIDER_VOCABULARY = {
    "properties/": "ga4.py",
    "accountSummaries": "ga4.py",
    "analyticsadmin": "ga4.py",
    "propertySummaries": "ga4.py",
    "sc-domain": "search_console.py",
    "webmasters": "search_console.py",
    "siteEntry": "search_console.py",
    "permissionLevel": "search_console.py",
}

#: Files that are allowed to name a provider at all: the provider modules
#: themselves, and the catalog entries that wire them up.
ALLOWED_FILES = {
    "ga4.py",
    "search_console.py",
    "google_ga4.py",
    "google_search_console.py",
}


def _source_files():
    for path in INTEGRATIONS_DIR.rglob("*.py"):
        if "__pycache__" in path.parts or "migrations" in path.parts:
            continue
        yield path


@pytest.mark.parametrize("term,owner", sorted(PROVIDER_VOCABULARY.items()))
def test_provider_vocabulary_stays_in_its_own_module(term, owner):
    """A GA4 or Search Console concept must not reach shared code.

    The abstraction is only worth having if it actually holds. `account`,
    `property_type` and the rest are values inside a provider module or inside
    an opaque metadata mapping — never names the shared layer knows.
    """
    offenders = [
        path.name
        for path in _source_files()
        if path.name not in ALLOWED_FILES and term in path.read_text()
    ]
    assert offenders == [], (
        f"{term!r} belongs in {owner}, but also appears in: {sorted(set(offenders))}"
    )


def test_the_orchestration_layer_names_no_provider():
    """resource_service must dispatch, not decide."""
    source = (INTEGRATIONS_DIR / "resource_service.py").read_text()
    for term in ("ga4", "search_console", "GA4", "SEARCH_CONSOLE"):
        assert term not in source, f"resource_service.py names a provider: {term!r}"


def test_every_catalog_satisfies_the_protocol():
    for provider in CATALOG:
        if provider.resources is None:
            continue
        assert isinstance(provider.resources, ResourceCatalog)
        for method in ("normalize_resource_id", "list_resources", "verify_resource"):
            assert callable(getattr(provider.resources, method))


def test_the_protocol_has_exactly_three_methods():
    """A guard against the abstraction growing on its own.

    A fourth method is added when a fourth call site exists in two
    implementations — deliberately, with this test updated to say so.
    """
    methods = {
        name
        for name in vars(ResourceCatalog)
        if not name.startswith("_") and callable(getattr(ResourceCatalog, name, None))
    }
    assert methods == {"normalize_resource_id", "list_resources", "verify_resource"}


def test_both_providers_supply_a_catalog():
    assert get_provider("ga4").resources is not None
    assert get_provider("search_console").resources is not None


def test_remote_resource_requires_only_an_id_and_a_label():
    """Everything else is optional, because not every provider has it."""
    minimal = RemoteResource(id="x", label="X")
    assert minimal.resource_type == ""
    assert minimal.group_label == ""
    assert minimal.metadata == {}


def test_a_listing_is_untruncated_unless_a_provider_says_otherwise():
    assert ResourceListing(resources=()).truncated is False


@pytest.mark.django_db
class TestProviderWithoutACatalog:
    """A provider that does not support resource selection answers 404."""

    def test_both_endpoints_are_not_found(
        self, monkeypatch, signed_in_client, make_project
    ):
        from integrations import providers

        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        catalogless = providers.get_provider("ga4").__class__(
            key="ga4",
            display_name="Catalogless",
            description="A provider with no resource selection.",
            oauth_scopes=(),
            resources=None,
        )
        monkeypatch.setattr(providers, "_BY_KEY", {"ga4": catalogless})

        assert (
            client.get(f"/api/projects/{project.pk}/integrations/ga4/resources").status_code
            == 404
        )
        assert (
            client.post(
                f"/api/projects/{project.pk}/integrations/ga4/resource",
                {"resource_id": "properties/1"},
                format="json",
            ).status_code
            == 404
        )


@pytest.mark.django_db
class TestCapabilityIsReportedToTheFrontend:
    """The entry payload says what a provider can do, not just where it is.

    The UI gates the resource-selection action on this, so it has to be true
    independently of connection status — otherwise a healthy connection to a
    provider with no catalog offers an action that cannot work.
    """

    def test_entries_report_whether_the_provider_supports_selection(
        self, signed_in_client, make_project
    ):
        client, _user, workspace = signed_in_client
        project = make_project(workspace)

        entries = {
            entry["provider"]: entry
            for entry in client.get(
                f"/api/projects/{project.pk}/integrations"
            ).data
        }

        assert entries["ga4"]["supports_resource_selection"] is True
        assert entries["search_console"]["supports_resource_selection"] is True

    def test_a_provider_without_a_catalog_reports_false_even_when_connected(
        self, monkeypatch, signed_in_client, make_project
    ):
        """Capability and status are independent, and this is the proof.

        A connection in the healthiest state there is, on a provider that
        cannot list resources, must still report that it supports no selection.
        """
        from integrations import providers
        from integrations.models import IntegrationConnection
        from integrations.status import ConnectionStatus

        client, _user, workspace = signed_in_client
        project = make_project(workspace)
        IntegrationConnection.objects.create(
            project=project,
            provider="ga4",
            status=ConnectionStatus.CONNECTED,
            external_resource_id="properties/1",
            external_resource_label="Something",
        )
        catalogless = providers.get_provider("ga4").__class__(
            key="ga4",
            display_name="Catalogless",
            description="A provider with no resource selection.",
            oauth_scopes=(),
            resources=None,
        )
        monkeypatch.setattr(providers, "CATALOG", (catalogless,))
        monkeypatch.setattr(providers, "_BY_KEY", {"ga4": catalogless})
        monkeypatch.setattr("integrations.services.CATALOG", (catalogless,))

        entry = client.get(f"/api/projects/{project.pk}/integrations").data[0]

        assert entry["status"] == "connected"
        assert entry["supports_resource_selection"] is False

