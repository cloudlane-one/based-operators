"""Typed Pydantic custom resources and Kubernetes CRD generation.

This package provides a Pydantic-first base class for custom-resource models and
a thin, tested adapter that turns Pydantic's JSON Schema output into structural
Kubernetes OpenAPI v3 schemas and ``apiextensions.k8s.io/v1`` CRD manifests.
"""

from based_operators.crd.config import ResourceConfig, Scope
from based_operators.crd.errors import (
    CrdError,
    ResourceDefinitionError,
    SchemaGenerationError,
)
from based_operators.crd.kubernetes import kubernetes_schema, source_json_schema
from based_operators.crd.manifest import crd_manifest, crd_yaml, to_yaml
from based_operators.crd.metadata import ObjectMeta
from based_operators.crd.resource import CustomResource

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
