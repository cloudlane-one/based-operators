"""Tests for the kopf handler interface in :mod:`based_operators.handlers`."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import kopf
import pytest
from pydantic import BaseModel, ValidationError

import based_operators as tk
from based_operators.crd import ResourceDefinitionError
from based_operators.handlers import (
    Diff,
    HandlerError,
    Patch,
    RawEvent,
    ResourceHandlers,
    handler_namespace,
)


class GreetSpec(BaseModel):
    """A small specification model."""

    message: str = "hello"
    replicas: int = 1


class GreetStatus(BaseModel):
    """A small status model."""

    phase: str = "Pending"


class Greeting(tk.CustomResource[GreetSpec, GreetStatus]):
    """A namespaced greeting resource with a status."""

    resource_config = tk.ResourceConfig(
        group="example.com",
        version="v1",
        kind="Greeting",
        plural="greetings",
        scope=tk.Scope.NAMESPACED,
    )


class Statusless(tk.CustomResource[GreetSpec, None]):
    """A status-less resource."""

    resource_config = tk.ResourceConfig(
        group="example.com",
        version="v1",
        kind="Statusless",
        plural="statuslesses",
        scope=tk.Scope.NAMESPACED,
    )


class Configless(tk.CustomResource[GreetSpec, None]):
    """A resource missing its ``resource_config``."""


BODY: dict[str, Any] = {
    "apiVersion": "example.com/v1",
    "kind": "Greeting",
    "metadata": {"name": "greeting-1", "namespace": "default", "uid": "uid-1"},
    "spec": {"message": "hi", "replicas": 3},
    "status": {"phase": "Ready"},
}


def _registry() -> kopf.OperatorRegistry:
    """Return a fresh, isolated kopf registry."""
    return kopf.OperatorRegistry()


def _registered(
    registry: kopf.OperatorRegistry,
    kind: str = "changing",
) -> list[Any]:
    """Return the handlers registered in one sub-registry of ``registry``."""
    subregistry = getattr(registry, f"_{kind}")
    return list(subregistry.get_all_handlers())


def _kwargs(**extra: Any) -> dict[str, Any]:
    """Return a kopf-like kwargs mapping for invoking a registered handler."""
    return {
        "body": dict(BODY),
        "patch": Patch(),
        "logger": logging.getLogger("test"),
        "resource": "kopf-resource-descriptor",
        "name": "greeting-1",
        "namespace": "default",
        "retry": 0,
        **extra,
    }


def test_namespace_is_shared_and_per_class() -> None:
    """The ``on`` namespace is stable per class and distinct between classes."""
    assert Greeting.on is Greeting.on
    assert Greeting.on is handler_namespace(Greeting)
    assert Greeting.on is not Statusless.on
    assert isinstance(Greeting.on, ResourceHandlers)


def test_public_aliases_come_from_kopf() -> None:
    """The public type aliases are kopf's own types."""
    assert Patch is kopf.Patch
    assert Diff is kopf.Diff
    assert RawEvent is kopf.RawEvent
    assert tk.Patch is kopf.Patch


@pytest.mark.parametrize(
    ("method", "kind", "options"),
    [
        ("create", "changing", {}),
        ("update", "changing", {}),
        ("delete", "changing", {}),
        ("resume", "changing", {}),
        ("field", "changing", {"field": "spec.replicas"}),
        ("event", "watching", {}),
        ("index", "indexing", {}),
        ("daemon", "spawning", {}),
        ("timer", "spawning", {"interval": 10}),
        ("validate", "webhooks", {}),
        ("mutate", "webhooks", {}),
    ],
)
def test_decorator_registers_with_kopf(method: str, kind: str, options: dict[str, Any]) -> None:
    """Every adapted decorator registers a handler with the right registry."""
    registry = _registry()

    def handler(**kwargs: Any) -> None:
        """A trivial handler."""

    decorator = getattr(Greeting.on, method)(registry=registry, **options)
    assert decorator(handler) is handler

    registered = _registered(registry, kind)
    assert len(registered) == 1
    assert registered[0].id.split("/")[0].rsplit(".", 1)[-1] == "handler"


def test_selector_is_derived_from_resource_config() -> None:
    """The kopf selector carries the resource's group, version and plural."""
    registry = _registry()
    Greeting.on.create(registry=registry)(lambda **kwargs: None)

    selector = _registered(registry)[0].selector
    assert selector.group == "example.com"
    assert selector.version == "v1"
    assert selector.plural == "greetings"


def test_bare_decorator_returns_the_handler_unchanged() -> None:
    """The bare form is the identity function, so decorators can be stacked."""
    registry = _registry()

    async def reconcile(resource: Greeting, **kwargs: Any) -> None:
        """A trivial handler."""

    decorated = Greeting.on.create(registry=registry)(reconcile)

    assert decorated is reconcile


def test_bare_decorator_registers_with_the_default_registry() -> None:
    """Applying the decorator bare registers with kopf's default registry."""

    async def bare_handler(resource: Greeting, **kwargs: Any) -> None:
        """A trivial handler."""

    decorated = Greeting.on.create(bare_handler)

    assert decorated is bare_handler
    registered = kopf.get_default_registry()._changing.get_all_handlers()
    assert any(handler.id.endswith("bare_handler") for handler in registered)


def test_stacked_decorators_register_every_handler() -> None:
    """Stacking two decorators registers both, wrapping the same function."""
    registry = _registry()

    @Greeting.on.create(registry=registry)
    @Greeting.on.delete(registry=registry)
    async def reconcile(resource: Greeting, **kwargs: Any) -> None:
        """A trivial handler."""

    registered = _registered(registry)
    assert len(registered) == 2
    assert {str(handler.reason) for handler in registered} == {"create", "delete"}
    assert all(handler.fn.__name__ == "reconcile" for handler in registered)
    # Each adapter wraps the original function, so the handlers are independent.
    assert registered[0].fn is not registered[1].fn


def test_options_are_forwarded_to_kopf() -> None:
    """Keyword options such as ``id`` and ``param`` reach kopf untouched."""
    registry = _registry()
    Greeting.on.create(id="custom", param={"k": 1}, registry=registry)(lambda **kwargs: None)

    handler = _registered(registry)[0]
    assert handler.id == "custom"
    assert handler.param == {"k": 1}


def test_field_option_is_parsed_by_kopf() -> None:
    """The ``field`` option is forwarded and parsed into a field path."""
    registry = _registry()
    Greeting.on.field(field="spec.replicas", registry=registry)(lambda **kwargs: None)

    assert _registered(registry)[0].field == ("spec", "replicas")


def test_async_handler_receives_typed_resource() -> None:
    """An async handler is invoked with the validated resource model."""
    registry = _registry()
    seen: dict[str, Any] = {}

    @Greeting.on.create(registry=registry)
    async def reconcile(resource: Greeting, patch: tk.Patch, logger: Any, **kwargs: Any) -> None:
        seen.update(resource=resource, patch=patch, logger=logger, kwargs=kwargs)

    handler = _registered(registry)[0]
    asyncio.run(handler.fn(**_kwargs()))

    resource = seen["resource"]
    assert isinstance(resource, Greeting)
    assert resource.spec.message == "hi"
    assert resource.spec.replicas == 3
    assert resource.status is not None
    assert resource.status.phase == "Ready"
    assert resource.metadata.name == "greeting-1"
    assert isinstance(seen["patch"], Patch)


def test_sync_handler_receives_typed_resource_and_field_values() -> None:
    """A sync field handler sees typed the resource and the raw field values."""
    registry = _registry()
    seen: dict[str, Any] = {}

    @Greeting.on.update(field="spec.replicas", registry=registry)
    def on_replicas(resource: Greeting, old: Any, new: Any, **kwargs: Any) -> None:
        seen.update(resource=resource, old=old, new=new)

    handler = _registered(registry)[0]
    handler.fn(**_kwargs(old=1, new=3))

    assert seen["resource"].spec.replicas == 3
    assert seen["old"] == 1
    assert seen["new"] == 3


def test_kopf_resource_descriptor_is_preserved_as_api_resource() -> None:
    """kopf's own ``resource`` descriptor moves to ``api_resource``."""
    registry = _registry()
    seen: dict[str, Any] = {}

    @Greeting.on.create(registry=registry)
    def reconcile(resource: Greeting, **kwargs: Any) -> None:
        seen.update(kwargs=kwargs)

    handler = _registered(registry)[0]
    handler.fn(**_kwargs())

    assert seen["kwargs"]["api_resource"] == "kopf-resource-descriptor"


def test_api_resource_is_absent_when_kopf_omits_it() -> None:
    """No ``api_resource`` is injected when kopf passed no descriptor."""
    registry = _registry()
    seen: dict[str, Any] = {}

    @Greeting.on.create(registry=registry)
    def reconcile(resource: Greeting, **kwargs: Any) -> None:
        seen.update(kwargs=kwargs)

    handler = _registered(registry)[0]
    kwargs = _kwargs()
    del kwargs["resource"]
    handler.fn(**kwargs)

    assert "api_resource" not in seen["kwargs"]


def test_body_is_accepted_as_a_kopf_body_mapping() -> None:
    """A kopf ``Body`` mapping validates just like a plain dictionary."""
    registry = _registry()
    seen: dict[str, Any] = {}

    @Greeting.on.create(registry=registry)
    def reconcile(resource: Greeting, **kwargs: Any) -> None:
        seen.update(resource=resource)

    handler = _registered(registry)[0]
    kwargs = _kwargs()
    kwargs["body"] = kopf.Body(cast("kopf.RawBody", dict(BODY)))
    handler.fn(**kwargs)

    assert seen["resource"].spec.replicas == 3


def test_statusless_resource_has_no_status() -> None:
    """A status-less resource parses without a status field."""
    registry = _registry()
    seen: dict[str, Any] = {}

    @Statusless.on.create(registry=registry)
    def reconcile(resource: Statusless, **kwargs: Any) -> None:
        seen.update(resource=resource)

    handler = _registered(registry)[0]
    assert handler.selector.plural == "statuslesses"
    kwargs = _kwargs()
    kwargs["body"] = {
        "apiVersion": "example.com/v1",
        "kind": "Statusless",
        "metadata": {"name": "statusless-1", "namespace": "default"},
        "spec": {"message": "hi", "replicas": 3},
    }
    handler.fn(**kwargs)

    assert not hasattr(seen["resource"], "status")


def test_handler_metadata_is_preserved() -> None:
    """The adapted handler keeps the function's name and docstring."""
    registry = _registry()

    @Greeting.on.create(registry=registry)
    async def reconcile(resource: Greeting, **kwargs: Any) -> None:
        """Reconcile a greeting."""

    handler = _registered(registry)[0]
    assert handler.fn.__name__ == "reconcile"
    assert handler.fn.__doc__ == "Reconcile a greeting."


def test_index_return_value_is_propagated() -> None:
    """The return value of an indexing handler reaches kopf."""
    registry = _registry()

    @Greeting.on.index(registry=registry)
    def index(resource: Greeting, **kwargs: Any) -> str:
        return resource.spec.message

    handler = _registered(registry, "indexing")[0]
    assert handler.fn(**_kwargs()) == "hi"


def test_event_handler_receives_raw_event_and_typed_resource() -> None:
    """An event handler gets the raw event kwargs and the validated resource."""
    registry = _registry()
    seen: dict[str, Any] = {}

    @Greeting.on.event(registry=registry)
    def watch(resource: Greeting, event: Any, **kwargs: Any) -> None:
        seen.update(resource=resource, event=event, **kwargs)

    handler = _registered(registry, "watching")[0]
    raw_event = {"type": "MODIFIED", "object": dict(BODY)}
    handler.fn(**_kwargs(type="MODIFIED", event=raw_event))

    assert seen["resource"].spec.replicas == 3
    assert seen["type"] == "MODIFIED"
    assert seen["event"] is raw_event


def test_daemon_and_timer_land_in_the_spawning_registry() -> None:
    """Daemons and timers are registered as spawning handlers."""
    registry = _registry()
    Greeting.on.daemon(registry=registry)(lambda **kwargs: None)
    Greeting.on.timer(interval=5, registry=registry)(lambda **kwargs: None)

    spawning = _registered(registry, "spawning")
    assert len(spawning) == 2
    assert {type(handler).__name__ for handler in spawning} == {"DaemonHandler", "TimerHandler"}


def test_missing_body_raises_handler_error() -> None:
    """Invoking an adapted handler without a body is a contract violation."""
    registry = _registry()
    Greeting.on.create(registry=registry)(lambda **kwargs: None)

    handler = _registered(registry)[0]
    with pytest.raises(HandlerError, match="without a 'body' argument"):
        handler.fn(patch=Patch(), logger=None)


def test_invalid_body_raises_validation_error() -> None:
    """An object that does not satisfy the model raises a validation error."""
    registry = _registry()
    Greeting.on.create(registry=registry)(lambda **kwargs: None)

    handler = _registered(registry)[0]
    kwargs = _kwargs()
    kwargs["body"] = {**BODY, "spec": {"message": "hi", "replicas": "many"}}
    with pytest.raises(ValidationError):
        handler.fn(**kwargs)


def test_non_callable_handler_is_rejected() -> None:
    """Passing something that is not a handler raises a clear error."""
    with pytest.raises(TypeError, match="expects a callable handler"):
        Greeting.on.create("not-a-handler")  # type: ignore[arg-type]


def test_resource_without_config_is_rejected_on_registration() -> None:
    """Accessing ``on`` is lazy; decorating a config-less resource fails."""
    assert isinstance(Configless.on, ResourceHandlers)

    with pytest.raises(ResourceDefinitionError):
        Configless.on.create(lambda **kwargs: None)
