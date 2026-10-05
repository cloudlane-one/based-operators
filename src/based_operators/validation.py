"""Centralized input-validation policies for typed Kopf handlers."""

from typing import Any

import kopf
from pydantic import BaseModel, ValidationError


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
    try:
        return model.model_validate(dict(body))
    except ValidationError as error:
        errors = error.errors(include_input=False)
        if errors and all(
            (loc := details.get("loc")) and loc[0] == "status" for details in errors
        ):
            # Status is observed, not desired. If stale status is invalid, still
            # reconcile the current spec when the status field has a safe default.
            snapshot = dict(body)
            snapshot.pop("status", None)
            try:
                return model.model_validate(snapshot)
            except ValidationError as fallback_error:
                _raise_if_invalid_spec(fallback_error)
                raise
        _raise_if_invalid_spec(error)
        raise


def _raise_if_invalid_spec(error: ValidationError) -> None:
    errors = error.errors(include_input=False)
    spec_errors = [
        details for details in errors
        if (loc := details.get("loc")) and loc[0] == "spec"
    ]
    unrelated_errors = [
        details for details in errors
        if not (loc := details.get("loc"))
        or loc[0] not in {"spec", "status"}
    ]
    if spec_errors and not unrelated_errors:
        raise InvalidDesiredInputError(spec_errors) from error


def respond_invalid(category: str, error: InvalidDesiredInputError, delay: float) -> None:
    """Apply category-specific policy without masking business-logic exceptions."""
    if category in {"create", "resume", "update", "field", "timer"}:
        raise kopf.TemporaryError(str(error), delay=delay) from error
    if category in {"validate", "mutate"}:
        raise kopf.AdmissionError(str(error), code=422) from error
