"""Tests for :mod:`based_operators.scaffold`."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from based_operators import scaffold as scaffolding
from based_operators.scaffold import templating
from based_operators.scaffold.errors import ResourceLoadError, ScaffoldError

OPERATOR_SOURCE = '''
"""The operator entry point of the test project."""

from typing import Any

from models import Gadget
from pydantic import BaseModel, Field

import based_operators as bk


class WidgetSpec(BaseModel):
    """The spec of the widget."""

    message: str = Field(min_length=1)
    replicas: int = Field(default=1, ge=0, le=10)


class Widget(bk.CustomResource[WidgetSpec, None]):
    """A namespaced widget."""

    resource_config = bk.ResourceConfig(
        group="example.com",
        version="v1",
        kind="Widget",
        plural="widgets",
        scope=bk.Scope.NAMESPACED,
    )


@Widget.on.create
@Gadget.on.create
async def reconcile(resource: Any, **kwargs: Any) -> None:
    """Reconcile a widget or a gadget."""
'''

MODELS_SOURCE = '''
"""The resource models the entry point imports."""

from pydantic import BaseModel

import based_operators as bk


class GadgetSpec(BaseModel):
    """The spec of the gadget."""

    size: int = 1


class Gadget(bk.CustomResource[GadgetSpec, None]):
    """A cluster-scoped gadget."""

    resource_config = bk.ResourceConfig(
        group="example.com",
        version="v1",
        kind="Gadget",
        plural="gadgets",
        scope=bk.Scope.CLUSTER,
    )
'''

_LOADED_MODULES = frozenset({"models", "single", "empty", "broken", "bad", "elsewhere"})
"""The module names the tests' entry points import or are loaded under."""


@pytest.fixture(autouse=True)
def _restore_imports() -> Iterator[None]:
    """Undo what loading an entry point leaves behind in :mod:`sys`.

    Executing an entry point prepends its directory to ``sys.path`` and leaves the
    module in ``sys.modules``; without this, one test's project would satisfy the
    next test's imports.
    """
    paths = list(sys.path)
    yield
    sys.path[:] = paths
    for name in list(sys.modules):
        if name in _LOADED_MODULES or name.startswith("__based_operator_script__"):
            del sys.modules[name]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Create a two-resource project.

    One resource is declared in the entry point and one is imported into it, so
    both halves of "the entry point contains or references the handlers" are
    exercised, and the sibling import proves the entry point is loaded the way
    kopf loads it.

    Args:
        tmp_path: The temporary directory pytest provides.

    Returns:
        The root of the temporary project.
    """
    (tmp_path / "models.py").write_text(MODELS_SOURCE, encoding="utf-8")
    (tmp_path / "operator.py").write_text(OPERATOR_SOURCE, encoding="utf-8")
    return tmp_path


def scaffold(project: Path, entry: str = "operator.py", **overrides: Any) -> scaffolding.Scaffold:
    """Scaffold the project, overriding selected inputs.

    Args:
        project: The root of the temporary project.
        entry: The file name of the entry point.
        **overrides: Values passed to
            :func:`~based_operators.scaffold.scaffold_operator` instead of their
            defaults.

    Returns:
        The written scaffold.
    """
    options: dict[str, Any] = {"output": project / "deploy"}
    options.update(overrides)
    return scaffolding.scaffold_operator(operator=project / entry, context=project, **options)


def read(root: Path, relative: str) -> str:
    """Return a generated file as text.

    Args:
        root: The root of the generated scaffold.
        relative: The path of the file, relative to the root.

    Returns:
        The contents of the file.
    """
    return (root / relative).read_text(encoding="utf-8")


def load(root: Path, relative: str) -> Any:
    """Return a generated YAML file, parsed.

    Args:
        root: The root of the generated scaffold.
        relative: The path of the file, relative to the root.

    Returns:
        The parsed document.
    """
    return yaml.safe_load(read(root, relative))


# -- the generated tree ----------------------------------------------------------


def test_scaffold_writes_the_whole_tree(project: Path) -> None:
    """One call writes the Dockerfile, the README and the complete chart."""
    result = scaffold(project)

    output = project / "deploy"
    written = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
    assert written == {
        "Dockerfile",
        "README.md",
        "helm/based-operator/.helmignore",
        "helm/based-operator/Chart.yaml",
        "helm/based-operator/README.md",
        "helm/based-operator/values.yaml",
        "helm/based-operator/crds/gadgets.example.com.yaml",
        "helm/based-operator/crds/widgets.example.com.yaml",
        "helm/based-operator/templates/NOTES.txt",
        "helm/based-operator/templates/_helpers.tpl",
        "helm/based-operator/templates/certificate.yaml",
        "helm/based-operator/templates/deployment.yaml",
        "helm/based-operator/templates/rbac.yaml",
        "helm/based-operator/templates/service.yaml",
        "helm/based-operator/templates/serviceaccount.yaml",
    }
    assert list(result.written) == sorted(
        path
        for path in written
        if path.startswith("helm/based-operator/crds/")
        or path in {"Dockerfile", "README.md", "helm/based-operator/values.yaml"}
    )


def test_scaffold_reports_the_resources_it_found(project: Path) -> None:
    """The result names the resources the CRDs were generated from."""
    result = scaffold(project)

    assert [resource.__name__ for resource in result.resources] == ["Gadget", "Widget"]
    assert result.spec.name == "operator"
    assert result.spec.output_ref == "deploy"


def test_chart_is_copied_verbatim(project: Path) -> None:
    """The chart ships as it is, without the scaffolder rendering it.

    ``values.yaml`` is the one exception: the image the scaffold was asked for is
    written into the copy.
    """
    scaffold(project)

    chart = templating.HELM_DIR / scaffolding.CHART_NAME
    copied = [path for path in sorted(chart.rglob("*")) if path.is_file()]
    assert copied, "the shipped chart is empty"

    for path in copied:
        relative = path.relative_to(chart).as_posix()
        if relative == "values.yaml":
            continue
        generated = read(project / "deploy" / "helm" / scaffolding.CHART_NAME, relative)
        assert generated == path.read_text(encoding="utf-8"), relative


def test_nothing_is_written_into_the_project(project: Path) -> None:
    """The scaffold stays in its output directory."""
    scaffold(project)

    left_behind = {path.name for path in project.iterdir()} - {"__pycache__"}
    assert left_behind == {"deploy", "models.py", "operator.py"}


# -- the Dockerfile --------------------------------------------------------------


def test_dockerfile_runs_kopf_with_health_checks(project: Path) -> None:
    """The image installs the project and runs kopf with its liveness endpoint."""
    scaffold(project)
    dockerfile = read(project / "deploy", "Dockerfile")

    assert "FROM python:3.13-slim" in dockerfile
    assert "COPY . /app/" in dockerfile
    assert "RUN pip install --no-cache-dir ." in dockerfile
    assert 'CMD ["kopf", "run", "operator.py", "--liveness=http://0.0.0.0:8080/healthz"]' in (
        dockerfile
    )
    assert "EXPOSE 8080" in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_dockerfile_documents_the_resources(project: Path) -> None:
    """The resources the operator reconciles are named in the image's header."""
    scaffold(project)
    dockerfile = read(project / "deploy", "Dockerfile")

    assert "example.com/v1 Widget (Namespaced, installed as widgets.example.com)" in dockerfile
    assert "example.com/v1 Gadget (Cluster, installed as gadgets.example.com)" in dockerfile


def test_dockerfile_has_unix_line_endings(project: Path) -> None:
    """Generated files are Linux-consumable on every platform."""
    scaffold(project)

    for path in (project / "deploy").rglob("*"):
        if path.is_file():
            assert b"\r\n" not in path.read_bytes(), path


def test_python_version_is_configurable(project: Path) -> None:
    """The base image version comes from the caller."""
    scaffold(project, python_version="3.12")

    assert "FROM python:3.12-slim" in read(project / "deploy", "Dockerfile")


# -- the CRDs --------------------------------------------------------------------


def test_crds_are_generated_from_the_resources(project: Path) -> None:
    """Every resource the entry point declares reaches the chart's CRD directory."""
    scaffold(project)

    widget = load(project / "deploy", "helm/based-operator/crds/widgets.example.com.yaml")
    gadget = load(project / "deploy", "helm/based-operator/crds/gadgets.example.com.yaml")

    assert widget["apiVersion"] == "apiextensions.k8s.io/v1"
    assert widget["metadata"]["name"] == "widgets.example.com"
    assert widget["spec"]["group"] == "example.com"
    assert widget["spec"]["scope"] == "Namespaced"
    assert gadget["spec"]["scope"] == "Cluster"

    schema = widget["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    assert schema["required"] == ["spec"]
    assert schema["properties"]["spec"]["properties"]["replicas"]["maximum"] == 10
    assert schema["properties"]["spec"]["properties"]["message"]["minLength"] == 1


def test_status_less_resources_have_no_status_subresource(project: Path) -> None:
    """A resource without a status model gets no status subresource."""
    scaffold(project)

    widget = load(project / "deploy", "helm/based-operator/crds/widgets.example.com.yaml")

    assert "subresources" not in widget["spec"]["versions"][0]
    assert "status" not in widget["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]


# -- the chart values ------------------------------------------------------------


def test_values_carry_the_image(project: Path) -> None:
    """The image the scaffold was asked for is the one the chart deploys."""
    scaffold(project, image="ghcr.io/acme/demo:v2")
    values = load(project / "deploy", "helm/based-operator/values.yaml")

    assert values["image"]["repository"] == "ghcr.io/acme/demo"
    assert values["image"]["tag"] == "v2"
    assert values["image"]["pullPolicy"] == "IfNotPresent"
    assert values["replicaCount"] == 1


def test_values_default_to_the_entry_point_name(project: Path) -> None:
    """Without an image, the chart deploys the name derived from the entry point."""
    scaffold(project)
    values = load(project / "deploy", "helm/based-operator/values.yaml")

    assert values["image"]["repository"] == "operator"
    assert values["image"]["tag"] == "latest"


def test_name_is_used(project: Path) -> None:
    """The chart, the release and the default image follow the name."""
    scaffold(project, name="acme-widgets")
    values = load(project / "deploy", "helm/based-operator/values.yaml")

    assert values["image"]["repository"] == "acme-widgets"
    assert scaffolding.CHART_NAME in read(project / "deploy", "README.md")


# -- the webhook -----------------------------------------------------------------


def test_webhook_is_off_by_default(project: Path) -> None:
    """The image loads nothing extra and the chart serves no webhooks."""
    scaffold(project)

    assert "-m" not in read(project / "deploy", "Dockerfile")
    values = load(project / "deploy", "helm/based-operator/values.yaml")
    assert values["webhook"]["enabled"] is False


def test_webhook_loads_the_library_module(project: Path) -> None:
    """The webhook server comes from the library, loaded by kopf with ``-m``."""
    scaffold(project, webhook=True)
    dockerfile = read(project / "deploy", "Dockerfile")

    assert scaffolding.WEBHOOK_MODULE in dockerfile
    assert (
        'CMD ["kopf", "run", "operator.py", "-m", "based_operators.webhook", '
        '"--liveness=http://0.0.0.0:8080/healthz"]'
    ) in dockerfile


def test_webhook_enables_the_deployment(project: Path) -> None:
    """The chart is told to deploy the webhook service, the certificate and its probes."""
    scaffold(project, webhook=True)
    values = load(project / "deploy", "helm/based-operator/values.yaml")

    assert values["webhook"]["enabled"] is True
    assert values["webhook"]["port"] == 8443
    assert "cert-manager" in read(project / "deploy", "README.md")


# -- the generated README --------------------------------------------------------


def test_readme_documents_build_and_install(project: Path) -> None:
    """The scaffold README carries the commands that deploy it."""
    scaffold(
        project,
        image="ghcr.io/acme/demo:v2",
        namespace="operators",
        webhook=True,
    )
    readme = read(project / "deploy", "README.md")

    assert "docker build -f deploy/Dockerfile -t ghcr.io/acme/demo:v2 ." in readme
    assert "docker push ghcr.io/acme/demo:v2" in readme
    assert (
        "helm install operator ./deploy/helm/based-operator --namespace operators "
        "--create-namespace"
    ) in readme
    assert "kubectl --namespace operators logs --follow deploy/operator-based-operator" in readme
    assert "cert-manager" in readme
    assert "- `example.com/v1 Gadget` — scope `Cluster`, plural `gadgets`" in readme


def test_readme_of_a_single_resource_uses_the_singular(project: Path) -> None:
    """The generated prose agrees with the number of resources."""
    (project / "single.py").write_text(MODELS_SOURCE, encoding="utf-8")
    scaffold(project, entry="single.py")

    assert "**Gadget** custom resource\n" in read(project / "deploy", "README.md")


# -- refusing to clobber ---------------------------------------------------------


def test_existing_output_is_refused(project: Path) -> None:
    """An output directory that is already in use is not overwritten silently."""
    (project / "deploy").mkdir()
    (project / "deploy" / "notes.txt").write_text("mine", encoding="utf-8")

    with pytest.raises(ScaffoldError, match="already contains notes.txt; pass --force"):
        scaffold(project)

    assert (project / "deploy" / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_force_overwrites(project: Path) -> None:
    """Force allows an existing directory to be scaffolded into."""
    scaffold(project)
    stale = project / "deploy" / "helm" / "based-operator" / "templates" / "stale.yaml"
    stale.write_text("stale", encoding="utf-8")

    scaffold(project, force=True)

    assert not stale.exists()


def test_output_may_be_an_empty_directory(project: Path) -> None:
    """An empty output directory needs no force."""
    (project / "deploy").mkdir()

    assert scaffold(project).written


# -- refusals --------------------------------------------------------------------


def test_entry_point_without_resources_is_refused(project: Path) -> None:
    """An importable entry point that declares nothing usable is an error."""
    (project / "empty.py").write_text('"""Nothing here."""\n', encoding="utf-8")

    with pytest.raises(ScaffoldError, match="declares no custom resource"):
        scaffold(project, entry="empty.py")


def test_entry_point_that_raises_is_refused(project: Path) -> None:
    """A broken entry point is reported as such."""
    (project / "broken.py").write_text('raise RuntimeError("boom")\n', encoding="utf-8")

    with pytest.raises(ResourceLoadError, match="could not be imported"):
        scaffold(project, entry="broken.py")


def test_invalid_resource_definition_is_refused(project: Path) -> None:
    """A class that is not a complete resource definition names why, loudly."""
    (project / "bad.py").write_text(
        "from pydantic import BaseModel, Field\n\n"
        "import based_operators as bk\n\n\n"
        "class BadSpec(BaseModel):\n"
        '    """A spec whose wire names cannot be reconciled."""\n\n'
        '    message: str = Field(validation_alias="msg")\n\n\n'
        "class Bad(bk.CustomResource[BadSpec, None]):\n"
        '    """A resource with a half-aliased field."""\n\n'
        "    resource_config = bk.ResourceConfig(\n"
        '        group="example.com", version="v1", kind="Bad", plural="bads",\n'
        "        scope=bk.Scope.NAMESPACED,\n    )\n",
        encoding="utf-8",
    )

    with pytest.raises(ScaffoldError, match="is not a valid resource definition"):
        scaffold(project, entry="bad.py")


def test_entry_point_outside_the_build_context_is_refused(project: Path, tmp_path: Path) -> None:
    """The image installs the project, so the entry point has to be inside it."""
    outside = tmp_path.parent / "elsewhere.py"
    outside.write_text(MODELS_SOURCE, encoding="utf-8")

    with pytest.raises(ScaffoldError, match="lies outside the build context"):
        scaffold(project, entry="../elsewhere.py")


def test_invalid_name_is_refused(project: Path) -> None:
    """Names reach Kubernetes object names, so they are validated."""
    with pytest.raises(ScaffoldError, match="must be a lower-case label"):
        scaffold(project, name="Acme_Operator")


def test_invalid_python_version_is_refused(project: Path) -> None:
    """The base image tag is validated before it reaches the Dockerfile."""
    with pytest.raises(ScaffoldError, match="major and minor version"):
        scaffold(project, python_version="3")


# -- the units underneath --------------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("operator", ("operator", "latest")),
        ("operator:v1", ("operator", "v1")),
        ("ghcr.io/acme/operator", ("ghcr.io/acme/operator", "latest")),
        ("ghcr.io/acme/operator:v1.2.3", ("ghcr.io/acme/operator", "v1.2.3")),
        ("localhost:5000/operator", ("localhost:5000/operator", "latest")),
        ("localhost:5000/operator:dev", ("localhost:5000/operator", "dev")),
    ],
)
def test_parse_image_ref(reference: str, expected: tuple[str, str]) -> None:
    """A registry port is not mistaken for a tag."""
    assert scaffolding.parse_image_ref(reference) == expected


@pytest.mark.parametrize("reference", ["", "operator@sha256:abc", ":v1"])
def test_parse_image_ref_rejects_unusable_references(reference: str) -> None:
    """References Helm cannot express are refused."""
    with pytest.raises(ScaffoldError):
        scaffolding.parse_image_ref(reference)


def test_load_operator_executes_the_entry_point(project: Path) -> None:
    """Loading an entry point runs it, so its own imports resolve."""
    module = scaffolding.load_operator(project / "operator.py")

    assert module.Widget.__name__ == "Widget"
    assert module.Gadget.__module__ == "models"


def test_discover_resources_is_ordered_and_deduplicated(project: Path) -> None:
    """Both declared and imported resources are found, ordered by CRD name."""
    module = scaffolding.load_operator(project / "operator.py")

    found = scaffolding.discover_resources(module)

    assert [resource.__name__ for resource in found] == ["Gadget", "Widget"]


def test_load_operator_reports_a_missing_file(project: Path) -> None:
    """A path that is not a Python file is reported as a load failure."""
    with pytest.raises(ResourceLoadError, match="not an importable Python file"):
        scaffolding.load_operator(project / "missing.txt")


def test_write_scaffold_refuses_a_file_as_output(project: Path) -> None:
    """The output directory cannot be an existing file."""
    spec = scaffolding.build_spec(
        operator=project / "operator.py",
        output=project / "operator.py",
        context=project,
    )

    with pytest.raises(ScaffoldError, match="exists and is not a directory"):
        scaffolding.write_scaffold(spec, ())


def test_write_scaffold_needs_generatable_resources(project: Path) -> None:
    """A CRD that cannot be generated stops the scaffold with a clear error."""
    spec = scaffolding.build_spec(
        operator=project / "operator.py",
        output=project / "deploy",
        context=project,
    )

    with pytest.raises(ScaffoldError, match="cannot be generated"):
        scaffolding.write_scaffold(spec, (object,))  # type: ignore[arg-type]


def test_build_spec_requires_a_file(project: Path) -> None:
    """The entry point has to exist."""
    with pytest.raises(ScaffoldError, match="is not a file"):
        scaffolding.build_spec(
            operator=project / "nope.py",
            output=project / "deploy",
            context=project,
        )


def test_build_spec_refuses_an_empty_namespace(project: Path) -> None:
    """Public identifiers are validated before they reach the generated files."""
    with pytest.raises(ScaffoldError, match="namespace"):
        scaffolding.build_spec(
            operator=project / "operator.py",
            output=project / "deploy",
            context=project,
            namespace="",
        )


def test_build_spec_exposes_the_entry_point(project: Path) -> None:
    """The spec knows where the entry point it describes lives."""
    spec = scaffolding.build_spec(
        operator=project / "operator.py",
        output=project / "deploy",
        context=project,
    )

    assert spec.operator_path == project / "operator.py"
    assert spec.operator_ref == "operator.py"
    assert spec.dockerfile_ref == "deploy/Dockerfile"
    assert spec.chart_ref == "deploy/helm/based-operator"


def test_describe_resource_summarizes_identity(project: Path) -> None:
    """The description used by the templates names the Kubernetes identity."""
    module = scaffolding.load_operator(project / "operator.py")

    described = scaffolding.describe_resource(module.Widget)

    assert described == {
        "kind": "Widget",
        "group": "example.com",
        "version": "v1",
        "plural": "widgets",
        "scope": "Namespaced",
        "api_version": "example.com/v1",
        "crd_name": "widgets.example.com",
    }
