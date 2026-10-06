# Based Operators

Model-first Python operators powered by [Kopf](https://kopf.readthedocs.io/) and [kdantic](https://github.com/sonyinteractive/kdantic).

See [the documentation](docs/index.md) for usage and compatibility limits.

This repository retains its original MIT licensing and contribution guide.

```python
import based_operators as tk

@tk.on.create(model=Greeting)
@tk.on.resume(model=Greeting)
def reconcile(resource: Greeting, patch: tk.Patch):
    print(resource.spec)
```

`Greeting` must be a Pydantic v2 resource model with a `spec` field and a
kdantic-compatible Kubernetes identity. See the [compatibility matrix](docs/operator-api.md)
before using typed deletion, admission, daemons, or HA deployments.
