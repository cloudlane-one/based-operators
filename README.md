# Based Operators

**Build a Kubernetes operator out of Pydantic models and Python, and ship it as
an image and a Helm chart.**

Based Operators is a model-first toolkit for custom operators. You describe a
custom resource with [Pydantic](https://docs.pydantic.dev/) v2 models, you write
its reconciliation in ordinary Python with [kopf](https://kopf.readthedocs.io/),
and everything a cluster needs is derived from those two things:

| What you write | What you get |
| --- | --- |
| a `BaseModel` for the resource's **spec**, and optionally one for its **status** | an `apiextensions.k8s.io/v1` CustomResourceDefinition, generated from Pydantic's own JSON Schema |
| handlers registered under `<Resource>.on` | kopf handlers that receive your typed model instead of a raw dictionary |
| one Python file that declares those handlers | a CRD-complete Helm chart, a Dockerfile and an operator entrypoint, ready to build and install |

The adapter in between is deliberately thin: it makes only the dialect changes
Kubernetes requires and refuses anything it cannot represent faithfully, so no
validation semantics are lost silently.

## Define a resource

```python
from pydantic import BaseModel, Field

import based_operators as bk


class WidgetSpec(BaseModel):
    message: str = Field(min_length=1)
    replicas: int = Field(default=1, ge=0, le=10)


class WidgetStatus(BaseModel):
    ready: int = 0


class Widget(bk.CustomResource[WidgetSpec, WidgetStatus]):
    resource_config = bk.ResourceConfig(
        group="example.com",
        version="v1",
        kind="Widget",
        plural="widgets",
        scope=bk.Scope.NAMESPACED,
    )
```

`crd_manifest(Widget)` returns the CustomResourceDefinition as a dictionary, and
`crd_yaml(Widget)` renders the same manifest as YAML (needs the `yaml` extra).

→ [How to define custom resources](./docs/how-to-define-custom-resources.md) ·
[Custom resources and CRDs](./docs/ref-crd.md)

## Reconcile it

```python
@Widget.on.create
@Widget.on.resume
async def reconcile(resource: Widget, patch: bk.Patch, logger, **kwargs) -> None:
    patch.status["ready"] = resource.spec.replicas
```

The decorator attaches the handler to kopf exactly like its `kopf.on`
counterpart, but the handler receives the validated model as `resource` instead
of a mapping, and the resource selector comes from the `ResourceConfig`.

→ [How to write handlers](./docs/how-to-write-handlers.md) ·
[Handlers and reconciliation](./docs/ref-handlers.md)

## Ship it

The `based-operators` command ships with the distribution and drives the
scaffolder in `based_operators.scaffold`:

```shell
uvx based-operators scaffold operator.py -o deploy --webhook
docker build -f deploy/Dockerfile -t ghcr.io/acme/widget-operator:v1 .
docker push ghcr.io/acme/widget-operator:v1
helm install widget-operator ./deploy/helm/based-operator --create-namespace
```

`scaffold` loads the entry point the way `kopf run` loads it, derives one CRD per
resource it declares, and writes a Dockerfile, a CRD-complete Helm chart and the
CRDs. The generated image runs `kopf run <entrypoint> --liveness=...`, which is
the endpoint the chart's liveness probe calls; `--webhook` adds the TLS and
cert-manager plumbing, backed by the `based_operators.webhook` module that ships
with the library.

→ [How to scaffold a deployment](./docs/how-to-scaffold-a-deployment.md) ·
[Command-line reference](./docs/ref-cli.md)

## Install

```shell
pip install based-operators   # the library, the scaffolder and the `based-operators` CLI
```

The package can also be run without installing it, straight from the index:

```shell
uvx based-operators scaffold operator.py -o deploy
```

## Development

This project follows the conventions of the repository template it was
bootstrapped from. See the [contribution guide](./docs/how-to-contribute.md) and
the [local setup guide](./docs/how-to-setup-locally.md) to get started.

```shell
uv sync          # install dependencies
uv run pytest    # run the test suite
uv run ruff check .
uv run pyright .
uv run vtr build-docs
```

The end-to-end integration suite (`tests/test_integration.py`) is skipped unless
`BASED_OPERATORS_K8S_INTEGRATION=1` is set and a cluster is reachable through
`kubectl`; it builds the operator image and deploys it, so it also needs `docker`
and `helm`.
