"""Exercise model adapters through Kopf's actual invocation path."""

import asyncio
import inspect
from types import SimpleNamespace
from typing import Literal, cast

import kopf
import pytest
from kopf._core.actions.invocation import invoke
from pydantic import BaseModel

import based_operators as tk
import based_operators.handlers as handler_module


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
    assert getattr(handler.fn, "__wrapped__") is reconcile
    result = asyncio.run(invoke(handler.fn, kwargs={"body": BODY, "spec": BODY["spec"]}))
    assert result == "hi"


def test_handler_signature_is_cached_at_registration(monkeypatch):
    """Invocation reuses signature metadata computed during registration."""
    registry = kopf.OperatorRegistry()
    signature_calls = 0

    def signature(fn):
        nonlocal signature_calls
        signature_calls += 1
        return inspect.signature(fn)

    monkeypatch.setattr(
        handler_module,
        "inspect",
        SimpleNamespace(
            signature=signature,
            iscoroutinefunction=inspect.iscoroutinefunction,
            Parameter=inspect.Parameter,
        ),
    )

    @tk.on.create(model=Greeting, registry=registry)
    def reconcile(resource: Greeting, spec: dict):
        return resource.spec.greeting

    handler = list(registry._changing.get_all_handlers())[0]
    assert signature_calls == 1
    monkeypatch.setattr(
        handler_module,
        "inspect",
        SimpleNamespace(signature=lambda _: pytest.fail("signature inspected during invocation")),
    )
    assert handler.fn(body=BODY, spec=BODY["spec"]) == "hi"


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


def test_status_patch_rejects_non_dict_status():
    """Status merge reports malformed existing status patches clearly."""

    class Status(BaseModel):
        observed: str | None = None

    with pytest.raises(TypeError, match=r"patch\['status'\] must be a dict"):
        tk.patch_status({"status": None}, Status(observed="ready"))


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
        assert cast(Greeting, resource_context.refresh()).spec.greeting == "fixed"
        body["spec"] = {"greeting": "updated"}
        return cast(Greeting, resource_context.refresh()).spec.greeting

    handler = list(registry._spawning.get_all_handlers())[0]
    assert asyncio.run(invoke(handler.fn, kwargs={"body": body, "stopped": stopped})) == "updated"
    assert stopped.waits == 1


def test_field_scoped_old_new_stay_raw():
    """Field selection old/new retain Kopf's original scalar values."""
    registry = kopf.OperatorRegistry()

    @tk.on.field(model=Greeting, field="spec.greeting", registry=registry)
    def changed(resource: Greeting, old: str, new: str):
        return resource.spec.greeting, old, new

    handler = list(registry._changing.get_all_handlers())[0]
    assert asyncio.run(invoke(handler.fn, kwargs={
        "body": BODY, "old": "old", "new": "hi", "diff": (),
    })) == ("hi", "old", "hi")


@pytest.mark.parametrize("category", ["event", "index"])
def test_nonretry_handlers_skip_invalid_input(category: str):
    """Invalid watch/index input is skipped without temporary retry errors."""
    registry = kopf.OperatorRegistry()
    calls = []

    @getattr(tk.on, category)(model=Greeting, registry=registry)
    def handle(resource: Greeting):
        calls.append(resource.spec.greeting)
        return {"ok": True}

    name = "_watching" if category == "event" else "_indexing"
    handler = list(getattr(registry, name).get_all_handlers())[0]
    assert asyncio.run(invoke(handler.fn, kwargs={"body": {**BODY, "spec": {}}})) is None
    assert asyncio.run(invoke(handler.fn, kwargs={"body": BODY})) == {"ok": True}
    assert calls == ["hi"]


@pytest.mark.parametrize("category", ["validate", "mutate"])
def test_admission_invalid_rejected(category: str):
    """Invalid submitted admission input receives an immediate response."""
    registry = kopf.OperatorRegistry()

    @getattr(tk.on, category)(model=Greeting, registry=registry)
    def admission(resource: Greeting):
        return None

    handler = list(registry._webhooks.get_all_handlers())[0]
    with pytest.raises(kopf.AdmissionError) as error:
        asyncio.run(invoke(handler.fn, kwargs={"new": {**BODY, "spec": {}}}))
    assert error.value.code == 422


def test_kopf_public_api_inventory_is_reexported():
    """Keep Kopf's declared public exports available through the facade."""
    assert set(kopf.__all__) <= set(dir(tk))
    assert not hasattr(tk, "upstream")
    assert tk.Patch is kopf.Patch


def test_stale_status_does_not_block_current_spec():
    """A defaulted status field tolerates stale observed status."""
    from based_operators.validation import validate_resource

    class WithStatus(Greeting):
        status: Spec | None = None

    valid = validate_resource(WithStatus, {**BODY, "status": {"greeting": "observed"}})
    assert cast(WithStatus, valid).status == Spec(greeting="observed")
    stale = validate_resource(WithStatus, {**BODY, "status": {"invalid": True}})
    assert cast(WithStatus, stale).status is None
