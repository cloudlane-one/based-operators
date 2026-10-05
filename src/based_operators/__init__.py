"""Model-first Kubernetes operator helpers; Kopf remains the execution engine."""

import kopf as upstream
from kopf import *  # noqa: F403

from based_operators.handlers import on
from based_operators.status import patch_status

__all__ = [name for name in dir(upstream) if not name.startswith("_")] + [
    "on", "upstream", "patch_status",
]
