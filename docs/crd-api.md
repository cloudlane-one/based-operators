# Custom resources and CRD generation

`based_operators.crd` provides a Pydantic-first base class for typed custom-resource
models. It derives CRD manifests from Pydantic's own JSON Schema output, adapting it
to the Kubernetes structural OpenAPI v3 dialect and refusing anything that cannot be
represented faithfully.

## Quickstart

!!! note "Optional dependency"
    The examples below render YAML with `crd_yaml()`, which needs the `yaml` extra
    (`pip install "based-operators[yaml]"`). `crd_manifest()` returns the same
    manifest as a dictionary and has no extra dependencies.

Define a `spec` model and an optional `status` model, then subclass
[`CustomResource`][based_operators.crd.CustomResource] with a
[`ResourceConfig`][based_operators.crd.ResourceConfig]:

```python exec="on" session="crd-api" source="block"
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

from based_operators.crd import CustomResource, ResourceConfig, Scope, crd_manifest, crd_yaml


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

A resource without a status is declared by passing `None` (or omitting the second
type parameter). It never acquires a status field, status schema or status
subresource:

```python exec="on" session="crd-api" source="block"
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

## Validating and serializing resources

Use `model_validate` for incoming bodies and `model_dump(mode="json", by_alias=True)`
for outgoing bodies. `apiVersion` and `kind` are derived from the resource
configuration; conflicting input is rejected. Omitted fields stay omitted
(`exclude_unset=True`) while explicit nulls are preserved:

```python exec="on" session="crd-api" source="block"
widget = Widget.model_validate({"spec": {"message": "hello"}})
print(widget.apiVersion, widget.kind)
print(widget.model_dump(mode="json", by_alias=True, exclude_unset=True))

explicit = Widget.model_validate({"spec": {"message": "hello"}, "status": None})
print(explicit.model_dump(mode="json", by_alias=True, exclude_unset=True))
```

## Public API

| Symbol | Purpose |
| --- | --- |
| [`CustomResource`][based_operators.crd.CustomResource] | Typed base class for custom-resource models. |
| [`ResourceConfig`][based_operators.crd.ResourceConfig] | Immutable group/version/kind/plural/scope configuration. |
| [`Scope`][based_operators.crd.Scope] | `Namespaced` or `Cluster`. |
| [`ObjectMeta`][based_operators.crd.ObjectMeta] | Common metadata model that preserves unknown server fields. |
| [`source_json_schema`][based_operators.crd.source_json_schema] | The original Pydantic JSON Schema. |
| [`kubernetes_schema`][based_operators.crd.kubernetes_schema] | The adapted structural OpenAPI v3 schema. |
| [`crd_manifest`][based_operators.crd.crd_manifest] | A JSON-compatible `apiextensions.k8s.io/v1` CRD dictionary. |
| [`crd_yaml`][based_operators.crd.crd_yaml] / [`to_yaml`][based_operators.crd.to_yaml] | Optional plain YAML rendering. |

`model_json_schema()` on a resource returns the source Pydantic schema; the
Kubernetes dialect is produced separately so no information is lost.

## Supported features and limitations

| Construct | Kubernetes representation | Notes |
| --- | --- | --- |
| `str`, `int`, `float`, `bool` | `string`, `integer`, `number`, `boolean` | — |
| Nested models | Inlined object schemas | Local `$ref`/`$defs` are resolved; recursion is rejected. |
| `enum`, `Literal[...]` | `enum` | Single-value `const` becomes a one-element `enum`. |
| `X \| None` | `type` + `nullable: true` | Requiredness is preserved independently of nullability. |
| `list[X]` | `type: array`, `items` | — |
| `dict[str, X]` | `type: object`, `additionalProperties` | String keys only. |
| `int \| str` | `x-kubernetes-int-or-string: true` | With the documented `anyOf` pattern. |
| `typing.Any` | `x-kubernetes-preserve-unknown-fields: true` | Untyped JSON sub-tree. |
| `Field(ge=, le=, gt=, lt=, multiple_of=)` | `minimum`/`maximum` + `exclusive*`, `multipleOf` | Exclusive bounds are converted to the OpenAPI 3.0 form. |
| `Field(min_length=, max_length=, pattern=)` | `minLength`/`maxLength`, `pattern` | — |
| `Field(alias=)` | Wire name is the alias | Validation and serialization aliases must match. |
| `extra="forbid"` | `additionalProperties: false` dropped | Kubernetes prunes unknown fields instead. |
| `extra="allow"` | `x-kubernetes-preserve-unknown-fields: true` | `additionalProperties` is mutually exclusive with `properties`. |
| `default=...` | `default` | `None` defaults are dropped. |

Unsupported constructs raise a
[`SchemaGenerationError`][based_operators.crd.SchemaGenerationError] naming the
model, field and JSON-schema path — nothing is silently weakened:

| Construct | Reason |
| --- | --- |
| Recursive / self-referential models | Kubernetes forbids recursive schemas. |
| Unions other than `X \| None` and `int \| str` | No faithful structural representation. |
| `set` / `uniqueItems: true` | Kubernetes rejects `uniqueItems: true`. |
| Mixed-type `Literal[1, "a"]` | No single `type`. |
| Protected groups (`*.k8s.io`, `*.kubernetes.io`) | Require the `api-approved.kubernetes.io` annotation, which is not emitted. |
| Shape-changing serializers, computed fields, `AliasChoices`, mismatched aliases | Wire names cannot be reconciled. |
Python validators remain Python validators: the generated OpenAPI schema does not
reproduce arbitrary procedural validation, and no CEL is generated.
