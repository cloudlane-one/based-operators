"""Exercise model adapters through Kopf's actual invocation path."""

import asyncio
import functools
import inspect
from types import SimpleNamespace
from typing import Literal, cast

import kopf
import pytest
from kopf._core.actions.invocation import invoke
from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

import based_operators as tk
import based_operators.handlers as handler_module


class Spec(BaseModel):
    greeting: str


class Greeting(BaseModel):
    api_version: Literal["example.com/v1"] = "example.com/v1"
    kind: Literal["Greeting"] = "Greeting"
    metadata: dict = Field(default_factory=dict)
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
    assert asyncio.run(invoke(handler.fn, kwargs={"body": BODY, "spec": BODY["spec"]})) == "hi"


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


def test_status_patch_preserves_pending_nested_updates():
    """Repeated status patches retain sibling keys and explicit null deletions."""

    class Details(BaseModel):
        first: str | None = None
        second: str | None = None

    class Status(BaseModel):
        details: Details

    patch = {"status": {"details": {"first": "old"}}}
    tk.patch_status(patch, Status(details=Details(second="new")))
    assert patch == {"status": {"details": {"first": "old", "second": "new"}}}
    tk.patch_status(patch, Status(details=Details(first=None)))
    assert patch == {"status": {"details": {"first": None, "second": "new"}}}


def test_status_patch_rejects_non_dict_status():
    """Status merge reports malformed existing status patches clearly."""

    class Status(BaseModel):
        observed: str | None = None

    with pytest.raises(TypeError, match=r"patch\['status'\] must be a dict"):
        tk.patch_status({"status": None}, Status(observed="ready"))


def test_status_patch_rejects_non_dict_patch():
    """Status merge reports a malformed patch clearly."""

    class Status(BaseModel):
        observed: str | None = None

    with pytest.raises(TypeError, match="patch must be a dict"):
        tk.patch_status(None, Status(observed="ready"))


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


def test_async_daemon_waits_for_corrected_live_body():
    """An async stop flag with a no-argument wait permits retries after a timeout."""
    registry = kopf.OperatorRegistry()
    body = {**BODY, "spec": {}}

    class Stopped(asyncio.Event):
        def __bool__(self):
            return self.is_set()

        async def wait(self):
            body["spec"] = {"greeting": "fixed"}
            return await super().wait()

    @tk.on.daemon(model=Greeting, registry=registry, retry_delay=0.001)
    async def daemon(resource: Greeting):
        return resource.spec.greeting

    handler = list(registry._spawning.get_all_handlers())[0]
    assert asyncio.run(invoke(handler.fn, kwargs={"body": body, "stopped": Stopped()})) == "fixed"


def test_wrapped_async_daemon_registers_async_wrapper():
    """Sync @wraps decorators around async daemons still register async wrappers."""
    registry = kopf.OperatorRegistry()
    body = {**BODY, "spec": {}}

    def sync_wrapper_decorator(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            return fn(*args, **kwargs)

        return wrapped

    class Stopped(asyncio.Event):
        def __bool__(self):
            return self.is_set()

        async def wait(self):
            body["spec"] = {"greeting": "fixed"}
            return await super().wait()

    @tk.on.daemon(model=Greeting, registry=registry, retry_delay=0.001)
    @sync_wrapper_decorator
    async def daemon(resource: Greeting):
        return resource.spec.greeting

    handler = list(registry._spawning.get_all_handlers())[0]
    assert handler_module._is_async_fn(handler.fn)
    assert inspect.iscoroutinefunction(handler.fn)
    assert asyncio.run(invoke(handler.fn, kwargs={"body": body, "stopped": Stopped()})) == "fixed"


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
    def admission(resource: Greeting | None):
        return None

    handler = list(registry._webhooks.get_all_handlers())[0]
    with pytest.raises(kopf.AdmissionError) as error:
        asyncio.run(invoke(handler.fn, kwargs={"new": {**BODY, "spec": {}}}))
    assert error.value.code == 422


@pytest.mark.parametrize("category", ["validate", "mutate"])
def test_admission_delete_injects_none_resource(category: str):
    """DELETE admission invokes model-aware handlers with no resource snapshot."""
    registry = kopf.OperatorRegistry()
    observed = []

    @getattr(tk.on, category)(model=Greeting, registry=registry)
    def admission(resource: Greeting | None):
        observed.append(resource)

    handler = list(registry._webhooks.get_all_handlers())[0]
    asyncio.run(invoke(handler.fn, kwargs={"new": None}))
    assert observed == [None]


def test_kopf_public_api_inventory_is_reexported():
    """Keep Kopf's declared public exports available through the facade."""
    assert set(kopf.__all__) <= set(dir(tk))
    assert not hasattr(tk, "upstream")
    assert tk.Patch is kopf.Patch
    for name in ("on", "ResourceContext", "patch_status"):
        assert name in tk.__all__
        assert hasattr(tk, name)


def test_stale_status_does_not_block_current_spec():
    """A defaulted status field tolerates stale observed status."""
    from based_operators.validation import validate_resource

    class WithStatus(Greeting):
        status: Spec | None = None

    valid = validate_resource(WithStatus, {**BODY, "status": {"greeting": "observed"}})
    assert cast(WithStatus, valid).status == Spec(greeting="observed")
    stale = validate_resource(WithStatus, {**BODY, "status": {"invalid": True}})
    assert cast(WithStatus, stale).status is None


def test_namespace_is_synthesized_from_metadata_by_default():
    """A plain namespace field is populated from metadata.namespace."""
    from based_operators.validation import validate_resource

    class RequiredNamespaceGreeting(Greeting):
        namespace: str

    resource = validate_resource(
        RequiredNamespaceGreeting,
        {
            **BODY,
            "metadata": {"name": "hello", "namespace": "operator-system"},
        },
    )

    assert cast(RequiredNamespaceGreeting, resource).namespace == "operator-system"


def test_namespace_alias_to_metadata_does_not_fail_extra_forbid():
    """A metadata-backed namespace alias does not synthesize an extra top-level key."""
    from based_operators.validation import validate_resource

    class Metadata(BaseModel):
        name: str
        namespace: str

    class AliasedNamespaceGreeting(BaseModel):
        model_config = ConfigDict(extra="forbid")
        api_version: Literal["example.com/v1"] = Field(validation_alias="apiVersion")
        kind: Literal["Greeting"]
        metadata: Metadata
        namespace: str = Field(validation_alias=AliasPath("metadata", "namespace"))
        spec: Spec

    resource = validate_resource(
        AliasedNamespaceGreeting,
        {
            **BODY,
            "metadata": {"name": "hello", "namespace": "operator-system"},
        },
    )

    validated = cast(AliasedNamespaceGreeting, resource)
    assert validated.metadata.namespace == "operator-system"
    assert validated.namespace == "operator-system"


def test_spec_defaults_and_explicit_null_are_distinguished():
    """An omitted spec uses its model default, but explicit null is invalid."""
    from based_operators.validation import InvalidDesiredInputError, validate_resource

    class DefaultedSpec(Greeting):
        spec: Spec = Field(default_factory=lambda: Spec(greeting="default"))

    body_without_spec = {key: value for key, value in BODY.items() if key != "spec"}
    valid = validate_resource(DefaultedSpec, body_without_spec)
    assert cast(DefaultedSpec, valid).spec == Spec(greeting="default")

    with pytest.raises(InvalidDesiredInputError):
        validate_resource(DefaultedSpec, {**BODY, "spec": None})


def test_omitted_spec_with_validation_alias_is_invalid_desired_input():
    """A missing required spec is classified through its validation alias."""
    from based_operators.validation import InvalidDesiredInputError, validate_resource

    class AliasedSpec(Greeting):
        spec: Spec = Field(validation_alias=AliasChoices("desired", "spec"))

    body_without_spec = {key: value for key, value in BODY.items() if key != "spec"}
    with pytest.raises(InvalidDesiredInputError) as error:
        validate_resource(AliasedSpec, body_without_spec)
    assert error.value.locations == [("desired",)]


def test_spec_field_validator_is_classified_as_invalid_desired_input():
    """Resource-level spec validators use the same invalid-input policy."""
    from based_operators.validation import InvalidDesiredInputError, validate_resource

    class ValidatedSpec(Greeting):
        @field_validator("spec")
        @classmethod
        def reject_spec(cls, spec: Spec) -> Spec:
            if spec.greeting == "reject":
                raise ValueError("spec rejected")
            return spec

    with pytest.raises(InvalidDesiredInputError):
        validate_resource(ValidatedSpec, {**BODY, "spec": {"greeting": "reject"}})


def test_unrelated_envelope_errors_are_not_classified_as_invalid_spec():
    """Unrelated model errors propagate even when the spec is also invalid."""
    from based_operators.validation import validate_resource

    with pytest.raises(ValidationError):
        validate_resource(Greeting, {**BODY, "metadata": "wrong", "spec": {}})
