"""Tests for :mod:`based_operators.crd.config`."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from based_operators.crd import ResourceConfig, Scope


def _config(**overrides: Any) -> ResourceConfig:
    """Build a valid config, overriding selected fields."""
    values: dict[str, Any] = {
        "group": "example.com",
        "version": "v1",
        "kind": "Widget",
        "plural": "widgets",
        "scope": Scope.NAMESPACED,
    }
    values.update(overrides)
    return ResourceConfig(**values)


def test_valid_config_defaults() -> None:
    """A minimal config is valid and derives identity strings."""
    config = _config()

    assert config.scope is Scope.NAMESPACED
    assert config.singular is None
    assert config.short_names == ()
    assert config.api_version == "example.com/v1"
    assert config.crd_name == "widgets.example.com"


def test_short_names() -> None:
    """Short names are accepted and preserved."""
    config = _config(short_names=("wd", "wid"))

    assert config.short_names == ("wd", "wid")


def test_config_is_frozen() -> None:
    """The configuration is immutable."""
    config = _config()

    with pytest.raises(ValidationError):
        config.group = "other.com"  # type: ignore[misc]


def test_unknown_key_is_rejected() -> None:
    """Unknown configuration keys are rejected."""
    with pytest.raises(ValidationError):
        _config(unexpected="value")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("group", "Example.com"),
        ("group", "example..com"),
        ("group", "-example.com"),
        ("version", "V1"),
        ("version", "v1/x"),
        ("kind", "widget"),
        ("kind", "1Widget"),
        ("kind", "Widget-Thing"),
        ("plural", "Widgets"),
        ("plural", "widget_s"),
        ("singular", "Widget"),
        ("short_names", ("WD",)),
    ],
)
def test_invalid_identifiers(field: str, value: object) -> None:
    """Malformed identifiers are rejected with a validation error."""
    with pytest.raises(ValidationError):
        _config(**{field: value})


def test_scope_must_be_known() -> None:
    """Only the two documented scopes are accepted."""
    with pytest.raises(ValidationError):
        _config(scope="global")


def test_singular_must_differ_from_plural() -> None:
    """A singular identical to the plural is rejected."""
    with pytest.raises(ValidationError):
        _config(plural="widgets", singular="widgets")


def test_duplicate_short_names_rejected() -> None:
    """Duplicate short names are rejected."""
    with pytest.raises(ValidationError):
        _config(short_names=("wd", "wd"))


def test_missing_required_field() -> None:
    """Scope is required."""
    with pytest.raises(ValidationError):
        ResourceConfig(  # type: ignore[call-arg]
            group="example.com", version="v1", kind="Widget", plural="widgets"
        )
