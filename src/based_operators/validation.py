"""Centralized input-validation policies for typed Kopf handlers."""

from typing import Any

import kopf
from pydantic import BaseModel, TypeAdapter, ValidationError


class InvalidDesiredInputError(Exception):
    """A resource's desired spec failed Pydantic validation."""

    def __init__(self, errors: list[Any]) -> None:
        """Retain only error locations, not potentially sensitive input values."""
        self.locations = [tuple(error["loc"]) for error in errors]
        super().__init__("invalid desired spec at " + ", ".join(
            ".".join(str(part) for part in location) or "spec" for location in self.locations[:5]
        ))


def validate_resource(model: type[BaseModel], body: dict[str, Any]) -> BaseModel:
    """Validate desired spec separately from stale status; return a detached snapshot."""
    spec_field = model.model_fields["spec"]
    try:
        TypeAdapter(spec_field.annotation).validate_python(body.get("spec"))
    except ValidationError as error:
        raise InvalidDesiredInputError(error.errors(include_input=False)) from error
    snapshot = dict(body)
    try:
        return model.model_validate(snapshot)
    except ValidationError as error:
        if not error.errors() or any(
            details["loc"][0] != "status" for details in error.errors()
        ):
            raise
        # Status is observed, not desired. If stale status is invalid, still
        # reconcile the current spec when the status field has a safe default.
        snapshot.pop("status", None)
        return model.model_validate(snapshot)


def respond_invalid(category: str, error: InvalidDesiredInputError, delay: float) -> None:
    """Apply category-specific policy without masking business-logic exceptions."""
    if category in {"create", "resume", "update", "field", "timer"}:
        raise kopf.TemporaryError(str(error), delay=delay) from error
    if category in {"validate", "mutate"}:
        raise kopf.AdmissionError(str(error), code=422) from error
