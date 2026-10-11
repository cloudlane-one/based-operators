---
hide:
- navigation
---

# Based Operators

<img src="img/logo.svg" alt="Based Operators" width="104" align="right">

**Build a Kubernetes operator out of Pydantic models and Python, and ship it as
an image and a Helm chart.**

Based Operators is a model-first toolkit for custom operators. A custom resource
is a Pydantic v2 model; its behaviour is Python code decorated with kopf's
handlers; and the CRDs, the container image and the Helm chart that put both on a
cluster are derived from them rather than written by hand.

<div class="grid cards" markdown>

-   **Custom resources**

    ---

    Describe the `spec` and `status` as Pydantic models and generate an
    `apiextensions.k8s.io/v1` CustomResourceDefinition from them.

    [How to define custom resources :octicons-arrow-right-24:](how-to-define-custom-resources.md)

    [Reference :octicons-arrow-right-24:](ref-crd.md)

-   **Reconciliation**

    ---

    Attach handlers to `<Resource>.on` and receive the validated resource
    instead of a raw dictionary.

    [How to write handlers :octicons-arrow-right-24:](how-to-write-handlers.md)

    [Reference :octicons-arrow-right-24:](ref-handlers.md)

-   **Deployment**

    ---

    Scaffold the Dockerfile, the CRDs and the Helm chart that deploy the
    operator, with health checks and optionally admission webhooks.

    [How to scaffold a deployment :octicons-arrow-right-24:](how-to-scaffold-a-deployment.md)

    [CLI reference :octicons-arrow-right-24:](ref-cli.md) ·
    [Webhook module :octicons-arrow-right-24:](ref-webhook.md)

</div>

## How the pieces fit together

1. **Models** — `based_operators.crd` turns Pydantic's own JSON Schema into the
   Kubernetes structural dialect, and refuses constructs it cannot represent
   faithfully rather than weakening them.
2. **Handlers** — `based_operators.handlers` wraps kopf's resource-level
   decorators so a handler works with the typed model. Discovery, filtering,
   retries and patching stay kopf's.
3. **Packaging** — `based-operators scaffold` (the `based-operators` console
   script, defined in `based_ops_cli.__main__`, with its logic in
   [`based_operators.scaffold`](ref-python-api.md)) loads an operator entrypoint
   exactly as `kopf run` does, writes one CRD per resource it declares, and copies
   a static Helm chart that installs both. Admission webhooks come from
   [`based_operators.webhook`](ref-webhook.md), which the generated image imports.

Start with [how to define custom resources](how-to-define-custom-resources.md),
then [how to write handlers](how-to-write-handlers.md), then
[how to scaffold a deployment](how-to-scaffold-a-deployment.md).
