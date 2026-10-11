# Based Operators

A model-first Python operator toolkit. The first module, `based_operators.crd`,
provides typed [Pydantic](https://docs.pydantic.dev/) custom-resource models and
generates `apiextensions.k8s.io/v1` CustomResourceDefinition manifests from
Pydantic's own JSON Schema output. The second, `based_operators.handlers`, wraps
[kopf](https://kopf.readthedocs.io/)'s resource-level decorators so that handlers
work with those typed models instead of raw dictionaries.

The adapter is deliberately thin: it makes only the dialect changes Kubernetes
requires and refuses anything it cannot represent faithfully, so no validation
semantics are lost silently.

## Quickstart

```python
from pydantic import BaseModel

from based_operators.crd import CustomResource, ResourceConfig, Scope, crd_yaml


class WidgetSpec(BaseModel):
    replicas: int = 1
    message: str


class Widget(CustomResource[WidgetSpec, None]):  # None => status-less resource
    resource_config = ResourceConfig(
        group="example.com",
        version="v1",
        kind="Widget",
        plural="widgets",
        scope=Scope.NAMESPACED,
    )


print(crd_yaml(Widget))
```

`crd_yaml()` renders YAML, which needs the optional extra — `pip install
"based-operators[yaml]"`. Without it, use `crd_manifest(Widget)`, which returns the
same manifest as a plain dictionary.

See [How to define custom resources](./docs/how-to-define-custom-resources.md) to
get started, and the [custom resources reference](./docs/ref-crd.md) for the full
API, the supported-construct table and the limitations.

## Reconciliation

Handlers are attached to a resource through its `on` namespace, which mirrors
kopf's decorators and derives the resource selector from the resource config:

```python
import based_operators as bk

from .models import Greeting


@Greeting.on.create
@Greeting.on.resume
async def reconcile(resource: Greeting, patch: bk.Patch, logger, **kwargs) -> None:
    desired = resource.spec  # a typed GreetingSpec, never a raw dict
```

See [How to write handlers](./docs/how-to-write-handlers.md) for worked examples,
and the [handlers reference](./docs/ref-handlers.md) for the decorator list and the
arguments a handler receives.

## Public API

- `CustomResource`, `ResourceConfig`, `Scope`, `ObjectMeta`
- `source_json_schema`, `kubernetes_schema`, `crd_manifest`, `crd_yaml`, `to_yaml`
- `ResourceHandlers`, `handler_namespace`, `Patch`, `Diff`, `RawEvent`, `HandlerError`

## Development

This project follows the conventions of the repository template it was bootstrapped
from. See the [contribution guide](./docs/how-to-contribute.md) and the
[local setup guide](./docs/how-to-setup-locally.md) to get started.

```shell
uv sync          # install dependencies
uv run pytest    # run the test suite
uv run ruff check .
uv run pyright .
uv run vtr build-docs
```

The version-pinned API-server integration suite (`tests/src/test_integration.py`)
is skipped unless `BASED_OPERATORS_K8S_INTEGRATION=1` is set and a cluster is
reachable through `kubectl`.
