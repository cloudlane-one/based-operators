"""Model-first Kubernetes operator helpers; Kopf remains the execution engine."""

import kopf
from kopf import *  # noqa: F403

from based_operators.handlers import on
from based_operators.daemon import ResourceContext
from based_operators.status import patch_status

__all__ = [name for name in dir(kopf) if not name.startswith("_")] + [
    "on", "patch_status", "ResourceContext",
]
del kopf
