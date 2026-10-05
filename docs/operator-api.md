# Operator API and compatibility

`import based_operators as tk` exposes Kopf 1.44.6's top-level public exports.
Downstream projects can also `import kopf` directly for the unmodified API.
`tk.on` delegates all registration to Kopf. Without `model=`, registration
is the original Kopf call.

| Kopf decorators | Model-aware behavior |
| --- | --- |
| `on.create`, `on.resume`, `on.update`, `on.field`, `on.timer` | Validate desired spec, inject a detached `resource` snapshot; retry invalid desired specs with `TemporaryError`. |
| `on.delete` | Invalid desired spec injects `None` for `resource` and permits cleanup. Declare `resource: MyModel \| None`, or use `strict_delete=True` (which can block deletion). |
| `on.daemon` | Wait for valid desired input before initial invocation; optionally request `resource_context` for live refresh. |
| `on.event`, `on.index` | Invalid input skips invocation, returning `None` (Kopf does not retry events). |
| `on.validate`, `on.mutate` | Invalid submitted input raises `AdmissionError(422)`; deletion with no new object passes through. Deployment requires a separately configured Kopf webhook server, Service and certificates. |
| `on.startup`, `on.cleanup`, `on.login`, `on.probe`, `on.subhandler`, `on.register` | Unchanged Kopf functions; no resource-model input. |
| `tk.daemon`, `tk.timer`, `tk.index`, `tk.register`, other top-level exports | Unchanged Kopf functions; use `tk.on.*` for model support. |

Selectors inferred from `model=` share kdantic's group, version, kind and
plural resolution with CRD generation. Explicit conflicting selectors or
positional selectors alongside `model=` are rejected. kdantic infers `Cluster`
scope for models without a `namespace` field (root or metadata model); its
default group/version are `example.com/v1`. Set actual identity on the model
and inspect the generated CRD before deployment. This package does not claim
that Python-only validators become API-server validation.

`resource` is an immutable-in-practice detached **read snapshot**: editing its
fields never patches Kubernetes. Raw Kopf `spec`, `body`, `status`, `old`, `new`,
`diff`, `patch`, and other kwargs retain their original semantics. In particular,
field-scoped `old`/`new` are not whole resources. Only parameters declared on a
handler are injected if it does not accept `**kwargs`. Explicit `model=` works
without annotation inspection (except optional deletion fallback). No automatic
annotation-only inference is provided.

The spec field is validated independently, then the full resource is validated;
an invalid observed status is ignored if the model permits omitting status.
A failure outside `spec` and `status` remains an ordinary validation
exception, not a retriable desired-spec error. Kopf's retry limits and timeouts
still apply: if configured, they can prevent a handler from recovering after a
correction. Business-logic exceptions are passed through unchanged.
Repeated invalid event/index inputs can generate repeated warnings; do not use
these handlers as a substitute for admission.

For daemons, `resource` remains the startup snapshot; running user code is never
paused automatically. `resource_context.refresh()` validates the latest observed
Kopf body; `resource_context.wait(delay)` and `await
resource_context.wait_async(delay)` retry invalid input until corrected or
stopped. Refresh at meaningful processing boundaries and keep business logic
idempotent. `tk.patch_status(patch, status)` merges explicitly set JSON-compatible,
alias-preserving status keys into a patch; an explicit `None` requests key deletion
under Kubernetes merge-patch semantics. Kopf 1.44.6 defaults to
`SmartProgressStorage`, which writes progress to annotations (and reads legacy
status); this package does not overwrite explicit user storage settings.

Supported runtime: Python `>=3.13,<4`, Kopf `1.44.6`, Pydantic `2.13.5`,
kdantic `0.1.0` pinned to Git commit
`ec128034f8637fbd28d8d2a2087938593b6ddda1`. kdantic is an alpha
Git dependency and its Pydantic/schema mapping is lossy for some constructs.
Its settings parser consumes process arguments by default; the metadata adapter
disables that behavior for model registration.
