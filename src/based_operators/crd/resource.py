"""The typed custom-resource base class."""

from __future__ import annotations

from types import NoneType
from typing import TYPE_CHECKING, Any, ClassVar, Generic, TypeVar, get_args
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.fields import FieldInfo

from based_operators.crd.config import ResourceConfig, Scope
from based_operators.crd.errors import ResourceDefinitionError
from based_operators.crd.metadata import ObjectMeta

if TYPE_CHECKING:
    from based_operators.handlers import ResourceHandlers

SpecT = TypeVar("SpecT", bound=BaseModel)
"""The concrete Pydantic model type describing a resource's ``spec``."""

StatusT = TypeVar("StatusT", default=NoneType)
"""The concrete Pydantic model type describing a resource's ``status``.

Defaults to :data:`types.NoneType`, which marks a resource as status-less.
"""


class _HandlersAccessor:
    """Descriptor exposing the kopf handler namespace of a resource class.

    Accessing ``<Resource>.on`` returns the
    :class:`~based_operators.handlers.ResourceHandlers` namespace shared by that
    resource class. The handler module is imported lazily so that the CRD layer
    keeps working without the kopf interface being loaded or kopf installed.
    """

    def __get__(
        self,
        instance: Any,
        owner: type[CustomResource[Any, Any]],
    ) -> ResourceHandlers:
        """Return the handler namespace bound to the owning resource class.

        Args:
            instance: The resource instance, when accessed through an instance.
            owner: The owning class, when accessed through the class.

        Returns:
            The shared handler namespace of the owning resource class.
        """
        from based_operators.handlers import handler_namespace

        return handler_namespace(owner)


class CustomResource(BaseModel, Generic[SpecT, StatusT]):
    """Base class for typed custom-resource models.

    A concrete resource is declared by subclassing with a concrete spec model and,
    optionally, a concrete status model::

        class Widget(CustomResource[WidgetSpec, WidgetStatus]):
            resource_config = ResourceConfig(
                group="example.com", version="v1", kind="Widget",
                plural="widgets", scope=Scope.NAMESPACED,
            )

    Subclassing with ``CustomResource[WidgetSpec, None]`` (or ``CustomResource[
    WidgetSpec]``) declares a status-less resource that never acquires a status
    field, status schema or status subresource.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    resource_config: ClassVar[ResourceConfig]
    """The :class:`~based_operators.crd.ResourceConfig` describing this resource."""

    on: ClassVar[_HandlersAccessor] = _HandlersAccessor()
    """The kopf handler namespace, e.g. ``@<Resource>.on.create``.

    See :class:`~based_operators.handlers.ResourceHandlers` for the available
    decorators. The namespace is shared per resource class.
    """

    apiVersion: str = Field(  # noqa: N815 - Kubernetes wire name
        default="",
        description="Derived from resource_config.",
    )
    """The ``group/version`` string, derived from ``resource_config``."""

    kind: str = Field(
        default="",
        description="Derived from resource_config.",
    )
    """The CamelCased kind, derived from ``resource_config``."""

    metadata: ObjectMeta = Field(default_factory=ObjectMeta)
    """Standard Kubernetes object metadata."""

    spec: SpecT
    """The typed resource specification."""

    status: StatusT | None = None
    """The typed resource status; absent for status-less resources."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Drop the ``status`` field for status-less resources.

        Pydantic resolves the generic parameters before this hook runs, but a
        concrete subclass re-collects the inherited ``status`` annotation from the
        generic base. The resolved status parameter is therefore read from the
        parametrized base in the MRO: when it resolves to ``None`` the ``status``
        field is removed and the model rebuilt so that no status field, schema or
        subresource can leak into generated manifests.

        Args:
            **kwargs: Forwarded to the Pydantic subclass hook.
        """
        super().__pydantic_init_subclass__(**kwargs)
        if _resolved_status_argument(cls) in (NoneType, None) and "status" in cls.model_fields:
            del cls.model_fields["status"]
            cls.model_rebuild(force=True)

    @classmethod
    def _require_resource_config(cls) -> ResourceConfig:
        """Return the resource configuration, or raise if it is missing.

        Returns:
            The :class:`~based_operators.crd.ResourceConfig` declared on the class.

        Raises:
            ResourceDefinitionError: If ``resource_config`` is unset or of the
                wrong type.
        """
        config = getattr(cls, "resource_config", None)
        if config is None:
            raise ResourceDefinitionError(
                f"{cls.__name__} does not define a 'resource_config' ResourceConfig class variable"
            )
        if not isinstance(config, ResourceConfig):
            raise ResourceDefinitionError(
                f"{cls.__name__}.resource_config must be a ResourceConfig, "
                f"got {type(config).__name__}"
            )
        return config

    @model_validator(mode="before")
    @classmethod
    def _derive_identity(cls, data: Any) -> Any:
        """Inject ``apiVersion`` and ``kind`` from the resource configuration.

        Args:
            data: The raw input passed to validation.

        Returns:
            The input with identity fields defaulted, or the input unchanged when
            it is not a mapping.

        Raises:
            ValueError: If provided identity fields conflict with the configuration.
        """
        if not isinstance(data, Mapping):
            return data
        config = cls._require_resource_config()
        data = dict(data)

        api_version = data.get("apiVersion")
        if api_version is not None and api_version != config.api_version:
            raise ValueError(
                f"apiVersion {api_version!r} conflicts with resource_config {config.api_version!r}"
            )
        data["apiVersion"] = config.api_version

        kind = data.get("kind")
        if kind is not None and kind != config.kind:
            raise ValueError(f"kind {kind!r} conflicts with resource_config {config.kind!r}")
        data["kind"] = config.kind

        return data

    @model_validator(mode="after")
    def _check_scope(self) -> CustomResource[SpecT, StatusT]:
        """Reject ``metadata.namespace`` on cluster-scoped resources.

        Returns:
            The validated resource.

        Raises:
            ValueError: If a cluster-scoped resource sets ``metadata.namespace``.
        """
        config = type(self)._require_resource_config()
        if config.scope is Scope.CLUSTER and self.metadata.namespace is not None:
            raise ValueError(
                f"cluster-scoped resource {config.kind!r} must not set metadata.namespace"
            )
        return self


_UNRESOLVED_STATUS = object()


def _resolved_status_argument(cls: type) -> Any:
    """Return the resolved status type argument for a resource subclass.

    Args:
        cls: The class whose MRO is searched.

    Returns:
        The resolved status type argument, or :data:`_UNRESOLVED_STATUS` when the
        class has no parametrized ``CustomResource`` base.
    """
    for base in cls.__mro__:
        generic_metadata = getattr(base, "__pydantic_generic_metadata__", None) or {}
        args = generic_metadata.get("args") or ()
        if len(args) == 2:
            return args[1]
    return _UNRESOLVED_STATUS


def _strip_optional(annotation: Any) -> Any:
    """Return the non-``None`` member of an optional annotation.

    Args:
        annotation: A type annotation that may be an ``X | None`` union.

    Returns:
        The single non-``None`` member when present, otherwise the annotation.
    """
    if annotation is NoneType:
        return NoneType
    args = get_args(annotation)
    if args and NoneType in args:
        rest = tuple(arg for arg in args if arg is not NoneType)
        if len(rest) == 1:
            return rest[0]
    return annotation


def _require_payload_model(annotation: Any, field_name: str, owner: type) -> type[BaseModel]:
    """Assert that a payload annotation resolves to a concrete Pydantic model.

    Args:
        annotation: The resolved annotation of the ``spec`` or ``status`` field.
        field_name: The field name, used in error messages.
        owner: The resource class, used in error messages.

    Returns:
        The concrete :class:`pydantic.BaseModel` subclass.

    Raises:
        ResourceDefinitionError: If the annotation is a type variable or is not a
            Pydantic model.
    """
    resolved = _strip_optional(annotation)
    if isinstance(resolved, TypeVar):
        raise ResourceDefinitionError(
            f"{owner.__name__}.{field_name} has unresolved generic parameter {resolved!r}; "
            "parameterize CustomResource with a concrete Pydantic model"
        )
    if not (isinstance(resolved, type) and issubclass(resolved, BaseModel)):
        raise ResourceDefinitionError(
            f"{owner.__name__}.{field_name} must be a concrete Pydantic BaseModel subclass, "
            f"got {resolved!r}"
        )
    return resolved


def _iter_nested_models(annotation: Any) -> list[type[BaseModel]]:
    """Collect the Pydantic models reachable from a type annotation.

    Args:
        annotation: A type annotation possibly wrapping models in containers.

    Returns:
        The list of directly referenced :class:`pydantic.BaseModel` subclasses.
    """
    annotation = _strip_optional(annotation)
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    models: list[type[BaseModel]] = []
    for arg in get_args(annotation):
        models.extend(_iter_nested_models(arg))
    return models


def _check_wire_names(model: type[BaseModel], visited: set[type[BaseModel]]) -> None:
    """Reject inconsistent validation/serialization names and shape serializers.

    Args:
        model: The model to inspect.
        visited: Models already inspected, to bound recursion.

    Raises:
        ResourceDefinitionError: If a field uses mismatched validation and
            serialization aliases, or if the model customises serialization in a
            way that can change the wire shape.
    """
    if model in visited:
        return
    visited.add(model)

    decorators = getattr(model, "__pydantic_decorators__", None)
    if decorators is not None and (
        decorators.field_serializers or decorators.model_serializers or decorators.computed_fields
    ):
        raise ResourceDefinitionError(
            f"{model.__name__} defines a custom serializer or computed field; "
            "shape-changing serialization is not supported"
        )

    for name, field in model.model_fields.items():
        _check_field_wire_name(model, name, field)
        for nested in _iter_nested_models(field.annotation):
            _check_wire_names(nested, visited)


def _check_field_wire_name(model: type[BaseModel], name: str, field: FieldInfo) -> None:
    """Reject a single field whose wire names cannot be reconciled.

    Args:
        model: The owning model.
        name: The field name.
        field: The field information.

    Raises:
        ResourceDefinitionError: If validation and serialization aliases differ.
    """
    validation_alias = field.validation_alias
    serialization_alias = field.serialization_alias
    if validation_alias is None and serialization_alias is None:
        return
    if validation_alias is None or serialization_alias is None:
        raise ResourceDefinitionError(
            f"{model.__name__}.{name} sets only one of validation/serialization alias; "
            "use a single 'alias' so incoming and outgoing wire names stay consistent"
        )
    if validation_alias != serialization_alias:
        raise ResourceDefinitionError(
            f"{model.__name__}.{name} has differing validation alias {validation_alias!r} and "
            f"serialization alias {serialization_alias!r}; this cannot be reconciled"
        )


def validate_resource_definition(resource_type: type[CustomResource[Any, Any]]) -> ResourceConfig:
    """Validate that a class is a complete, CRD-generatable resource definition.

    The check is intentionally repeated before every schema/manifest generation so
    that an incomplete class can never silently produce a manifest.

    Args:
        resource_type: The resource class to validate.

    Returns:
        The resource's :class:`~based_operators.crd.ResourceConfig`.

    Raises:
        ResourceDefinitionError: If the class is not a concrete resource with
            concrete Pydantic payload types and consistent wire names.
    """
    if not (isinstance(resource_type, type) and issubclass(resource_type, CustomResource)):
        raise ResourceDefinitionError(f"{resource_type!r} is not a CustomResource subclass")

    generic_metadata = getattr(resource_type, "__pydantic_generic_metadata__", None) or {}
    if generic_metadata.get("parameters"):
        raise ResourceDefinitionError(
            f"{resource_type.__name__} has unresolved generic parameters; parameterize "
            "CustomResource with concrete spec and status models"
        )

    config = resource_type._require_resource_config()

    fields = resource_type.model_fields
    spec_field = fields.get("spec")
    if spec_field is None:
        raise ResourceDefinitionError(f"{resource_type.__name__} has no 'spec' field")
    spec_model = _require_payload_model(spec_field.annotation, "spec", resource_type)

    status_model: type[BaseModel] | None = None
    if "status" in fields:
        status_model = _require_payload_model(fields["status"].annotation, "status", resource_type)

    try:
        for model in (resource_type, spec_model, status_model):
            if model is not None:
                model.model_rebuild()
    except Exception as error:  # noqa: BLE001 - surfaced as a definition error
        raise ResourceDefinitionError(
            f"{resource_type.__name__} could not rebuild its model: {error}"
        ) from error

    visited: set[type[BaseModel]] = set()
    _check_wire_names(resource_type, visited)
    _check_wire_names(spec_model, visited)
    if status_model is not None:
        _check_wire_names(status_model, visited)

    return config
