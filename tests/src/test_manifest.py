"""Tests for CRD manifest generation."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from based_operators.crd import (
    CustomResource,
    CrdError,
    ResourceConfig,
    Scope,
    crd_manifest,
    crd_yaml,
    to_yaml,
)


class ThingSpec(BaseModel):
    """A minimal specification."""

    size: int = 1


class ThingStatus(BaseModel):
    """A minimal status."""

    ready: bool = False


class Thing(CustomResource[ThingSpec, ThingStatus]):
    """A namespaced resource with a status."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Thing",
        plural="things",
        scope=Scope.NAMESPACED,
        singular="thing",
        short_names=("th",),
    )


class ClusterThing(CustomResource[ThingSpec, None]):
    """A cluster-scoped resource without a status."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1alpha1",
        kind="ClusterThing",
        plural="clusterthings",
        scope=Scope.CLUSTER,
    )


def test_manifest_envelope() -> None:
    """The manifest carries the CRD envelope and naming."""
    manifest = crd_manifest(Thing)

    assert manifest["apiVersion"] == "apiextensions.k8s.io/v1"
    assert manifest["kind"] == "CustomResourceDefinition"
    assert manifest["metadata"]["name"] == "things.example.com"


def test_manifest_spec_fields() -> None:
    """Group, scope, names and a single served/storage version are set."""
    spec = crd_manifest(Thing)["spec"]

    assert spec["group"] == "example.com"
    assert spec["scope"] == "Namespaced"
    assert spec["names"] == {
        "plural": "things",
        "kind": "Thing",
        "listKind": "ThingList",
        "singular": "thing",
        "shortNames": ["th"],
    }
    assert len(spec["versions"]) == 1
    version = spec["versions"][0]
    assert version["name"] == "v1"
    assert version["served"] is True
    assert version["storage"] is True
    assert version["schema"]["openAPIV3Schema"]["type"] == "object"


def test_status_subresource_present() -> None:
    """A status-bearing resource gets a status subresource and schema."""
    version = crd_manifest(Thing)["spec"]["versions"][0]

    assert version["subresources"] == {"status": {}}
    assert "status" in version["schema"]["openAPIV3Schema"]["properties"]


def test_status_subresource_absent() -> None:
    """A status-less resource gets neither subresource nor status schema."""
    manifest = crd_manifest(ClusterThing)
    version = manifest["spec"]["versions"][0]

    assert "subresources" not in version
    assert "status" not in version["schema"]["openAPIV3Schema"]["properties"]
    assert manifest["spec"]["scope"] == "Cluster"


def test_manifests_are_independent() -> None:
    """Each call returns an independent manifest."""
    first = crd_manifest(Thing)
    first["spec"]["group"] = "mutated"

    second = crd_manifest(Thing)

    assert second["spec"]["group"] == "example.com"


def test_yaml_rendering() -> None:
    """The manifest renders to plain YAML deterministically."""
    text = crd_yaml(Thing)

    assert "apiVersion: apiextensions.k8s.io/v1" in text
    assert "name: things.example.com" in text
    assert text == crd_yaml(Thing)


def test_yaml_round_trips() -> None:
    """Rendered YAML parses back to the manifest."""
    import yaml

    assert yaml.safe_load(crd_yaml(Thing)) == crd_manifest(Thing)


def test_to_yaml_without_pyyaml(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clear error is raised when the optional dependency is missing."""
    import sys

    monkeypatch.setitem(sys.modules, "yaml", None)

    with pytest.raises(CrdError):
        to_yaml({"a": 1})
