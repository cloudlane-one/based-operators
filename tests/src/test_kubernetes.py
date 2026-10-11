"""Tests for the Pydantic-to-Kubernetes schema adapter."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from based_operators.crd import (
    CustomResource,
    ResourceConfig,
    SchemaGenerationError,
    Scope,
    kubernetes_schema,
    source_json_schema,
)


class Color(StrEnum):
    """A homogeneous string enum."""

    RED = "red"
    BLUE = "blue"


class Nested(BaseModel):
    """A nested object reused by more than one field."""

    x: int


class AllowModel(BaseModel):
    """A model that allows unknown fields."""

    model_config = ConfigDict(extra="allow")
    a: int


class ForbidModel(BaseModel):
    """A model that forbids unknown fields."""

    model_config = ConfigDict(extra="forbid")
    a: int


class FullSpec(BaseModel):
    """A specification exercising the supported feature surface."""

    constrained: Annotated[int, Field(ge=0, le=10)]
    patterned: Annotated[str, Field(pattern=r"^[a-z]+$", min_length=1, max_length=5)]
    ratio: Annotated[float, Field(gt=0, lt=1, multiple_of=0.5)] = 0.5
    optional: str | None = None
    required_nullable: str | None
    color: Color
    literal: Literal["only"]
    nested: Nested
    nested_again: Nested
    tags: list[str] = Field(default_factory=list)
    settings: dict[str, int] = Field(default_factory=dict)
    anything: Any = None
    kind: str = "payload-kind"
    apiVersion: str = "payload-version"  # noqa: N815 - Kubernetes wire name
    described: Annotated[int, Field(description="a described field")] = 3
    aliased: str = Field(default="v", alias="aliasedName")
    allow: AllowModel
    forbid: ForbidModel


class Full(CustomResource[FullSpec, None]):
    """A resource exposing the full specification."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Full",
        plural="fulls",
        scope=Scope.NAMESPACED,
    )


class ForwardSpec(BaseModel):
    """A model using a forward reference to a later module-level class."""

    child: ForwardChild


class ForwardChild(BaseModel):
    """A model referenced before its definition."""

    y: int


class Forward(CustomResource[ForwardSpec, None]):
    """A resource whose spec uses a forward reference."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Forward",
        plural="forwards",
        scope=Scope.NAMESPACED,
    )


def _spec_schema() -> dict[str, Any]:
    """Return the adapted ``spec`` subschema."""
    return kubernetes_schema(Full)["properties"]["spec"]


class CoverageSpec(BaseModel):
    """A specification covering dialect-specific keyword handling."""

    described_ref: Annotated[Nested, Field(description="a described nested")]
    with_examples: Annotated[int, Field(examples=[1, 2])] = 1
    with_extra: Annotated[int, Field(json_schema_extra={"x-kubernetes-foo": True})] = 1
    deprecated_field: Annotated[int, Field(deprecated=True)] = 1


class Coverage(CustomResource[CoverageSpec, None]):
    """A resource covering dialect-specific keyword handling."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Coverage",
        plural="coverages",
        scope=Scope.NAMESPACED,
    )


class TupleSpec(BaseModel):
    """A specification using a tuple, which has no structural representation."""

    pair: tuple[int, str] = (1, "a")


class WithTuple(CustomResource[TupleSpec, None]):
    """A resource whose spec uses a tuple."""

    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="WithTuple",
        plural="withtuples",
        scope=Scope.NAMESPACED,
    )


def test_source_schema_keeps_definitions() -> None:
    """The original Pydantic schema keeps ``$defs`` and ``$ref``."""
    source = source_json_schema(Full)

    assert "$defs" in source
    assert source["properties"]["spec"]["$ref"] == "#/$defs/FullSpec"


def test_kubernetes_schema_is_fully_inlined() -> None:
    """The adapted schema has no ``$ref`` or ``$defs`` anywhere."""
    schema = kubernetes_schema(Full)

    assert "$defs" not in schema

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            assert "$ref" not in node
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(schema)


def test_constraints_are_preserved() -> None:
    """Numeric, string and multiple-of constraints survive adaptation."""
    spec = _spec_schema()

    assert spec["properties"]["constrained"]["minimum"] == 0
    assert spec["properties"]["constrained"]["maximum"] == 10
    patterned = spec["properties"]["patterned"]
    assert patterned["pattern"] == "^[a-z]+$"
    assert patterned["minLength"] == 1
    assert patterned["maxLength"] == 5


def test_exclusive_bounds_are_converted() -> None:
    """JSON Schema exclusive bounds become OpenAPI minimum+exclusive flags."""
    ratio = _spec_schema()["properties"]["ratio"]

    assert ratio["minimum"] == 0
    assert ratio["exclusiveMinimum"] is True
    assert ratio["maximum"] == 1
    assert ratio["exclusiveMaximum"] is True
    assert ratio["multipleOf"] == 0.5


def test_nullable_omittable_field() -> None:
    """An optional field is nullable but not required, and drops null default."""
    optional = _spec_schema()["properties"]["optional"]

    assert optional == {"type": "string", "title": "Optional", "nullable": True}
    assert "optional" not in _spec_schema().get("required", [])


def test_required_nullable_field() -> None:
    """A required nullable field keeps requiredness and nullability."""
    spec = _spec_schema()

    assert spec["properties"]["required_nullable"]["nullable"] is True
    assert "required_nullable" in spec["required"]


def test_const_becomes_enum() -> None:
    """A single-value literal becomes a one-element enum."""
    literal = _spec_schema()["properties"]["literal"]

    assert literal["enum"] == ["only"]
    assert literal["type"] == "string"


def test_enum_is_preserved() -> None:
    """A homogeneous enum is preserved."""
    color = _spec_schema()["properties"]["color"]

    assert color["enum"] == ["red", "blue"]
    assert color["type"] == "string"


def test_nested_models_are_inlined_and_reused() -> None:
    """A reused nested model is inlined for every reference."""
    spec = _spec_schema()

    nested = spec["properties"]["nested"]
    assert nested["type"] == "object"
    assert nested["properties"]["x"] == {"type": "integer", "title": "X"}
    assert nested["required"] == ["x"]
    assert spec["properties"]["nested_again"] == nested


def test_maps_and_lists() -> None:
    """String-keyed maps and lists are adapted."""
    spec = _spec_schema()

    assert spec["properties"]["tags"]["type"] == "array"
    assert spec["properties"]["tags"]["items"] == {"type": "string"}
    assert spec["properties"]["settings"]["type"] == "object"
    assert spec["properties"]["settings"]["additionalProperties"] == {"type": "integer"}


def test_any_preserves_unknown_fields() -> None:
    """``typing.Any`` becomes ``x-kubernetes-preserve-unknown-fields``."""
    anything = _spec_schema()["properties"]["anything"]

    assert anything["x-kubernetes-preserve-unknown-fields"] is True
    assert "type" not in anything


def test_additional_properties_false_is_dropped() -> None:
    """``additionalProperties: false`` is never emitted."""
    spec = _spec_schema()

    assert "additionalProperties" not in spec["properties"]["forbid"]
    assert spec["properties"]["forbid"]["properties"]["a"] == {"type": "integer", "title": "A"}


def test_extra_allow_uses_preserve_unknown() -> None:
    """A model allowing extras preserves unknown fields via the extension."""
    allow = _spec_schema()["properties"]["allow"]

    assert allow["x-kubernetes-preserve-unknown-fields"] is True
    assert "additionalProperties" not in allow


def test_descriptions_and_defaults() -> None:
    """Descriptions and supported defaults are preserved."""
    spec = _spec_schema()

    assert spec["properties"]["described"]["description"] == "a described field"
    assert spec["properties"]["described"]["default"] == 3


def test_alias_is_used_as_wire_name() -> None:
    """Serialization uses the alias as the wire name."""
    spec = _spec_schema()

    assert "aliasedName" in spec["properties"]
    assert "aliased" not in spec["properties"]


def test_payload_fields_named_kind_and_apiversion() -> None:
    """Fields named kind/apiVersion inside spec are ordinary fields."""
    spec = _spec_schema()

    assert spec["properties"]["kind"]["type"] == "string"
    assert spec["properties"]["apiVersion"]["type"] == "string"
    assert "enum" not in spec["properties"]["kind"]


def test_envelope_special_casing() -> None:
    """Only the envelope apiVersion/kind/metadata are special-cased."""
    schema = kubernetes_schema(Full)

    assert schema["properties"]["apiVersion"] == {"type": "string"}
    assert schema["properties"]["kind"] == {"type": "string"}
    assert schema["properties"]["metadata"] == {"type": "object"}
    assert schema["required"] == ["spec"]


def test_results_are_independent() -> None:
    """Every call returns a fresh, independent structure."""
    first = kubernetes_schema(Full)
    first["properties"]["spec"]["properties"]["nested"]["title"] = "mutated"

    second = kubernetes_schema(Full)

    assert second["properties"]["spec"]["properties"]["nested"]["title"] == "Nested"


def test_int_or_string() -> None:
    """``int | str`` uses the Kubernetes-specific representation."""

    class IosSpec(BaseModel):
        value: int | str = 5

    class Ios(CustomResource[IosSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="Ios",
            plural="ioses",
            scope=Scope.NAMESPACED,
        )

    value = kubernetes_schema(Ios)["properties"]["spec"]["properties"]["value"]

    assert value["x-kubernetes-int-or-string"] is True
    assert value["anyOf"] == [{"type": "integer"}, {"type": "string"}]
    assert value["default"] == 5


def test_int_or_string_siblings_are_adapted() -> None:
    """Siblings of an ``int | str`` union are adapted, not copied verbatim."""

    class IosSiblingSpec(BaseModel):
        value: Annotated[int | str, Field(examples=[1])] = 1

    class IosSibling(CustomResource[IosSiblingSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="IosSibling",
            plural="iossiblings",
            scope=Scope.NAMESPACED,
        )

    field = kubernetes_schema(IosSibling)["properties"]["spec"]["properties"]["value"]

    assert field["example"] == 1
    assert "examples" not in field


def test_int_or_string_rejects_unsupported_siblings() -> None:
    """An unsupported keyword sibling of an ``int | str`` union is rejected."""

    class IosBadSpec(BaseModel):
        value: Annotated[int | str, Field(json_schema_extra={"not": {"type": "integer"}})] = 1

    class IosBad(CustomResource[IosBadSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="IosBad",
            plural="iosbads",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(SchemaGenerationError, match="unsupported JSON schema keyword"):
        kubernetes_schema(IosBad)


def test_recursive_model_is_rejected() -> None:
    """A recursive model graph cannot be represented and is rejected."""

    class Node(BaseModel):
        child: Node | None = None

    class Recursive(CustomResource[Node, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="Recursive",
            plural="recursives",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(SchemaGenerationError):
        kubernetes_schema(Recursive)


def test_unsupported_union_is_rejected() -> None:
    """A union other than int|string or nullable is rejected."""

    class UnionSpec(BaseModel):
        value: int | bool

    class Union(CustomResource[UnionSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="Union",
            plural="unions",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(SchemaGenerationError, match=r"Union.*spec\.value"):
        kubernetes_schema(Union)


def test_set_is_rejected() -> None:
    """A set (uniqueItems) cannot be represented and is rejected."""

    class SetSpec(BaseModel):
        value: set[str] = Field(default_factory=set)

    class WithSet(CustomResource[SetSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="WithSet",
            plural="withsets",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(SchemaGenerationError):
        kubernetes_schema(WithSet)


def test_mixed_literal_is_rejected() -> None:
    """A mixed-type literal has no single type and is rejected."""

    class MixedSpec(BaseModel):
        value: Literal[1, "a"]

    class Mixed(CustomResource[MixedSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="Mixed",
            plural="mixeds",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(SchemaGenerationError):
        kubernetes_schema(Mixed)


def test_forward_reference_resolves() -> None:
    """Forward references are resolved through ``model_rebuild``."""
    child = kubernetes_schema(Forward)["properties"]["spec"]["properties"]["child"]

    assert child["properties"]["y"]["type"] == "integer"


def test_reference_siblings_are_merged() -> None:
    """A ``$ref`` with annotative siblings is inlined and the siblings kept."""
    described = kubernetes_schema(Coverage)["properties"]["spec"]["properties"]["described_ref"]

    assert described["type"] == "object"
    assert described["description"] == "a described nested"
    assert described["properties"]["x"]["type"] == "integer"


def test_conflicting_ref_sibling_is_rejected() -> None:
    """A ``$ref`` sibling colliding with a validation keyword is rejected."""

    class RefConflictSpec(BaseModel):
        nested: Annotated[Nested, Field(json_schema_extra={"required": []})]

    class RefConflict(CustomResource[RefConflictSpec, None]):
        resource_config = ResourceConfig(
            group="example.com",
            version="v1",
            kind="RefConflict",
            plural="refconflicts",
            scope=Scope.NAMESPACED,
        )

    with pytest.raises(SchemaGenerationError, match="conflict"):
        kubernetes_schema(RefConflict)


def test_examples_become_example() -> None:
    """The first JSON Schema example becomes the OpenAPI example."""
    field = kubernetes_schema(Coverage)["properties"]["spec"]["properties"]["with_examples"]

    assert field["example"] == 1


def test_x_kubernetes_extra_is_passed_through() -> None:
    """``x-kubernetes-*`` schema extras are preserved."""
    field = kubernetes_schema(Coverage)["properties"]["spec"]["properties"]["with_extra"]

    assert field["x-kubernetes-foo"] is True


def test_deprecated_is_dropped() -> None:
    """The ``deprecated`` keyword is dropped."""
    field = kubernetes_schema(Coverage)["properties"]["spec"]["properties"]["deprecated_field"]

    assert "deprecated" not in field


def test_tuple_is_rejected() -> None:
    """A tuple (``prefixItems``) has no structural representation and is rejected."""
    with pytest.raises(SchemaGenerationError, match="prefixItems"):
        kubernetes_schema(WithTuple)


def _extra_resource(name: str, field: Any) -> type[CustomResource[Any, Any]]:
    """Build a resource whose spec injects a raw keyword via ``json_schema_extra``."""

    class Spec(BaseModel):
        value: Any = field

    return type(
        name,
        (CustomResource[Spec, None],),
        {
            "resource_config": ResourceConfig(
                group="example.com",
                version="v1",
                kind=name,
                plural=f"{name.lower()}s",
                scope=Scope.NAMESPACED,
            )
        },
    )


def test_one_of_is_rejected() -> None:
    """A ``oneOf`` keyword is rejected."""
    resource = _extra_resource("OneOf", Field(json_schema_extra={"oneOf": [{"type": "string"}]}))

    with pytest.raises(SchemaGenerationError, match="oneOf"):
        kubernetes_schema(resource)


def test_not_is_rejected() -> None:
    """A ``not`` keyword is rejected."""
    resource = _extra_resource("NotSchema", Field(json_schema_extra={"not": {"type": "string"}}))

    with pytest.raises(SchemaGenerationError, match="'not'"):
        kubernetes_schema(resource)


def test_multi_branch_all_of_is_rejected() -> None:
    """An ``allOf`` combining more than a bare reference is rejected."""
    resource = _extra_resource(
        "MultiAllOf",
        Field(json_schema_extra={"allOf": [{"type": "string"}, {"minLength": 1}]}),
    )

    with pytest.raises(SchemaGenerationError, match="allOf"):
        kubernetes_schema(resource)


def test_unresolved_reference_is_rejected() -> None:
    """A ``$ref`` to a missing definition is rejected."""
    from based_operators.crd import kubernetes as kubernetes_module

    with pytest.raises(SchemaGenerationError, match="unresolved reference"):
        kubernetes_module._adapt({"$ref": "#/$defs/DoesNotExist"}, {}, "spec.value", ())


def test_const_and_enum_conflict_is_rejected() -> None:
    """A node carrying both ``const`` and ``enum`` is rejected."""
    resource = _extra_resource("ConstEnum", Field(json_schema_extra={"const": 1, "enum": [1, 2]}))

    with pytest.raises(SchemaGenerationError, match="'const' and 'enum'"):
        kubernetes_schema(resource)


def test_list_style_items_is_rejected() -> None:
    """A tuple-style ``items`` list is rejected."""
    resource = _extra_resource(
        "ListItems", Field(json_schema_extra={"items": [{"type": "string"}]})
    )

    with pytest.raises(SchemaGenerationError, match="tuple-style"):
        kubernetes_schema(resource)


def test_invalid_additional_properties_is_rejected() -> None:
    """A non-bool, non-object ``additionalProperties`` is rejected."""
    resource = _extra_resource(
        "BadAdditional", Field(json_schema_extra={"additionalProperties": 5})
    )

    with pytest.raises(SchemaGenerationError, match="additionalProperties"):
        kubernetes_schema(resource)


def test_invalid_exclusive_bound_is_rejected() -> None:
    """A non-numeric exclusive bound is rejected."""
    resource = _extra_resource("BadExclusive", Field(json_schema_extra={"exclusiveMinimum": "x"}))

    with pytest.raises(SchemaGenerationError, match="exclusiveMinimum"):
        kubernetes_schema(resource)


def test_boolean_exclusive_bound_is_preserved() -> None:
    """An already-boolean exclusive bound passes through."""
    resource = _extra_resource(
        "BoolExclusive", Field(json_schema_extra={"minimum": 0, "exclusiveMinimum": True})
    )

    field = kubernetes_schema(resource)["properties"]["spec"]["properties"]["value"]

    assert field["minimum"] == 0
    assert field["exclusiveMinimum"] is True


def test_any_of_with_typed_branches_is_rejected() -> None:
    """An ``anyOf`` whose branches carry types is rejected."""
    resource = _extra_resource(
        "TypedAnyOf",
        Field(
            json_schema_extra={"anyOf": [{"type": "string", "maxLength": 1}, {"type": "integer"}]}
        ),
    )

    with pytest.raises(SchemaGenerationError, match="anyOf"):
        kubernetes_schema(resource)
