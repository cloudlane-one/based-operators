"""Single kdantic-backed identity resolution for registration and CRDs."""

from unittest.mock import patch
from typing import Any

from kdantic.helpers.annotations import CRDMetaData
from pydantic import BaseModel


def resolve_metadata(model: type[BaseModel]) -> CRDMetaData:
    """Resolve a model's Kubernetes identity with kdantic's naming rules.

    Reject conflicting annotations/dunder metadata instead of letting kdantic
    silently ignore a contradictory declaration.
    """
    from kdantic.helpers.annotations import build_crd_meta
    from kdantic.helpers.settings import Settings

    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise TypeError("model must be a Pydantic BaseModel subclass")
    if "spec" not in model.model_fields:
        raise ValueError(f"{model.__name__} must declare a spec field")
    settings_options: dict[str, Any] = {"_cli_parse_args": []}
    with patch("kdantic.helpers.annotations.kdantic_settings", Settings(**settings_options)):
        meta = build_crd_meta(model)
    if not meta.group or not meta.version or meta.scope not in {"Namespaced", "Cluster"}:
        raise ValueError(f"{model.__name__} needs a valid group, version and scope")
    identity = {
        "apiVersion": f"{meta.group}/{meta.version}",
        "kind": meta.names.kind,
    }
    for name, expected in identity.items():
        field = model.model_fields.get(name)
        if field is not None and isinstance(field.default, str) and field.default != expected:
            raise ValueError(f"{model.__name__}.{name} conflicts with resolved CRD identity")
    return meta
