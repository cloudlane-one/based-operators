"""Adapt Pydantic JSON Schema output to the Kubernetes OpenAPI v3 dialect.

The adapter is deliberately small: it starts from Pydantic's own
``model_json_schema(mode="validation", by_alias=True)`` output and makes only the
dialect changes Kubernetes requires, refusing anything that cannot be represented
faithfully instead of dropping validation semantics.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from based_operators.crd.errors import SchemaGenerationError
from based_operators.crd.resource import CustomResource, validate_resource_definition

# Keywords accepted by apiextensions.k8s.io JSONSchemaProps that pass through
# unchanged.
_PASSTHROUGH_KEYWORDS = frozenset(
    {
        "type",
        "format",
        "title",
        "description",
        "pattern",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "multipleOf",
        "required",
        "nullable",
        "minimum",
        "maximum",
    }
)

# Pydantic keywords with no Kubernetes counterpart; their semantics are either
# re-expressed elsewhere or intentionally dropped.
_DROPPED_KEYWORDS = frozenset(
    {"$schema", "$id", "$comment", "$defs", "deprecated", "discriminator"}
)

_ENVELOPE_FIELDS = ("apiVersion", "kind", "metadata")


def source_json_schema(resource_type: type[CustomResource[Any, Any]]) -> dict[str, Any]:
    """Return the original Pydantic JSON Schema for a resource.

    Args:
        resource_type: The resource class to describe.

    Returns:
        A fresh copy of ``model_json_schema(mode="validation", by_alias=True)``.

    Raises:
        ResourceDefinitionError: If the class is not a valid resource definition.
    """
    validate_resource_definition(resource_type)
    return copy.deepcopy(resource_type.model_json_schema(mode="validation", by_alias=True))


def kubernetes_schema(resource_type: type[CustomResource[Any, Any]]) -> dict[str, Any]:
    """Return the adapted Kubernetes structural OpenAPI v3 schema for a resource.

    Args:
        resource_type: The resource class to describe.

    Returns:
        A new ``openAPIV3Schema``-ready dictionary. Every call returns an
        independent result.

    Raises:
        ResourceDefinitionError: If the class is not a valid resource definition.
        SchemaGenerationError: If the Pydantic schema cannot be represented in the
            Kubernetes dialect.
    """
    validate_resource_definition(resource_type)
    raw = resource_type.model_json_schema(mode="validation", by_alias=True)
    definitions = raw.pop("$defs", {})
    try:
        adapted = _adapt(raw, definitions, path="", stack=())
    except SchemaGenerationError as error:
        raise SchemaGenerationError(f"{resource_type.__name__}: {error}") from error
    return _apply_envelope(adapted)


def _adapt(
    node: dict[str, Any],
    definitions: dict[str, Any],
    path: str,
    stack: tuple[str, ...],
) -> dict[str, Any]:
    """Adapt a single JSON-schema node.

    Args:
        node: The schema node to adapt.
        definitions: The ``$defs`` map used to resolve local references.
        path: A dotted path used in error messages.
        stack: Names of the definitions currently being inlined.

    Returns:
        The adapted node.

    Raises:
        SchemaGenerationError: If the node cannot be represented faithfully.
    """
    node = dict(node)

    if "$ref" in node:
        return _adapt_reference(node, definitions, path, stack)
    if "allOf" in node:
        return _adapt_all_of(node, definitions, path, stack)
    if "anyOf" in node:
        return _adapt_union(node, "anyOf", definitions, path, stack)
    if "oneOf" in node:
        raise SchemaGenerationError(_error(path, "'oneOf' unions cannot be represented faithfully"))
    if "not" in node:
        raise SchemaGenerationError(_error(path, "'not' schemas are not supported"))

    return _adapt_plain(node, definitions, path, stack)


def _adapt_reference(
    node: dict[str, Any],
    definitions: dict[str, Any],
    path: str,
    stack: tuple[str, ...],
) -> dict[str, Any]:
    """Inline a local ``$ref``, merging any sibling keywords.

    Args:
        node: The node holding the ``$ref``.
        definitions: The ``$defs`` map.
        path: A dotted path used in error messages.
        stack: Names of the definitions currently being inlined.

    Returns:
        The adapted referenced schema.

    Raises:
        SchemaGenerationError: On recursive references or unresolved references.
    """
    reference = node["$ref"]
    name = reference.rsplit("/", 1)[-1]
    if name in stack:
        raise SchemaGenerationError(
            _error(path, f"recursive reference to {name!r} is not supported by Kubernetes")
        )
    target = definitions.get(name)
    if target is None:
        raise SchemaGenerationError(_error(path, f"unresolved reference {reference!r}"))
    siblings = {key: value for key, value in node.items() if key != "$ref"}
    return _adapt({**target, **siblings}, definitions, path, (*stack, name))


def _adapt_all_of(
    node: dict[str, Any],
    definitions: dict[str, Any],
    path: str,
    stack: tuple[str, ...],
) -> dict[str, Any]:
    """Adapt a single-reference ``allOf`` wrapper.

    Args:
        node: The node holding the ``allOf``.
        definitions: The ``$defs`` map.
        path: A dotted path used in error messages.
        stack: Names of the definitions currently being inlined.

    Returns:
        The adapted referenced schema.

    Raises:
        SchemaGenerationError: If the ``allOf`` combines more than a bare reference.
    """
    branches = node["allOf"]
    siblings = {key: value for key, value in node.items() if key != "allOf"}
    if len(branches) == 1 and set(branches[0]) == {"$ref"}:
        return _adapt({**branches[0], **siblings}, definitions, path, stack)
    raise SchemaGenerationError(
        _error(path, "'allOf' combinations cannot be represented faithfully")
    )


def _adapt_union(
    node: dict[str, Any],
    key: str,
    definitions: dict[str, Any],
    path: str,
    stack: tuple[str, ...],
) -> dict[str, Any]:
    """Adapt an ``anyOf`` union that is either nullable or int-or-string.

    Args:
        node: The node holding the union.
        key: The union keyword (``anyOf``).
        definitions: The ``$defs`` map.
        path: A dotted path used in error messages.
        stack: Names of the definitions currently being inlined.

    Returns:
        The adapted schema.

    Raises:
        SchemaGenerationError: For any other union shape.
    """
    branches = node[key]
    siblings = {field: value for field, value in node.items() if field != key}

    null_branches = [branch for branch in branches if _is_null_branch(branch)]
    non_null = [branch for branch in branches if not _is_null_branch(branch)]

    if len(null_branches) == 1 and len(non_null) == 1:
        merged = {**siblings, **non_null[0]}
        result = _adapt(merged, definitions, path, stack)
        result["nullable"] = True
        return result

    if _is_int_or_string(branches):
        result: dict[str, Any] = {
            field: value for field, value in siblings.items() if field != "default"
        }
        if siblings.get("default") is not None:
            result["default"] = siblings["default"]
        result["x-kubernetes-int-or-string"] = True
        result["anyOf"] = [{"type": "integer"}, {"type": "string"}]
        return result

    raise SchemaGenerationError(_error(path, f"'{key}' union cannot be represented faithfully"))


def _adapt_plain(
    node: dict[str, Any],
    definitions: dict[str, Any],
    path: str,
    stack: tuple[str, ...],
) -> dict[str, Any]:
    """Adapt a node without references or logical junctors.

    Args:
        node: The schema node to adapt.
        definitions: The ``$defs`` map.
        path: A dotted path used in error messages.
        stack: Names of the definitions currently being inlined.

    Returns:
        The adapted node.

    Raises:
        SchemaGenerationError: On unsupported keywords or semantics.
    """
    if "const" in node and "enum" in node:
        raise SchemaGenerationError(_error(path, "both 'const' and 'enum' are present"))

    result: dict[str, Any] = {}
    for keyword, value in node.items():
        if keyword == "additionalProperties":
            result.update(_adapt_additional_properties(value, node, definitions, path, stack))
        elif keyword == "properties":
            result["properties"] = {
                name: _adapt(sub, definitions, _join(path, name), stack)
                for name, sub in value.items()
            }
        elif keyword == "items":
            if isinstance(value, list):
                raise SchemaGenerationError(_error(path, "tuple-style 'items' is not supported"))
            result["items"] = _adapt(value, definitions, f"{path}[]", stack)
        else:
            _adapt_scalar(result, keyword, value, path)

    if "enum" in result and "type" not in result:
        raise SchemaGenerationError(
            _error(
                path,
                "enum without a single 'type' (e.g. a mixed-type Literal) is not supported",
            )
        )
    if _needs_preserve_unknown(result):
        result["x-kubernetes-preserve-unknown-fields"] = True
    return result


def _adapt_scalar(result: dict[str, Any], keyword: str, value: Any, path: str) -> None:
    """Adapt a scalar (non-structural) JSON-schema keyword.

    Args:
        result: The node being built, mutated in place.
        keyword: The source keyword.
        value: The source value.
        path: A dotted path used in error messages.

    Raises:
        SchemaGenerationError: On unsupported keywords or semantics.
    """
    if keyword in _DROPPED_KEYWORDS:
        return
    handler = _SCALAR_HANDLERS.get(keyword)
    if handler is not None:
        handler(result, value, path)
        return
    if keyword.startswith("x-kubernetes-") or keyword in _PASSTHROUGH_KEYWORDS:
        result[keyword] = value
        return
    raise SchemaGenerationError(_error(path, f"unsupported JSON schema keyword {keyword!r}"))


def _handle_const(result: dict[str, Any], value: Any, path: str) -> None:
    """Convert a ``const`` keyword into a single-element enum."""
    result["enum"] = [value]


def _handle_enum(result: dict[str, Any], value: Any, path: str) -> None:
    """Copy a homogeneous ``enum`` keyword."""
    result["enum"] = list(value)


def _handle_default(result: dict[str, Any], value: Any, path: str) -> None:
    """Copy a non-null default value."""
    if value is not None:
        result["default"] = value


def _handle_exclusive_minimum(result: dict[str, Any], value: Any, path: str) -> None:
    """Convert a JSON Schema exclusive lower bound."""
    _adapt_exclusive(result, "minimum", "exclusiveMinimum", value, path)


def _handle_exclusive_maximum(result: dict[str, Any], value: Any, path: str) -> None:
    """Convert a JSON Schema exclusive upper bound."""
    _adapt_exclusive(result, "maximum", "exclusiveMaximum", value, path)


def _handle_unique_items(result: dict[str, Any], value: Any, path: str) -> None:
    """Reject ``uniqueItems: true`` which Kubernetes does not support."""
    if value:
        raise SchemaGenerationError(
            _error(path, "'uniqueItems: true' (e.g. a Python set) is not supported")
        )


def _handle_examples(result: dict[str, Any], value: Any, path: str) -> None:
    """Map the first JSON Schema example onto the OpenAPI ``example`` field."""
    if value:
        result["example"] = value[0]


_SCALAR_HANDLERS: dict[str, Callable[[dict[str, Any], Any, str], None]] = {
    "const": _handle_const,
    "enum": _handle_enum,
    "default": _handle_default,
    "exclusiveMinimum": _handle_exclusive_minimum,
    "exclusiveMaximum": _handle_exclusive_maximum,
    "uniqueItems": _handle_unique_items,
    "examples": _handle_examples,
}


def _adapt_exclusive(
    result: dict[str, Any],
    bound: str,
    exclusive: str,
    value: Any,
    path: str,
) -> None:
    """Rewrite a JSON Schema 2020-12 exclusive bound into the OpenAPI 3.0 form.

    Args:
        result: The node being built, mutated in place.
        bound: ``minimum`` or ``maximum``.
        exclusive: ``exclusiveMinimum`` or ``exclusiveMaximum``.
        value: The bound value from the source schema.
        path: A dotted path used in error messages.

    Raises:
        SchemaGenerationError: If the value is neither a bool nor a number.
    """
    if isinstance(value, bool):
        result[exclusive] = value
    elif isinstance(value, (int, float)):
        result[bound] = value
        result[exclusive] = True
    else:
        raise SchemaGenerationError(_error(path, f"invalid {exclusive} value {value!r}"))


def _adapt_additional_properties(
    value: Any,
    node: dict[str, Any],
    definitions: dict[str, Any],
    path: str,
    stack: tuple[str, ...],
) -> dict[str, Any]:
    """Translate ``additionalProperties`` into Kubernetes-safe keywords.

    Args:
        value: The ``additionalProperties`` value from the source schema.
        node: The enclosing node, used to detect ``properties`` co-presence.
        definitions: The ``$defs`` map.
        path: A dotted path used in error messages.
        stack: Names of the definitions currently being inlined.

    Returns:
        The keywords to merge into the adapted node.

    Raises:
        SchemaGenerationError: If the value is not a bool or a schema object.
    """
    if value is False:
        # Kubernetes forbids additionalProperties:false; pruning is the default.
        return {}
    if value is True:
        if "properties" in node:
            # additionalProperties is mutually exclusive with properties, so
            # unknown fields are preserved with the Kubernetes extension instead.
            return {"x-kubernetes-preserve-unknown-fields": True}
        return {"additionalProperties": True}
    if isinstance(value, dict):
        return {"additionalProperties": _adapt(value, definitions, f"{path}.*", stack)}
    raise SchemaGenerationError(_error(path, f"unsupported additionalProperties {value!r}"))


def _apply_envelope(schema: dict[str, Any]) -> dict[str, Any]:
    """Apply the resource-envelope special cases.

    ``apiVersion``, ``kind`` and ``metadata`` are only treated specially at the
    resource envelope. ``metadata`` is reduced to a bare object because a
    structural schema may only restrict ``metadata.name`` and
    ``metadata.generateName``.

    Args:
        schema: The fully adapted resource schema.

    Returns:
        The schema with envelope fields normalised.
    """
    schema = copy.deepcopy(schema)
    properties = schema.setdefault("properties", {})
    properties["apiVersion"] = {"type": "string"}
    properties["kind"] = {"type": "string"}
    properties["metadata"] = {"type": "object"}
    schema["type"] = "object"
    schema.pop("additionalProperties", None)

    required = schema.get("required")
    if required is not None:
        schema["required"] = [name for name in required if name not in _ENVELOPE_FIELDS]
    return schema


def _is_null_branch(branch: Any) -> bool:
    """Return whether a union branch represents JSON ``null``.

    Args:
        branch: A candidate union branch.

    Returns:
        ``True`` if the branch only accepts ``null``.
    """
    return isinstance(branch, dict) and branch.get("type") == "null"


def _is_int_or_string(branches: list[Any]) -> bool:
    """Return whether a union is exactly ``int | str``.

    Args:
        branches: The union branches.

    Returns:
        ``True`` if the branches are exactly ``{"type": "integer"}`` and
        ``{"type": "string"}``.
    """
    if len(branches) != 2:
        return False
    if not all(isinstance(branch, dict) and set(branch) == {"type"} for branch in branches):
        return False
    return sorted(branch["type"] for branch in branches) == ["integer", "string"]


def _needs_preserve_unknown(result: dict[str, Any]) -> bool:
    """Return whether a typed node is missing and must preserve arbitrary JSON.

    Args:
        result: The partially adapted node.

    Returns:
        ``True`` when the node has no type and no structural keyword, which is how
        Pydantic represents :data:`typing.Any`.
    """
    structural = {
        "type",
        "properties",
        "items",
        "additionalProperties",
        "enum",
        "x-kubernetes-int-or-string",
        "x-kubernetes-preserve-unknown-fields",
    }
    return not structural.intersection(result)


def _join(path: str, name: str) -> str:
    """Join a dotted schema path with a field name.

    Args:
        path: The current path.
        name: The field name.

    Returns:
        The extended path.
    """
    return f"{path}.{name}" if path else name


def _error(path: str, message: str) -> str:
    """Format a schema error message with its path.

    Args:
        path: The dotted schema path.
        message: The error description.

    Returns:
        The formatted message.
    """
    return f"at {path or '<root>'}: {message}"
