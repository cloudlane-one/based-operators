"""Turn the custom resources of a project into a deployable operator.

This package derives everything a cluster needs to run an operator: it loads an
operator entry point the way ``kopf run`` loads it, generates one
CustomResourceDefinition per custom resource it declares, and writes a Dockerfile
and a CRD-complete Helm chart next to them.

The command-line front end of this package is the ``scaffold`` command of the
``based-operators`` console script, defined in :mod:`based_ops_cli.__main__`.
"""

from based_operators.scaffold.errors import ResourceLoadError, ScaffoldError
from based_operators.scaffold.project import (
    CHART_NAME,
    CRDS_DIRNAME,
    HEALTH_PATH,
    HEALTH_PORT,
    WEBHOOK_MODULE,
    Scaffold,
    ScaffoldSpec,
    build_spec,
    describe_resource,
    parse_image_ref,
    scaffold_operator,
    write_scaffold,
)
from based_operators.scaffold.resources import (
    discover_resources,
    is_resource_class,
    load_operator,
)

__all__ = [
    "CHART_NAME",
    "CRDS_DIRNAME",
    "HEALTH_PATH",
    "HEALTH_PORT",
    "WEBHOOK_MODULE",
    "ResourceLoadError",
    "Scaffold",
    "ScaffoldError",
    "ScaffoldSpec",
    "build_spec",
    "describe_resource",
    "discover_resources",
    "is_resource_class",
    "load_operator",
    "parse_image_ref",
    "scaffold_operator",
    "write_scaffold",
]
