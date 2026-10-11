"""A minimal ``metadata`` model that preserves unknown server fields."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ObjectMeta(BaseModel):
    """Common ``metadata`` fields, tolerant of unknown server-populated keys.

    The model intentionally keeps only the fields a client is expected to read
    or write. Server-managed fields such as ``uid`` and ``resourceVersion`` are
    optional, and any additional metadata the API server returns is preserved
    rather than rejected.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    name: str | None = Field(default=None, description="Name of the resource.")
    """The resource name, unique within its namespace."""

    generate_name: str | None = Field(
        default=None, alias="generateName", description="Server-side name prefix."
    )
    """Prefix used by the API server to generate a unique name."""

    namespace: str | None = Field(default=None, description="Namespace of the resource.")
    """The namespace; must be omitted for cluster-scoped resources."""

    labels: dict[str, str] | None = Field(default=None, description="Map of string labels.")
    """Key/value labels for selection and grouping."""

    annotations: dict[str, str] | None = Field(
        default=None, description="Map of string annotations."
    )
    """Key/value annotations for arbitrary metadata."""

    uid: str | None = Field(default=None, description="Server-generated unique identifier.")
    """Server-generated unique identifier; not required for creation input."""

    resource_version: str | None = Field(
        default=None, alias="resourceVersion", description="Server-side version token."
    )
    """Opaque server-side version token; not required for creation input."""

    generation: int | None = Field(default=None, description="Server-side generation counter.")
    """Server-side generation counter, incremented on spec changes."""

    creation_timestamp: str | None = Field(
        default=None, alias="creationTimestamp", description="RFC 3339 creation timestamp."
    )
    """RFC 3339 timestamp of creation, set by the server."""

    deletion_timestamp: str | None = Field(
        default=None, alias="deletionTimestamp", description="RFC 3339 deletion timestamp."
    )
    """RFC 3339 timestamp set when deletion is requested."""

    deletion_grace_period_seconds: int | None = Field(
        default=None,
        alias="deletionGracePeriodSeconds",
        description="Grace period before deletion.",
    )
    """Grace period, in seconds, before the resource is deleted."""

    finalizers: list[str] | None = Field(default=None, description="List of finalizers.")
    """Finalizers that must be removed before the resource can be deleted."""

    owner_references: list[dict[str, Any]] | None = Field(
        default=None, alias="ownerReferences", description="List of owning references."
    )
    """References to the objects that own this resource."""

    managed_fields: list[dict[str, Any]] | None = Field(
        default=None, alias="managedFields", description="Server-side field management records."
    )
    """Server-side field-management records."""
