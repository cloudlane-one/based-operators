# How to define custom resources

This guide turns Pydantic models into a typed Kubernetes custom resource and a
ready-to-apply CustomResourceDefinition. For the full symbol list, the
supported-construct table and the limitations, see the
[custom resources reference](./ref-crd.md).

## Define the spec and the status

A resource is a subclass of
[`CustomResource`][based_operators.crd.CustomResource] parameterized with a
`spec` model and, optionally, a `status` model. Pass a
[`ResourceConfig`][based_operators.crd.ResourceConfig] as the `resource_config`
class variable to fix the group, version, kind, plural and scope:

```python exec="on" session="how-to-crd" source="block"
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from based_operators.crd import CustomResource, ResourceConfig, Scope, crd_yaml


class Phase(StrEnum):
    PENDING = "Pending"
    READY = "Ready"


class WidgetSpec(BaseModel):
    replicas: Annotated[int, Field(ge=0, le=10)] = 1
    message: str


class WidgetStatus(BaseModel):
    phase: Phase = Phase.PENDING


class Widget(CustomResource[WidgetSpec, WidgetStatus]):
    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Widget",
        plural="widgets",
        scope=Scope.NAMESPACED,
        short_names=("wd",),
    )


print(crd_yaml(Widget))
```

!!! note "Optional dependency"
    `crd_yaml()` needs the `yaml` extra (`pip install "based-operators[yaml]"`).
    `crd_manifest()` returns the same manifest as a dictionary and has no extra
    dependencies.

The generated CRD is single-version, served and stored, and carries a `status`
subresource whenever the resource has a status model. Nested models, enums,
constraints and aliases are all carried over from Pydantic's own schema; see
[supported constructs](./ref-crd.md#supported-constructs) for the full mapping.

## Declare a status-less or cluster-scoped resource

Pass `None` as the second type parameter (or omit it) to drop the `status` field,
schema and subresource entirely. Cluster-scoped resources set
[`Scope.CLUSTER`][based_operators.crd.Scope], and may not set
`metadata.namespace`:

```python exec="on" session="how-to-crd" source="block"
from based_operators.crd import crd_manifest


class Gizmo(CustomResource[WidgetSpec, None]):
    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Gizmo",
        plural="gizmos",
        scope=Scope.CLUSTER,
    )


assert "status" not in Gizmo.model_fields
assert "subresources" not in crd_manifest(Gizmo)["spec"]["versions"][0]
print("Gizmo is status-less and cluster-scoped")
```

## Validate incoming and serialize outgoing bodies

Build resources from raw API responses with `model_validate`, and send them back
with `model_dump(mode="json", by_alias=True)`. `apiVersion` and `kind` are
derived from the configuration and conflicting input is rejected. Omitted fields
stay omitted (`exclude_unset=True`) while explicit nulls are preserved:

```python exec="on" session="how-to-crd" source="block"
widget = Widget.model_validate({"spec": {"message": "hello"}})
print(widget.apiVersion, widget.kind)
print(widget.model_dump(mode="json", by_alias=True, exclude_unset=True))

explicit = Widget.model_validate({"spec": {"message": "hello"}, "status": None})
print(explicit.model_dump(mode="json", by_alias=True, exclude_unset=True))
```

`metadata` is tolerant of unknown keys, so server-populated fields such as `uid`
and `resourceVersion` survive a round trip without being declared.

## Next steps

- [How to write handlers](./how-to-write-handlers.md) — reconcile the resource you
  just defined through its `.on` namespace.
- [Custom resources and CRDs](./ref-crd.md) — the full API, the schema mapping and
  the constructs that are rejected.