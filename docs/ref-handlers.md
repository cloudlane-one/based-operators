# Handlers and reconciliation

`based_operators` wraps the resource-level decorators of
[kopf](https://kopf.readthedocs.io/) so that a handler works with the typed
resource classes from [Custom resources and CRDs](./ref-crd.md) instead of raw
dictionaries. Each resource class exposes one namespace, `<Resource>.on`, whose
members mirror kopf's decorators.

A handler registered this way is attached to kopf exactly like its `kopf.on`
counterpart, so kopf's discovery, filtering, retries and patching all keep
working; only what the handler receives changes.

For task-oriented guidance, see
[How to write handlers](./how-to-write-handlers.md).

## The available decorators

| `<Resource>.on.…` | kopf decorator | Purpose |
| --- | --- | --- |
| `create` | [`kopf.on.create`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.create) | An object was created while the operator is running. |
| `update` | [`kopf.on.update`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.update) | An essential field of an existing object changed. |
| `delete` | [`kopf.on.delete`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.delete) | Deletion was requested; use `optional=True` to not block it. |
| `resume` | [`kopf.on.resume`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.resume) | The object already existed when the operator (re)started. |
| `event` | [`kopf.on.event`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.event) | Every raw watch event, without cause detection. |
| `field` | [`kopf.on.field`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.field) | A single field changed; `field=` is required. |
| `index` | [`kopf.on.index`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.index) | Build an in-memory index; the return value is the index value. |
| `daemon` | [`kopf.on.daemon`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.daemon) | A long-running task tied to each object. |
| `timer` | [`kopf.on.timer`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.timer) | A periodic task; `interval=` is required. |
| `validate` | [`kopf.on.validate`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.validate) | Validating admission webhook. |
| `mutate` | [`kopf.on.mutate`](https://kopf.readthedocs.io/en/stable/packages/kopf.on/#kopf.on.mutate) | Mutating admission webhook. |

Each decorator may be used bare (`@<Resource>.on.create`) or called with kopf's
options (`@<Resource>.on.update(field="spec.replicas", param=10)`), and every
option is forwarded verbatim to the same-named `kopf.on` decorator. Only the
resource-level decorators are wrapped: `kopf.on.startup`, `kopf.on.cleanup`,
`kopf.on.login` and `kopf.on.probe` do not operate on custom resources and are
used directly from kopf.

## What a handler receives

kopf builds the keyword arguments; the adapter validates the raw `body` into the
concrete resource model and adds it as `resource`. Everything else passes through
unchanged.

| Keyword | Type | Description |
| --- | --- | --- |
| `resource` | `<Resource>` | The validated resource model, parsed from kopf's `body`. |
| `api_resource` | [`kopf.Resource`](https://kopf.readthedocs.io/en/stable/packages/kopf/#kopf.Resource) | kopf's own descriptor of the API resource, moved here because `resource` is taken. |
| `patch` | [`Patch`][based_operators.handlers.Patch] | The merge-patch applied after the handler: `patch.spec[...]`, `patch.status[...]`, `patch.metadata[...]`. |
| `logger` | `logging.Logger` | The per-object logger kopf provides. |
| `old` / `new` / `diff` | `Any` | The detected changes; raw bodies for whole-object handlers, field values for `field=` handlers. |
| `body`, `meta`, `spec`, `status` | mappings | kopf's raw, unvalidated views of the object, unchanged. |
| `param`, `retry`, `started`, `runtime`, `memo` | `Any` | kopf's standard arguments; see kopf's [Arguments](https://kopf.readthedocs.io/en/stable/kwargs/) page. |
| `**kwargs` | `Any` | Anything else kopf adds in future releases — always accept it. |

`body`, `meta`, `spec` and `status` remain available for the rare handlers that
want kopf's raw views; ordinary reconciling logic reads the typed `resource`
instead.

If the object does not satisfy the model, the handler raises
[`pydantic.ValidationError`](https://docs.pydantic.dev/latest/errors/validation_errors/).
Because the CRD schema admits only objects that the same models accept, this
should never happen in practice; if it does, it means the cluster and the
operator's models have diverged.

## Differences from plain kopf

| Aspect | kopf | `based_operators` |
| --- | --- | --- |
| Object argument | `body`, a read-only mapping | `resource`, a validated `CustomResource` instance |
| Resource selector | passed to the decorator | derived from `resource_config` |
| Resource descriptor | `resource` | `api_resource` |
| Handler shape | free knot of `**kwargs` | `(resource, patch, logger, **kwargs)` by convention |
| Everything else | — | identical: same decorators, options, causes and patching |

The adapter returns the handler function unchanged (as kopf's decorators do), so
decorators stack freely and the decorated name stays the plain Python function.

## Public API

| Symbol | Purpose |
| --- | --- |
| [`handler_namespace`][based_operators.handlers.handler_namespace] | Return the shared `.on` namespace of a resource class. |
| [`ResourceHandlers`][based_operators.handlers.ResourceHandlers] | The namespace type behind `<Resource>.on`. |
| [`Patch`][based_operators.handlers.Patch] | kopf's merge-patch type, re-exported. |
| [`Diff`][based_operators.handlers.Diff] | kopf's diff type, re-exported. |
| [`RawEvent`][based_operators.handlers.RawEvent] | kopf's raw watch-event type, re-exported. |
| [`HandlerError`][based_operators.handlers.HandlerError] | Raised when an adapted handler cannot be invoked. |