# Build downstream operator artifacts

Configure the models and Kopf handler explicitly in the downstream project's `pyproject.toml`:

```toml
[tool.based-operators]
models = ["my_operator.models:Widget", "my_operator.models:Gadget"]
handler = "app/operator.py"
```

The handler must exist under `app/`. Model imports resolve from the project or its `src/` directory. Each model must be a Pydantic model with a `spec` model field and optionally a `status` model field. Set `apiVersion` to your API group/version and `kind` to your resource kind; kdantic resolves the remaining CRD metadata. Model imports run Python code, so only build trusted projects.

```sh
python -m based_operators.cli build --project-root . --output dist/operator --image registry.example.com/operator:1
docker build -f dist/operator/Dockerfile -t registry.example.com/operator:1 .
helm install my-operator dist/operator/helm --namespace my-namespace
```

The build generates a multi-stage, non-root `Dockerfile`, `manifest.json` with SHA-256 checksums, and a chart with `Chart.yaml`, `values.yaml`, `values.schema.json`, `README.md`, namespace-scoped Role/RoleBinding, a single-replica Deployment and kdantic CRDs under `helm/crds/`. CRDs in `helm/crds/` are installed by Helm but **not upgraded or removed** by Helm; manage CRD schema upgrades separately. The output directory must not exist; artifacts are published together after generation succeeds. The Dockerfile assumes the project contains `src/`, `app/`, `README.md` and `uv.lock`, and that its lockfile supports `uv sync --frozen --no-dev` (including the downstream package). Build with the project as Docker context. Outside Helm, provide an explicit namespace, e.g. `docker run -e POD_NAMESPACE=my-namespace ...`, with namespace-scoped Kubernetes credentials. The Helm deployment sets `POD_NAMESPACE` from its release namespace.

Only namespaced resources watched in the Helm release namespace and `--standalone` mode are supported. Cluster-scoped resources, cross-namespace watching, multiple replicas and `--mode ha` are rejected or not generated: there is no distributed leader election or high-availability guarantee. Supported schema field types are `str`, `int`, `float`, `bool`, string `Literal`, enums with homogeneous primitive values, nested Pydantic models, typed lists and maps with `str` keys; each can be optional with `None`. Other leaf types, untyped collections and non-optional unions such as `int | str` are rejected rather than silently converted to strings. Explicit field defaults must be JSON-serializable. No runtime image is built or pushed by this command.
