# How to write handlers

This guide attaches handlers to a typed custom resource defined as in
[How to define custom resources](./how-to-define-custom-resources.md). For the
full decorator list, the arguments a handler receives and the public API, see the
[handlers reference](./ref-handlers.md).

## Attach a handler to a resource

Every resource class exposes an `on` namespace whose members mirror kopf's
resource-level decorators. Decorate a function with one or more of them; the
resource selector is derived from the class's
[`ResourceConfig`][based_operators.crd.ResourceConfig], so it is never passed by
hand:

```python exec="on" session="how-to-handlers" source="block"
from enum import StrEnum
from typing import Any

import based_operators as bk
from pydantic import BaseModel


class Phase(StrEnum):
    PENDING = "Pending"
    READY = "Ready"


class GreetingSpec(BaseModel):
    message: str
    replicas: int = 1


class GreetingStatus(BaseModel):
    phase: Phase = Phase.PENDING


class Greeting(bk.CustomResource[GreetingSpec, GreetingStatus]):
    resource_config = bk.ResourceConfig(
        group="example.com",
        version="v1",
        kind="Greeting",
        plural="greetings",
        scope=bk.Scope.NAMESPACED,
    )


@Greeting.on.create
@Greeting.on.resume
async def reconcile(resource: Greeting, patch: bk.Patch, logger: Any, **kwargs: Any) -> None:
    """Bring a greeting object in line with its spec."""
    if resource.spec.replicas > 1:
        patch.status["phase"] = Phase.READY


print("handlers attached to", Greeting.__name__)
```

Stacking works because each decorator returns the function unchanged, so
`reconcile` stays a plain Python function that is registered once per cause.
Decorators may also be called with kopf's options instead of used bare:
`@Greeting.on.update(field="spec.replicas", param=10)`.

## Patch the object from a handler

A handler changes the resource by writing into the
[`Patch`][based_operators.handlers.Patch] it receives; kopf applies the
accumulated changes as a JSON merge-patch once the handler returns. Setting a key
to `None` deletes it, and `patch.fns` accepts transformation functions for
list-based edits that a merge-patch cannot express.

Registering into an isolated
[`kopf.OperatorRegistry`](https://kopf.readthedocs.io/en/stable/packages/kopf/#kopf.OperatorRegistry)
lets you exercise the handler without a cluster; this is exactly the call kopf
makes at runtime:

```python exec="on" session="how-to-handlers" source="block"
import asyncio
import logging

import kopf

registry = kopf.OperatorRegistry()
patch = bk.Patch()


@Greeting.on.create(registry=registry)
async def on_create(resource: Greeting, patch: bk.Patch, logger: Any, **kwargs: Any) -> None:
    patch.status["phase"] = Phase.READY


handler = registry._changing.get_all_handlers()[0]
print("selector:", handler.selector)

asyncio.run(
    handler.fn(
        body={
            "apiVersion": "example.com/v1",
            "kind": "Greeting",
            "metadata": {"name": "greeting-1", "namespace": "default"},
            "spec": {"message": "hello", "replicas": 2},
        },
        patch=patch,
        logger=logging.getLogger("example"),
    )
)
print("patch:", dict(patch))
```

## React to a single field change

Use `on.field` (or `on.update` with `field=`) to react to one path instead of the
whole object. kopf parses the path and passes the field's previous and current
values as `old` and `new`; every other option is forwarded to kopf verbatim:

```python exec="on" session="how-to-handlers" source="block"
revised = kopf.OperatorRegistry()
seen: dict[str, Any] = {}


@Greeting.on.update(field="spec.replicas", registry=revised)
def replicas_changed(resource: Greeting, old: Any, new: Any, **kwargs: Any) -> None:
    seen.update(message=resource.spec.message, old=old, new=new)


scaled = revised._changing.get_all_handlers()[0]
print("watching:", ".".join(scaled.field))

scaled.fn(
    body={
        "apiVersion": "example.com/v1",
        "kind": "Greeting",
        "metadata": {"name": "greeting-1", "namespace": "default"},
        "spec": {"message": "hello", "replicas": 3},
    },
    patch=bk.Patch(),
    logger=None,
    old=2,
    new=3,
)
print("observed:", seen)
```

`old` and `new` are raw values — a field value here, a whole body for
whole-object handlers — so validate them with `Greeting.model_validate` when the
typed form is needed.

## Validate or mutate at admission time

`on.validate` and `on.mutate` register admission webhooks. Both receive the
candidate object as `resource`; a failing validation raises
[`kopf.AdmissionError`](https://kopf.readthedocs.io/en/stable/packages/kopf/#kopf.AdmissionError),
and a mutation is written into `patch`:

```python exec="on" session="how-to-handlers" source="block"
admission = kopf.OperatorRegistry()


@Greeting.on.validate(registry=admission)
async def require_message(resource: Greeting, **kwargs: Any) -> None:
    if not resource.spec.message.strip():
        raise kopf.AdmissionError("spec.message must not be blank")


@Greeting.on.mutate(registry=admission)
async def default_replicas(resource: Greeting, patch: bk.Patch, **kwargs: Any) -> None:
    if resource.spec.replicas < 1:
        patch.spec["replicas"] = 1


print("webhooks:", [handler.id for handler in admission._webhooks.get_all_handlers()])
```

!!! note "Side effects"
    Admission handlers must stay free of side effects unless they declare
    `side_effects=True`, and must respect the `dryrun` flag kopf passes them.

## Next steps

- [Handlers and reconciliation](./ref-handlers.md) — every decorator, every
  argument a handler receives, and the differences from plain kopf.
- kopf's own [handlers](https://kopf.readthedocs.io/en/stable/handlers/) and
  [patching](https://kopf.readthedocs.io/en/stable/patches/) pages apply unchanged
  to every option these decorators forward.