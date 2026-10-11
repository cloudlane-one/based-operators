"""Exceptions raised by the CRD modelling layer."""

from __future__ import annotations


class CrdError(Exception):
    """Base class for every error raised by :mod:`based_operators.crd`."""


class ResourceDefinitionError(CrdError):
    """Raised when a resource class or its configuration is not a valid definition.

    This covers missing or malformed :class:`~based_operators.crd.ResourceConfig`
    values, unresolved generic parameters, non-Pydantic payload types, and
    inconsistent wire names between validation and serialization.
    """


class SchemaGenerationError(CrdError):
    """Raised when a Pydantic schema cannot be represented faithfully in Kubernetes.

    The message always names the offending model, field and JSON-schema path so
    that no validation semantics are dropped silently.
    """
