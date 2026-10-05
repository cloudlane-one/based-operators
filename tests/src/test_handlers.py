"""Exercise model adapters through Kopf's actual invocation path."""

import asyncio
from typing import Literal

import kopf
import pytest
from kopf._core.actions.invocation import invoke
from pydantic import BaseModel

import based_operators as tk


class Spec(BaseModel):
    greeting: str


class Greeting(BaseModel):
    api_version: Literal["example.com/v1"] = "example.com/v1"
    kind: Literal["Greeting"] = "Greeting"
    metadata: dict = {}
    spec: Spec


BODY = {
    "apiVersion": "example.com/v1",
    "kind": "Greeting",
    "metadata": {"name": "hello"},
    "spec": {"greeting": "hi"},
}


def test_sync_kopf_invoke_no_kwargs_and_preserve_identity():
    """Register with the genuine Kopf registry; inject only declared parameters."""
    registry = kopf.OperatorRegistry()

    @tk.on.create(model=Greeting, registry=registry, id="greeting.create")
    def reconcile(resource: Greeting, spec: dict) -> str:
        assert isinstance(resource, Greeting)
        assert spec == BODY["spec"]
        return resource.spec.greeting

    handler = list(registry._changing.get_all_handlers())[0]
    assert handler.id == "greeting.create"
    assert handler.fn.__wrapped__ is reconcile
    result = asyncio.run(invoke(handler.fn, kwargs={"body": BODY, "spec": BODY["spec"]}))
    assert result == "hi"


def test_async_invalid_retries_then_recovers():
    """Invalid desired input never calls business logic but corrected input does."""
    registry = kopf.OperatorRegistry()
    calls: list[str] = []

    @tk.on.resume(model=Greeting, registry=registry, retry_delay=7)
    async def reconcile(resource: Greeting):
        calls.append(resource.spec.greeting)

    handler = list(registry._changing.get_all_handlers())[0]
    with pytest.raises(kopf.TemporaryError) as error:
        asyncio.run(invoke(handler.fn, kwargs={"body": {**BODY, "spec": {}}}))
    assert error.value.delay == 7
    assert not calls
    asyncio.run(invoke(handler.fn, kwargs={"body": BODY}))
    assert calls == ["hi"]


def test_business_validation_error_is_not_converted():
    """Only adapter validation errors are policy-controlled."""
    registry = kopf.OperatorRegistry()

    @tk.on.update(model=Greeting, registry=registry)
    def reconcile(resource: Greeting):
        Spec.model_validate({})

    handler = list(registry._changing.get_all_handlers())[0]
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        asyncio.run(invoke(handler.fn, kwargs={"body": BODY}))


def test_delete_invalid_fallback():
    """Delete cleanup still executes with an optional typed snapshot."""
    registry = kopf.OperatorRegistry()
    observed = []

    @tk.on.delete(model=Greeting, registry=registry)
    def cleanup(resource: Greeting | None, name: str):
        observed.append((resource, name))

    handler = list(registry._changing.get_all_handlers())[0]
    asyncio.run(invoke(handler.fn, kwargs={"body": {**BODY, "spec": {}}, "name": "hello"}))
    assert observed == [(None, "hello")]


def test_explicit_selector_conflict_and_plain_passthrough():
    """Model identity and plain Kopf registration remain distinct."""
    with pytest.raises(ValueError, match="conflicts"):
        tk.on.create(model=Greeting, group="other.example")
    assert tk.on.event("greetings") is not None


def test_status_patch_alias_and_unset():
    """Status merge retains JSON-compatible aliases and explicit nulls."""
    from pydantic import Field

    class Status(BaseModel):
        observed: str | None = Field(default=None, alias="observedValue")
        ignored: int = 1

    patch = {"status": {"other": 3}}
    tk.patch_status(patch, Status(observedValue=None))
    assert patch == {"status": {"other": 3, "observedValue": None}}


def test_daemon_waits_for_corrected_live_body():
    """A daemon starts only when live observed input becomes valid."""
    registry = kopf.OperatorRegistry()
    body = {**BODY, "spec": {}}

    class Stopped:
        def __init__(self):
            self.waits = 0

        def __bool__(self):
            return False

        def wait(self, timeout):
            self.waits += 1
            body["spec"] = {"greeting": "fixed"}

    stopped = Stopped()

    @tk.on.daemon(model=Greeting, registry=registry)
    def daemon(resource: Greeting, resource_context: tk.ResourceContext):
        assert resource.spec.greeting == "fixed"
        assert resource_context.refresh().spec.greeting == "fixed"
        body["spec"] = {"greeting": "updated"}
        return resource_context.refresh().spec.greeting

    handler = list(registry._spawning.get_all_handlers())[0]
    assert asyncio.run(invoke(handler.fn, kwargs={"body": body, "stopped": stopped})) == "updated"
    assert stopped.waits == 1
