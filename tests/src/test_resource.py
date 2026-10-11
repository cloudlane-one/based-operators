"""Tests for the :class:`based_operators.crd.CustomResource` base class."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
)

from based_operators.crd import (
    CustomResource,
    ResourceConfig,
    ResourceDefinitionError,
    Scope,
    crd_manifest,
)


class WidgetSpec(BaseModel):
    """A small specification model."""

    size: int = 1


class WidgetStatus(BaseModel):
    """A small status model."""

    ready: bool = False


class Widget(CustomResource[WidgetSpec, WidgetStatus]):
    """A namespaced resource with a status."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Widget",
        plural="widgets",
        scope=Scope.NAMESPACED,
    )


class Gizmo(CustomResource[WidgetSpec, None]):
    """A namespaced resource without a status."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Gizmo",
        plural="gizmos",
        scope=Scope.NAMESPACED,
    )


class ClusterGizmo(CustomResource[WidgetSpec, None]):
    """A cluster-scoped resource without a status."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="ClusterGizmo",
        plural="clustergizmos",
        scope=Scope.CLUSTER,
    )


class ImplicitStatusless(CustomResource[WidgetSpec]):
    """A resource relying on the default ``None`` status parameter."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Implicit",
        plural="implicits",
        scope=Scope.NAMESPACED,
    )


def _widget(**body: Any) -> Widget:
    """Validate a widget body from a raw mapping."""
    return Widget.model_validate({"spec": {"size": 1}, **body})


def test_identity_is_derived() -> None:
    """apiVersion and kind are derived from the resource configuration."""
    widget = Widget(spec=WidgetSpec(size=2))

    assert widget.apiVersion == "example.com/v1"
    assert widget.kind == "Widget"
    assert widget.spec.size == 2


def test_matching_identity_is_accepted() -> None:
    """Explicitly supplied matching identity fields are accepted."""
    widget = Widget(apiVersion="example.com/v1", kind="Widget", spec=WidgetSpec())

    assert widget.kind == "Widget"


@pytest.mark.parametrize(
    ("field", "value"),
    [("apiVersion", "other.com/v1"), ("kind", "Other")],
)
def test_conflicting_identity_is_rejected(field: str, value: str) -> None:
    """Conflicting identity input is rejected."""
    with pytest.raises(ValidationError):
        _widget(**{field: value})


def test_cluster_scope_rejects_namespace() -> None:
    """Cluster-scoped resources must not set metadata.namespace."""
    with pytest.raises(ValidationError):
        ClusterGizmo.model_validate({"spec": {"size": 1}, "metadata": {"namespace": "default"}})


def test_namespaced_allows_omitted_namespace() -> None:
    """A namespaced resource can be created without a namespace."""
    widget = Widget(spec=WidgetSpec())

    assert widget.metadata.namespace is None


def test_status_bearing_resource_has_status() -> None:
    """A status-bearing resource exposes the typed status field."""
    assert "status" in Widget.model_fields

    widget = Widget(spec=WidgetSpec(), status=WidgetStatus(ready=True))

    assert widget.status is not None
    assert widget.status.ready is True


def test_statusless_resource_has_no_status() -> None:
    """A resource declared with ``None`` status has no status field."""
    assert "status" not in Gizmo.model_fields
    assert "status" not in ImplicitStatusless.model_fields

    gizmo = Gizmo(spec=WidgetSpec())

    assert not hasattr(gizmo, "status")


def test_statusless_resource_rejects_status_input() -> None:
    """A status-less resource rejects a status payload."""
    with pytest.raises(ValidationError):
        Gizmo.model_validate({"spec": {"size": 1}, "status": {"ready": True}})


def test_server_metadata_is_preserved() -> None:
    """Unknown server-populated metadata keys are preserved."""
    widget = Widget.model_validate(
        {
            "apiVersion": "example.com/v1",
            "kind": "Widget",
            "metadata": {
                "name": "n",
                "uid": "abc",
                "resourceVersion": "5",
                "someServerField": "kept",
            },
            "spec": {"size": 1},
        }
    )

    assert widget.metadata.uid == "abc"
    assert widget.metadata.resource_version == "5"
    assert widget.metadata.model_extra is not None
    assert widget.metadata.model_extra["someServerField"] == "kept"


def test_uid_not_required_for_creation() -> None:
    """Server-generated identifiers are not required for creation input."""
    widget = Widget(spec=WidgetSpec())

    assert widget.metadata.uid is None
    assert widget.metadata.resource_version is None


def test_omitted_vs_explicit_null() -> None:
    """Omitted status is absent, explicit null is preserved."""
    omitted = Widget(spec=WidgetSpec()).model_dump(mode="json", by_alias=True, exclude_unset=True)
    assert "status" not in omitted

    explicit = Widget.model_validate({"spec": {"size": 1}, "status": None}).model_dump(
        mode="json", by_alias=True, exclude_unset=True
    )
    assert "status" in explicit
    assert explicit["status"] is None


def test_json_round_trip() -> None:
    """A resource survives a JSON round trip unchanged."""
    widget = Widget.model_validate(
        {"spec": {"size": 3}, "status": {"ready": True}, "metadata": {"name": "a"}}
    )

    dumped = widget.model_dump(mode="json", by_alias=True)
    restored = Widget.model_validate(dumped)

    assert restored == widget


def test_user_validators_run() -> None:
    """User-defined field validators execute through model_validate."""

    class UpperSpec(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def _upper(cls, value: str) -> str:
            return value.upper()

    class Upper(CustomResource[UpperSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="Upper",
            plural="uppers",
            scope=Scope.NAMESPACED,
        )

    resource = Upper.model_validate({"spec": {"name": "abc"}})

    assert resource.spec.name == "ABC"


def test_missing_resource_config_is_rejected() -> None:
    """A resource without a configuration cannot be generated."""

    class NoConfig(CustomResource[WidgetSpec, None]):
        pass

    with pytest.raises(ResourceDefinitionError):
        crd_manifest(NoConfig)


def test_unresolved_generic_is_rejected() -> None:
    """The bare generic base cannot be turned into a manifest."""
    with pytest.raises(ResourceDefinitionError):
        crd_manifest(CustomResource)  # type: ignore[arg-type]


def test_non_model_spec_is_rejected() -> None:
    """A spec that is not a Pydantic model is rejected."""

    class BadSpec(CustomResource[int, None]):  # type: ignore[arg-type]
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="BadSpec",
            plural="badspecs",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(ResourceDefinitionError):
        crd_manifest(BadSpec)


def test_mismatched_aliases_are_rejected() -> None:
    """Fields with differing validation/serialization aliases are rejected."""

    class AliasSpec(BaseModel):
        model_config = ConfigDict(populate_by_name=True)
        value: int = Field(validation_alias="v", serialization_alias="s")

    class Aliased(CustomResource[AliasSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="Aliased",
            plural="aliaseds",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(ResourceDefinitionError):
        crd_manifest(Aliased)


def test_half_alias_is_rejected() -> None:
    """A field setting only one alias direction is rejected."""

    class HalfAliasSpec(BaseModel):
        value: int = Field(validation_alias="v")

    class HalfAliased(CustomResource[HalfAliasSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="HalfAliased",
            plural="halfaliaseds",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(ResourceDefinitionError):
        crd_manifest(HalfAliased)


def test_custom_serializer_is_rejected() -> None:
    """Shape-changing serializers are rejected."""

    class SerializedSpec(BaseModel):
        value: int

        @field_serializer("value")
        def _serialize(self, value: int) -> str:
            return str(value)

    class Serialized(CustomResource[SerializedSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="Serialized",
            plural="serializeds",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(ResourceDefinitionError):
        crd_manifest(Serialized)
