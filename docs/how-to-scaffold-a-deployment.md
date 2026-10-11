# How to scaffold a deployment

This guide turns an operator entry point into a container image and a Helm chart.
For the full option list and the exact layout that is written, see the
[command-line reference](./ref-cli.md).

## What the command needs

An **operator entry point**: a single Python file that declares, or imports, both
the resource classes and the handlers. That file is loaded exactly the way
`kopf run` loads it — its own directory is put in front of `sys.path`, so it may
import sibling modules by their plain module names — and it is the file the image
runs. [`app/operator_example.py`][example] in this repository is a complete one.

[example]: https://github.com/cloudlane-one/based-operators/blob/main/app/operator_example.py

Run the command from the project root, because that directory becomes the build
context of the generated Dockerfile: the entry point and the output directory
both have to live inside it. The command is the `based-operators` console script
of the distribution, so it needs no checkout — `uvx based-operators` runs it
straight from the package:

```shell
uvx based-operators scaffold operator.py -o deploy
```

```txt
Scaffolded the Widget operator into deploy/:
  Dockerfile
  README.md
  helm/based-operator/Chart.yaml
  helm/based-operator/crds/widgets.example.com.yaml
  helm/based-operator/values.yaml
  ...

  next: docker build -f deploy/Dockerfile -t operator:latest .
  then: helm install operator ./deploy/helm/based-operator --namespace default --create-namespace
```

## What is written

| Path | Purpose |
| --- | --- |
| `Dockerfile` | The operator image: the project, plus kopf running the entry point. |
| `README.md` | The build, push and install commands for this particular scaffold. |
| `helm/based-operator/` | A self-contained Helm chart: the CRDs under `crds/`, the RBAC derived from them, the deployment, and a values file holding the image. |

The Dockerfile and the README are rendered from templates, so they can be read
and edited like any other file; the chart is a plain chart that is copied as it
is, and the CRDs are generated from your models. No Python is generated: with
`--webhook`, the image imports [`based_operators.webhook`](./ref-webhook.md), a
module that ships with `based-operators` itself.

!!! note "The image installs your project"
    The generated Dockerfile runs `pip install .` on the build context, so the
    operator's own dependencies — including `based-operators` and `kopf` — come
    from the project itself. It also installs `git` and copies the whole context,
    `.git` included, so a project that derives its version from version control
    (`hatch-vcs`, `setuptools-scm`, `uv-dynamic-versioning`, …) installs without
    extra work. A project that is not pip-installable needs the Dockerfile's
    install step adjusted.

## Build, push and install

```shell
docker build -f deploy/Dockerfile -t ghcr.io/acme/widget-operator:v1 .
docker push ghcr.io/acme/widget-operator:v1
helm install widget-operator ./deploy/helm/based-operator --create-namespace
```

Pass the image to the scaffold so that the chart deploys what you built:

```shell
uvx based-operators scaffold operator.py -o deploy --image ghcr.io/acme/widget-operator:v1
```

## Health checks

kopf serves a liveness endpoint when it is given `--liveness`, and the generated
image's command does exactly that:

```shell
kopf run operator.py --liveness=http://0.0.0.0:8080/healthz
```

The chart's `livenessProbe` calls the same port and path, so the operator is
restarted if it stops responding. The command is baked into the image, and the
image runs as an unprivileged user; both are visible in the generated Dockerfile.

## Admission webhooks

Pass `--webhook` when the entry point declares `on.validate` or `on.mutate`
handlers. kopf refuses to start if admission handlers exist without a webhook
server, so the flag is what makes such an operator deployable:

```shell
uvx based-operators scaffold operator.py -o deploy --webhook
```

The image then imports [`based_operators.webhook`](./ref-webhook.md), the module
that ships with `based-operators` and arms the webhook server, so no file is
generated for it:

```shell
kopf run operator.py -m based_operators.webhook --liveness=http://0.0.0.0:8080/healthz
```

The module reads everything deployment-specific from the environment, which the
chart sets: the mounted certificate directory, the port, the hostname the API
server calls, and the name of the webhook configuration kopf keeps in sync. The
chart itself adds a `Service`, a cert-manager `Certificate` (self-signed by
default, or point `webhook.certManager.issuerRef` at your own issuer), and the
permissions to manage the webhook configurations.

## Re-scaffolding after a change

The scaffold refuses to write into a directory that is not empty, so that a
mistyped `-o` cannot quietly overwrite work; pass `--force` to overwrite the
files it generates. The chart directory is replaced as a whole, so templates from
an older scaffold do not linger. CRDs are installed by Helm from `crds/` and are
never upgraded by it, so re-apply them by hand:

```shell
kubectl apply -f deploy/helm/based-operator/crds/
```

## Next steps

- [Command-line reference](./ref-cli.md) — the command and its options, generated
  from the CLI itself.
- [The webhook module](./ref-webhook.md) — what `based_operators.webhook` does
  and which environment it reads.
- kopf's own [deployment recipe](https://docs.kopf.dev/en/stable/deployment/) —
  why the chart runs exactly one replica with `strategy: Recreate`.
