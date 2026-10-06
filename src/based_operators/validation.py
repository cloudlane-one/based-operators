"""Centralized input-validation policies for typed Kopf handlers."""

from typing import Any

import kopf
from pydantic import AliasChoices, AliasPath, BaseModel, ValidationError


class InvalidDesiredInputError(Exception):
    """A resource's desired spec failed Pydantic validation."""

    def __init__(self, errors: list[Any]) -> None:
        """Retain only error locations, not potentially sensitive input values."""
        self.locations = [tuple(error["loc"]) for error in errors]
        super().__init__("invalid desired spec at " + ", ".join(
            ".".join(str(part) for part in location) or "spec" for location in self.locations[:5]
        ))


def validate_resource[T: BaseModel](model: type[T], body: dict[str, Any]) -> T:
    """Validate desired spec separately from stale status; return a detached snapshot."""
    from copy import deepcopy

    snapshot = deepcopy(dict(body))
    metadata = snapshot.get("metadata")
    if (
        "namespace" in model.model_fields
        and "namespace" not in snapshot
        and isinstance(metadata, dict)
        and "namespace" in metadata
        and not _namespace_already_aliased_to_metadata(model)
    ):
        snapshot["namespace"] = metadata["namespace"]
    try:
        return model.model_validate(snapshot)
    except ValidationError as error:
        errors = error.errors(include_input=False)
        if errors and all(
            (loc := _normalized_location(model, details.get("loc")))
            and loc[0] == "status"
            for details in errors
        ):
            # Status is observed, not desired. If stale status is invalid, still
            # reconcile the current spec when the status field has a safe default.
            snapshot.pop("status", None)
            try:
                return model.model_validate(snapshot)
            except ValidationError as fallback_error:
                _raise_if_invalid_spec(model, fallback_error)
                raise
        _raise_if_invalid_spec(model, error)
        raise


def _normalized_location(
    model: type[BaseModel], location: tuple[Any, ...] | None
) -> tuple[Any, ...] | None:
    """Normalize an envelope field's validation alias to its model field name."""
    if not location:
        return location
    for field_name in ("spec", "status"):
        field = model.model_fields.get(field_name)
        if field is None or not model.model_config.get("validate_by_alias", True):
            continue
        input_alias = field.validation_alias or field.alias
        if input_alias is None:
            continue
        aliases = input_alias.choices if isinstance(input_alias, AliasChoices) else (input_alias,)
        for alias in aliases:
            alias_path = alias.convert_to_aliases() if isinstance(alias, AliasPath) else [alias]
            alias_prefix = tuple(alias_path)
            if location[:len(alias_prefix)] == alias_prefix:
                return (field_name, *location[len(alias_prefix):])
    return location


def _namespace_already_aliased_to_metadata(model: type[BaseModel]) -> bool:
    """Return whether the namespace field already reads from metadata.namespace."""
    field = model.model_fields.get("namespace")
    if field is None or not model.model_config.get("validate_by_alias", True):
        return False
    input_alias = field.validation_alias or field.alias
    if input_alias is None:
        return False
    aliases = input_alias.choices if isinstance(input_alias, AliasChoices) else (input_alias,)
    for alias in aliases:
        if not isinstance(alias, AliasPath):
            continue
        alias_path = alias.convert_to_aliases()
        if alias_path[:2] == ["metadata", "namespace"]:
            return True
    return False


def _raise_if_invalid_spec(model: type[BaseModel], error: ValidationError) -> None:
    errors = error.errors(include_input=False)
    spec_errors = [
        details for details in errors
        if (loc := _normalized_location(model, details.get("loc"))) and loc[0] == "spec"
    ]
    unrelated_errors = [
        details for details in errors
        if not (loc := _normalized_location(model, details.get("loc")))
        or loc[0] not in {"spec", "status"}
    ]
    if spec_errors and not unrelated_errors:
        raise InvalidDesiredInputError(spec_errors) from None


def respond_invalid(category: str, error: InvalidDesiredInputError, delay: float) -> None:
    """Apply category-specific policy without masking business-logic exceptions."""
    if category in {"create", "resume", "update", "field", "timer"}:
        raise kopf.TemporaryError(str(error), delay=delay) from error
    if category in {"validate", "mutate"}:
        raise kopf.AdmissionError(str(error), code=422) from error
