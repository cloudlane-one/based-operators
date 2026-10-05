"""Model-first Kubernetes operator helpers; Kopf remains the execution engine."""

import kopf
from kopf import *  # noqa: F403

from based_operators.handlers import on
from based_operators.daemon import ResourceContext
from based_operators.status import patch_status

_kopf_exports = list(getattr(kopf, "__all__", ()))
_extra_exports = ["on", "patch_status", "ResourceContext"]
__all__ = [name for name in _kopf_exports + _extra_exports if name in globals()]
del _kopf_exports
del _extra_exports
del kopf
