"""Model-first Python operator toolkit.

The public surface currently exposes typed Pydantic custom-resource models and
Kubernetes CRD generation from :mod:`based_operators.crd`.
"""

from based_operators.crd import (
    CrdError,
    CustomResource,
    ObjectMeta,
    ResourceConfig,
    ResourceDefinitionError,
    SchemaGenerationError,
    Scope,
    crd_manifest,
    crd_yaml,
    kubernetes_schema,
    source_json_schema,
    to_yaml,
)

__all__ = [
    "CrdError",
    "CustomResource",
    "ObjectMeta",
    "ResourceConfig",
    "ResourceDefinitionError",
    "SchemaGenerationError",
    "Scope",
    "crd_manifest",
    "crd_yaml",
    "kubernetes_schema",
    "source_json_schema",
    "to_yaml",
]
