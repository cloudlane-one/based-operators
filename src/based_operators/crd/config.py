"""Immutable identity and scope configuration for a custom resource."""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Kubernetes identifier syntax, see:
# https://kubernetes.io/docs/concepts/overview/working-with-objects/names/
_DNS_1123_SUBDOMAIN = re.compile(
    r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
)
_DNS_1035_LABEL = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
_DNS_1123_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_CAMEL_KIND = re.compile(r"^[A-Z][A-Za-z0-9]*$")

_MAX_GROUP_LENGTH = 253
_MAX_NAME_LENGTH = 63


class Scope(StrEnum):
    """The scope of a custom resource, mirroring ``spec.scope`` of a CRD."""

    NAMESPACED = "Namespaced"
    CLUSTER = "Cluster"


class ResourceConfig(BaseModel):
    """Immutable identity and scope configuration held on a resource subclass.

    The configuration is stored as the ``resource_config`` class variable of a
    :class:`~based_operators.crd.CustomResource` subclass and drives identity
    derivation, CRD naming and scope validation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    group: str = Field(description="API group, e.g. ``example.com``.")
    """The API group the resource belongs to, e.g. ``example.com``."""

    version: str = Field(description="API version, e.g. ``v1``.")
    """The single served and stored API version, e.g. ``v1``."""

    kind: str = Field(description="CamelCased kind, e.g. ``Widget``.")
    """The CamelCased kind used in resource manifests."""

    plural: str = Field(description="Lowercase plural resource name, e.g. ``widgets``.")
    """The lowercase plural used in the REST path and CRD name."""

    scope: Scope = Field(description="Whether the resource is namespaced or cluster-scoped.")
    """Whether the resource is ``Namespaced`` or ``Cluster`` scoped."""

    singular: str | None = Field(
        default=None, description="Optional lowercase singular resource name."
    )
    """Optional lowercase singular alias; defaults to the lowercased kind."""

    short_names: tuple[str, ...] = Field(default=(), description="Optional CLI short names.")
    """Optional CLI short names, e.g. ``("wd",)``."""

    @field_validator("group")
    @classmethod
    def _check_group(cls, value: str) -> str:
        if len(value) > _MAX_GROUP_LENGTH or not _DNS_1123_SUBDOMAIN.match(value):
            raise ValueError(
                f"group {value!r} must be a DNS-1123 subdomain "
                f"(lowercase alphanumeric, '-', '.', max {_MAX_GROUP_LENGTH} chars)"
            )
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if len(value) > _MAX_NAME_LENGTH or not _DNS_1123_LABEL.match(value):
            raise ValueError(
                f"version {value!r} must be a DNS-1123 label "
                f"(lowercase alphanumeric and '-', max {_MAX_NAME_LENGTH} chars)"
            )
        return value

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, value: str) -> str:
        if len(value) > _MAX_NAME_LENGTH or not _CAMEL_KIND.match(value):
            raise ValueError(
                f"kind {value!r} must be CamelCase starting with an uppercase letter "
                f"(max {_MAX_NAME_LENGTH} chars)"
            )
        return value

    @field_validator("plural")
    @classmethod
    def _check_plural(cls, value: str) -> str:
        if len(value) > _MAX_NAME_LENGTH or not _DNS_1035_LABEL.match(value):
            raise ValueError(
                f"plural {value!r} must be a DNS-1035 label "
                f"(start with a lowercase letter, max {_MAX_NAME_LENGTH} chars)"
            )
        return value

    @field_validator("singular")
    @classmethod
    def _check_singular(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value) > _MAX_NAME_LENGTH or not _DNS_1035_LABEL.match(value):
            raise ValueError(
                f"singular {value!r} must be a DNS-1035 label "
                f"(start with a lowercase letter, max {_MAX_NAME_LENGTH} chars)"
            )
        return value

    @field_validator("short_names")
    @classmethod
    def _check_short_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for name in value:
            if len(name) > _MAX_NAME_LENGTH or not _DNS_1035_LABEL.match(name):
                raise ValueError(
                    f"short name {name!r} must be a DNS-1035 label "
                    f"(start with a lowercase letter, max {_MAX_NAME_LENGTH} chars)"
                )
        if len(set(value)) != len(value):
            raise ValueError(f"short names {value!r} contain duplicates")
        return value

    @model_validator(mode="after")
    def _check_consistency(self) -> ResourceConfig:
        if self.singular is not None and self.singular == self.plural:
            raise ValueError(f"singular {self.singular!r} must differ from plural {self.plural!r}")
        return self

    @property
    def api_version(self) -> str:
        """The ``group/version`` string used as ``apiVersion``."""
        return f"{self.group}/{self.version}"

    @property
    def crd_name(self) -> str:
        """The ``plural.group`` name of the generated CustomResourceDefinition."""
        return f"{self.plural}.{self.group}"
