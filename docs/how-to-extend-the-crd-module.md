# How to extend the CRD module

`based_operators.crd` turns Pydantic models into Kubernetes CustomResourceDefinition
manifests. This page is for people changing the adapter itself — if you only want to
*use* it, read [How to define custom resources](./how-to-define-custom-resources.md) instead.

## One rule to rule them all

Every schema starts from Pydantic's own output:

```python
Model.model_json_schema(mode="validation", by_alias=True)
```

Resist the urge to walk annotations or re-derive constraints, enums, aliases and
defaults by hand — that is exactly how the Pydantic schema and the Kubernetes schema
drift apart. The adapter (`src/based_operators/crd/kubernetes.py`) makes only the
dialect changes Kubernetes requires and **refuses** anything it cannot express
faithfully, so validation is never silently weakened.

!!! tip "Reject, don't guess"
    When a construct has no faithful Kubernetes representation, raise
    `SchemaGenerationError` naming the model and the JSON-schema path. A loud failure
    beats a quietly lossy CRD.

## Kubernetes structural schemas: the rules that bite

| Rule | Detail |
| --- | --- |
| Every node needs a `type` | …unless it sets `x-kubernetes-int-or-string` or `x-kubernetes-preserve-unknown-fields`. |
| `nullable` is core-only | Allowed at the core, forbidden inside `anyOf`/`oneOf`/`allOf`/`not`. |
| No `additionalProperties: false` | It is forbidden, and `additionalProperties` is mutually exclusive with `properties` — use `x-kubernetes-preserve-unknown-fields` for extra-allowing models. |
| `metadata` is special | Only `metadata.name`/`generateName` may be constrained, so emit `{type: object}`. |
| Banned keywords | `$ref`, `uniqueItems: true`, `discriminator`, `deprecated`. |

## The generics trap (status-less resources)

`class Widget(CustomResource[Spec, None])` must end up with no `status` field, schema
or subresource. The subtle part: deleting `status` inside `__pydantic_init_subclass__`
on the *parametrized* class is not enough — a concrete subclass re-collects the
inherited annotation from the generic base and puts `status` back.

The fix is to find the resolved status argument by scanning `cls.__mro__` for a
`__pydantic_generic_metadata__["args"]` of length 2, then delete the field and call
`model_rebuild(force=True)`. Note that the stored argument is `None` (the singleton)
for `[Spec, None]` and `NoneType` for `[Spec, NoneType]` — check both.

## Verifying a change

```shell
uv run pytest tests/src/test_kubernetes.py tests/src/test_resource.py
```

For a real API server — it registers representative CRDs, builds the operator
image and deploys it, checking establishment, defaulting, pruning, schema
rejection, the status subresource and reconciliation of a live object:

```shell
BASED_OPERATORS_K8S_INTEGRATION=1 uv run pytest tests/test_integration.py
```

The integration suite pins Kubernetes 1.31.
