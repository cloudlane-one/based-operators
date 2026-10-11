# based-operator

The Helm chart that `based-operators scaffold` copies into a project. It installs
the CRDs of the operator, the RBAC it needs, and the operator itself.

The chart is static: it is not rendered by the scaffolder. The only values the
scaffolder writes are `image.repository`/`image.tag` (from `--image`) and
`webhook.enabled` (from `--webhook`); every other value below keeps its default.

With `webhook.enabled`, the operator container runs
`kopf run ... -m based_operators.webhook` and the chart sets the environment the
module reads — the mounted certificate, the port, the service hostname and the
managed configuration name. See the [webhook module docs][webhook] for what the
module itself does.

[webhook]: https://cloudlane-one.github.io/based-operators/ref-webhook.html

## Values

| Value | Default | Purpose |
| --- | --- | --- |
| `replicaCount` | `1` | Number of operator pods. Keep it at one: two operators reconciling the same objects collide. |
| `image.repository` | `based-operator` | The image repository built from the scaffolded Dockerfile. |
| `image.tag` | `latest` | The image tag. |
| `image.pullPolicy` | `IfNotPresent` | When the kubelet pulls the image. |
| `imagePullSecrets` | `[]` | Secrets for a private registry. |
| `nameOverride` | `""` | Overrides the chart name in the generated object names. |
| `fullnameOverride` | `""` | Overrides the generated object names entirely. |
| `serviceAccount.create` | `true` | Create the service account the operator runs as. |
| `serviceAccount.name` | `""` | Use an existing service account instead. |
| `serviceAccount.annotations` | `{}` | Annotations, e.g. for a workload-identity integration. |
| `rbac.create` | `true` | Create the `ClusterRole`/`ClusterRoleBinding`. Its rules are derived from the CRDs under `crds/`. |
| `podLabels`, `podAnnotations` | `{}` | Extra labels and annotations on the operator pods. |
| `securityContext` | hardened | Runs the container as the unprivileged user of the image, without capabilities. |
| `podSecurityContext` | `{}` | Pod-level security context. |
| `webhook.enabled` | `false` | Serve admission webhooks. Needs cert-manager and an operator that declares `on.validate`/`on.mutate` handlers. |
| `webhook.port` | `8443` | The port the webhook server listens on; the service exposes the same port, because the API server calls the endpoint kopf advertises. |
| `webhook.certManager.createIssuer` | `true` | Create a self-signed `Issuer` for the certificate. |
| `webhook.certManager.issuerRef` | `{}` | Use an existing CA-backed issuer instead, e.g. `{name: my-ca, kind: ClusterIssuer}`. |
| `resources` | `{}` | Resource requests and limits of the operator container. |
| `nodeSelector`, `tolerations`, `affinity` | `{}` | Standard scheduling controls. |

## CRDs

The CRDs live in `crds/`, which Helm installs before the chart and never
upgrades or deletes. Apply changes to them by hand:

```shell
kubectl apply -f crds/
```

The `ClusterRole` derives one rule per CRD from that same directory, so the
operator can reconcile exactly the resources of this chart and nothing else.
