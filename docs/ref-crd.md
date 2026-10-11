# Custom resources and CRDs

`based_operators.crd` provides a Pydantic-first base class for typed
custom-resource models. It derives CRD manifests from Pydantic's own JSON Schema
output, adapting it to the Kubernetes structural OpenAPI v3 dialect and refusing
anything that cannot be represented faithfully.

For task-oriented guidance, see
[How to define custom resources](./how-to-define-custom-resources.md).

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
| [`CrdError`][based_operators.crd.CrdError] | Base class of every error this module raises. |
| [`ResourceDefinitionError`][based_operators.crd.ResourceDefinitionError] | The resource class or its configuration is not a valid definition. |
| [`SchemaGenerationError`][based_operators.crd.SchemaGenerationError] | The Pydantic schema cannot be represented in the Kubernetes dialect. |

`model_json_schema()` on a resource returns the source Pydantic schema; the
Kubernetes dialect is produced separately, by `kubernetes_schema()`, so no
information is lost along the way.

## Supported constructs

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

## Unsupported constructs

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