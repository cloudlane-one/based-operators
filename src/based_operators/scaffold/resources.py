"""Load an operator entry point and the custom resources it declares.

The entry point is the Python file that is handed to ``kopf run`` inside the
generated image, so it is loaded exactly the way kopf loads it (see kopf's own
``loaders.preload``): the file's directory is put in front of ``sys.path`` and the
file is executed as a fresh module. Loading it the same way matters, because it is
the only way to guarantee that the derived CRDs cover precisely the resource
classes the operator would register handlers for at runtime.

Resources are then picked from the module namespace, which covers both halves of
"contain or reference the handlers": a resource declared in the entry point and a
resource imported into it are equally visible there.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from based_operators.crd import CustomResource, ResourceConfig
from based_operators.crd.errors import CrdError
from based_operators.crd.resource import validate_resource_definition
from based_operators.scaffold.errors import ResourceLoadError

__all__ = [
    "discover_resources",
    "is_resource_class",
    "load_operator",
]


def load_operator(path: Path) -> ModuleType:
    """Execute an operator entry point the way ``kopf run <path>`` does.

    Args:
        path: The path of the entry point.

    Returns:
        The executed module.

    Raises:
        ResourceLoadError: If the file cannot be imported, or raises while it is
            imported.
    """
    sys.path.insert(0, str(path.parent.resolve()))
    name = f"__based_operator_script__{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec) if spec is not None else None
    if module is None or spec is None or spec.loader is None:
        raise ResourceLoadError(f"{path} is not an importable Python file")

    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:  # noqa: BLE001 - reported as a load failure
        raise ResourceLoadError(f"{path} could not be imported: {error!r}") from error
    return module


def discover_resources(module: ModuleType) -> list[type[CustomResource[Any, Any]]]:
    """Collect the concrete custom resources an entry point declares.

    Args:
        module: The module produced by :func:`load_operator`.

    Returns:
        The declared resource classes, ordered by their CRD name.

    Raises:
        ResourceLoadError: If the module declares no resource at all, or declares
            one that is not a complete resource definition.
    """
    found: dict[str, type[CustomResource[Any, Any]]] = {}
    for name, value in vars(module).items():
        if not is_resource_class(value):
            continue
        try:
            config = validate_resource_definition(value)
        except CrdError as error:
            raise ResourceLoadError(
                f"{module.__name__}.{name} is not a valid resource definition: {error}"
            ) from error
        found[config.crd_name] = value

    if not found:
        raise ResourceLoadError(
            f"{module.__name__} declares no custom resource; a resource is a "
            "CustomResource subclass that sets 'resource_config'"
        )
    return [found[crd_name] for crd_name in sorted(found)]


def is_resource_class(value: object) -> bool:
    """Return whether a module attribute is a concrete, configured resource.

    Intermediate base classes are skipped deliberately: a class that still has
    unresolved generic parameters, or that does not declare a
    :class:`~based_operators.crd.ResourceConfig`, is scaffolding for other
    resources rather than a resource of its own.

    Args:
        value: An attribute found in the module namespace.

    Returns:
        ``True`` for a :class:`~based_operators.crd.CustomResource` subclass that
        is fully parameterized and carries a resource configuration.
    """
    if not (isinstance(value, type) and issubclass(value, CustomResource)):
        return False
    metadata = getattr(value, "__pydantic_generic_metadata__", None) or {}
    if metadata.get("parameters"):
        return False
    return isinstance(getattr(value, "resource_config", None), ResourceConfig)
