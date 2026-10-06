"""Explicit status serialization to a Kopf patch."""

from typing import Any

from pydantic import BaseModel


def patch_status(patch: Any, status: BaseModel) -> None:
    """Merge serialized status keys into a patch; omitted keys are unchanged.

    Explicit ``None`` values are retained (Kubernetes merge patch deletes them).
    """
    if not isinstance(status, BaseModel):
        raise TypeError("status must be a Pydantic model")
    if not isinstance(patch, dict):
        raise TypeError("patch must be a dict")
    status_patch = patch.setdefault("status", {})
    if not isinstance(status_patch, dict):
        raise TypeError("patch['status'] must be a dict")
    def merge(target: dict, updates: dict) -> None:
        for key, value in updates.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    merge(
        status_patch,
        status.model_dump(mode="json", by_alias=True, exclude_unset=True),
    )
