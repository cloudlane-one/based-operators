---
hide:
- navigation
---
# Based Operators Docs

Welcome! 👋

**Based Operators** is a model-first Python operator toolkit. Its first module,
[`based_operators.crd`](./ref-crd.md), provides typed Pydantic custom-resource
models and generates Kubernetes CRD manifests from Pydantic's own JSON Schema
output. Its second module, [`based_operators.handlers`](./ref-handlers.md), wraps
kopf's resource-level decorators so that handlers receive those typed models under
`<Resource>.on`.

Start with the guides:

- [How to define custom resources](./how-to-define-custom-resources.md)
- [How to write handlers](./how-to-write-handlers.md)

The reference pages, [custom resources and CRDs](./ref-crd.md) and
[handlers and reconciliation](./ref-handlers.md), document the full API, the
supported constructs and the limitations.
