"""Model-first Python operator toolkit.

The public surface exposes typed Pydantic custom-resource models and Kubernetes
CRD generation from :mod:`based_operators.crd`, together with the kopf handler
interface from :mod:`based_operators.handlers`. A resource class reaches its
kopf decorators through ``<Resource>.on``.
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
from based_operators.handlers import (
    Diff,
    HandlerError,
    Patch,
    RawEvent,
    ResourceHandlers,
    handler_namespace,
)

__all__ = [
    "CrdError",
    "CustomResource",
    "Diff",
    "HandlerError",
    "ObjectMeta",
    "Patch",
    "RawEvent",
    "ResourceConfig",
    "ResourceDefinitionError",
    "ResourceHandlers",
    "SchemaGenerationError",
    "Scope",
    "crd_manifest",
    "crd_yaml",
    "handler_namespace",
    "kubernetes_schema",
    "source_json_schema",
    "to_yaml",
]
