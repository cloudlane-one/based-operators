# Based Operators

A model-first Python operator toolkit. The first module, `based_operators.crd`,
provides typed [Pydantic](https://docs.pydantic.dev/) custom-resource models and
generates `apiextensions.k8s.io/v1` CustomResourceDefinition manifests from
Pydantic's own JSON Schema output.

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

See the [custom resources and CRD guide](./docs/crd-api.md) for the full API, the
supported-feature table and the limitations.

## Public API

- `CustomResource`, `ResourceConfig`, `Scope`, `ObjectMeta`
- `source_json_schema`, `kubernetes_schema`, `crd_manifest`, `crd_yaml`, `to_yaml`

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
