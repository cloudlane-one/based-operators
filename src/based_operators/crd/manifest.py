"""Generate CustomResourceDefinition manifests from resource classes."""

from __future__ import annotations

from typing import Any

from based_operators.crd.errors import CrdError
from based_operators.crd.kubernetes import kubernetes_schema
from based_operators.crd.resource import CustomResource, validate_resource_definition

_CRD_API_VERSION = "apiextensions.k8s.io/v1"


def crd_manifest(resource_type: type[CustomResource[Any, Any]]) -> dict[str, Any]:
    """Build a complete ``apiextensions.k8s.io/v1`` CRD manifest for a resource.

    Args:
        resource_type: The resource class to describe.

    Returns:
        A new, JSON-compatible dictionary for a single-version CRD. Every call
        returns an independent result.

    Raises:
        ResourceDefinitionError: If the class is not a valid resource definition.
        SchemaGenerationError: If the resource schema cannot be represented.
    """
    config = validate_resource_definition(resource_type)
    schema = kubernetes_schema(resource_type)

    version: dict[str, Any] = {
        "name": config.version,
        "served": True,
        "storage": True,
        "schema": {"openAPIV3Schema": schema},
    }
    if "status" in resource_type.model_fields:
        version["subresources"] = {"status": {}}

    names: dict[str, Any] = {
        "plural": config.plural,
        "kind": config.kind,
        "listKind": f"{config.kind}List",
    }
    if config.singular is not None:
        names["singular"] = config.singular
    if config.short_names:
        names["shortNames"] = list(config.short_names)

    return {
        "apiVersion": _CRD_API_VERSION,
        "kind": "CustomResourceDefinition",
        "metadata": {"name": config.crd_name},
        "spec": {
            "group": config.group,
            "scope": config.scope.value,
            "names": names,
            "versions": [version],
        },
    }


def to_yaml(data: Any) -> str:
    """Render JSON-compatible data as plain YAML.

    Args:
        data: Any JSON-compatible structure.

    Returns:
        The YAML document as a string.

    Raises:
        CrdError: If the optional ``pyyaml`` dependency is not installed.
    """
    yaml = _import_yaml()
    return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)


def crd_yaml(resource_type: type[CustomResource[Any, Any]]) -> str:
    """Render the CRD manifest of a resource as plain YAML.

    Args:
        resource_type: The resource class to describe.

    Returns:
        The CRD manifest as a YAML string.

    Raises:
        ResourceDefinitionError: If the class is not a valid resource definition.
        SchemaGenerationError: If the resource schema cannot be represented.
        CrdError: If the optional ``pyyaml`` dependency is not installed.
    """
    return to_yaml(crd_manifest(resource_type))


def _import_yaml() -> Any:
    """Import PyYAML lazily.

    Returns:
        The imported :mod:`yaml` module.

    Raises:
        CrdError: If PyYAML is not installed.
    """
    try:
        import yaml
    except ImportError as error:  # pragma: no cover - exercised via monkeypatch
        raise CrdError(
            "YAML rendering requires the optional 'pyyaml' dependency; "
            "install 'based-operators[yaml]'"
        ) from error
    return yaml
