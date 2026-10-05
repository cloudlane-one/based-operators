"""Tests for downstream operator artifact generation."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from enum import Enum
from types import SimpleNamespace
from typing import Annotated, cast

import pytest
import yaml
from pydantic import AliasPath, BaseModel

from based_operators.build import (
    BuildError,
    _check_type,
    _load_model,
    _normalize_model_required,
    _normalize_nullable,
    build,
)
from based_operators.cli import main


def project(
    tmp_path: Path,
    *,
    models: str = '"sample:Widget"',
    fields: str = "value: int",
    clear_import_cache: bool = True,
) -> Path:
    """Create a minimal isolated project with explicit model imports."""
    if clear_import_cache:
        sys.modules.pop("sample", None)
    (tmp_path / "src").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "operator.py").write_text("import kopf\n")
    (tmp_path / "README.md").write_text("Test project\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / "src" / "sample.py").write_text(
        "from pydantic import BaseModel\n"
        "class Spec(BaseModel):\n"
        f"    {fields}\n"
        "class Widget(BaseModel):\n"
        "    apiVersion: str = 'widgets.example.com/v1'\n"
        "    kind: str = 'Widget'\n"
        "    namespace: str = 'default'\n"
        "    spec: Spec\n"
        "class Duplicate(BaseModel):\n"
        "    apiVersion: str = 'widgets.example.com/v1'\n"
        "    kind: str = 'Widget'\n"
        "    namespace: str = 'default'\n"
        "    spec: Spec\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.based-operators]\nmodels = [" + models + ']\nhandler = "app/operator.py"\n'
    )
    return tmp_path


def test_build_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The generated resources use kdantic schemas and a single namespaced replica."""
    root = project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    output = build(root, root / "dist/operator", image="example/operator:1")
    crd = json.loads((output / "helm/crds/widgets.widgets.example.com.yaml").read_text())
    assert (
        crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"][
            "value"
        ]["type"]
        == "integer"
    )
    assert crd["spec"]["scope"] == "Namespaced"
    assert "replicas: 1" in (output / "helm/templates/operator.yaml").read_text()
    dockerfile = (output / "Dockerfile").read_text()
    assert "FROM python:3.13-slim AS build" in dockerfile
    assert "USER 10001:10001" in dockerfile
    assert "--no-install-project" not in dockerfile
    assert "default" not in dockerfile
    cmd = json.loads(dockerfile.split("CMD ", 1)[1])
    assert cmd[:2] == ["python", "-c"]
    assert "os.execvp" in cmd[2] and "POD_NAMESPACE" in cmd[2]
    assert "--liveness=http://0.0.0.0:8080/healthz" in cmd[2]
    assert "--module" in cmd[2] and "based_operators.runtime" in cmd[2]
    workload = (output / "helm/templates/operator.yaml").read_text()
    assert "livenessProbe:" in workload and "path: /healthz" in workload
    assert '"example/operator:1"' in (output / "helm/values.yaml").read_text()
    schema = json.loads((output / "helm/values.schema.json").read_text())
    assert schema["required"] == ["image"]
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["models"] == ["sample:Widget"]
    for path, checksum in manifest["sha256"].items():
        assert hashlib.sha256((output / path).read_bytes()).hexdigest() == checksum
    other = build(root, root / "dist/other", image="example/operator:1")
    assert (output / "manifest.json").read_bytes() == (other / "manifest.json").read_bytes()
    if shutil.which("helm"):
        rendered = subprocess.run(
            ["helm", "template", "sample", str(output / "helm"), "--namespace", "demo"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "replicas: 1" in rendered
        assert 'image: "example/operator:1"' in rendered
    with pytest.raises(BuildError, match="already exists"):
        build(root, output, image="example/operator:1")


def test_rbac_rules_keep_resources_in_their_api_groups(tmp_path: Path) -> None:
    """Do not grant permissions for unconfigured group/resource combinations."""
    root = project(tmp_path, models='"sample:Widget", "sample:Gadget"')
    sample = root / "src" / "sample.py"
    sample.write_text(
        sample.read_text()
        + "class Gadget(BaseModel):\n"
        "    apiVersion: str = 'gadgets.example.com/v1'\n"
        "    kind: str = 'Gadget'\n"
        "    namespace: str = 'default'\n"
        "    spec: Spec\n"
    )
    output = build(root, root / "out", image="x")
    template = (output / "helm/templates/operator.yaml").read_text()
    role = yaml.safe_load("rules:\n" + template.split("rules:\n", 1)[1].split("---", 1)[0])
    rules = role["rules"]
    assert len(rules) == 5
    assert {
        (tuple(rule["apiGroups"]), tuple(rule["resources"]), tuple(rule["verbs"]))
        for rule in rules
    } == {
        (("widgets.example.com",), ("widgets",),
         ("get", "list", "watch", "create", "update", "patch", "delete")),
        (("widgets.example.com",), ("widgets/status",), ("get", "patch", "update")),
        (("gadgets.example.com",), ("gadgets",),
         ("get", "list", "watch", "create", "update", "patch", "delete")),
        (("gadgets.example.com",), ("gadgets/status",), ("get", "patch", "update")),
        (("",), ("events",), ("create", "patch")),
    }


def test_model_import_restores_sys_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A model module cannot leave process-wide import path changes behind."""
    root = project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    sample = root / "src" / "sample.py"
    sample.write_text("import sys\nsys.path.insert(0, 'unexpected-path')\n" + sample.read_text())
    original_sys_path = sys.path.copy()

    _load_model("sample:Widget", root)

    assert sys.path == original_sys_path


def test_build_isolates_consecutive_project_imports(tmp_path: Path) -> None:
    """Consecutive builds isolate same-named project packages and their dependencies."""
    package_name = "based_operators_fixture_models"
    package_modules = (package_name, f"{package_name}.schema", f"{package_name}.shared")
    for name in package_modules:
        sys.modules.pop(name, None)

    def configure_model(root: Path, value_type: str, value: str) -> None:
        package = root / "src" / package_name
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "shared.py").write_text(f"VALUE = {value!r}\n")
        (package / "schema.py").write_text(
            "from pydantic import BaseModel\n"
            "from .shared import VALUE\n"
            "class Spec(BaseModel):\n"
            f"    value: {value_type} = VALUE\n"
            "class Widget(BaseModel):\n"
            "    apiVersion: str = 'widgets.example.com/v1'\n"
            "    kind: str = 'Widget'\n"
            "    namespace: str = 'default'\n"
            "    spec: Spec\n"
        )
        config = root / "pyproject.toml"
        config.write_text(
            config.read_text().replace('"sample:Widget"', f'"{package_name}.schema:Widget"')
        )

    try:
        first_root = tmp_path / "first"
        first_root.mkdir()
        first = project(first_root)
        configure_model(first, "int", "1")
        build(first, first / "out", image="x")

        second_root = tmp_path / "second"
        second_root.mkdir()
        second = project(second_root, clear_import_cache=False)
        configure_model(second, "str", "fresh")
        output = build(second, second / "out", image="x")

        crd = json.loads(
            (output / "helm/crds/widgets.widgets.example.com.yaml").read_text()
        )
        value_schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
            "properties"
        ]["spec"]["properties"]["value"]
        assert value_schema["type"] == "string"
    finally:
        for name in package_modules:
            sys.modules.pop(name, None)


def test_build_preserves_project_environment_dependencies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache cleanup must not evict dependencies installed under the project root."""
    root = project(tmp_path)
    dependency_path = root / ".venv/lib/python/site-packages"
    dependency_path.mkdir(parents=True)
    (dependency_path / "project_dependency.py").write_text("VALUE = 1\n")
    sample = root / "src" / "sample.py"
    sample.write_text("from project_dependency import VALUE\n" + sample.read_text())
    monkeypatch.syspath_prepend(str(dependency_path))

    build(root, root / "first", image="x")
    dependency = sys.modules["project_dependency"]
    build(root, root / "second", image="x")

    assert sys.modules["project_dependency"] is dependency


def test_build_preserves_non_filesystem_modules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cache cleanup must not evict built-in or frozen modules when cwd is the project."""
    root = project(tmp_path)
    monkeypatch.chdir(root)
    builtin = sys.modules["sys"]
    frozen = sys.modules["importlib._bootstrap"]
    build(root, root / "out", image="x")

    assert sys.modules["sys"] is builtin
    assert sys.modules["importlib._bootstrap"] is frozen


def test_project_root_model_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Root-level model modules cannot be imported by the generated container."""
    root = project(tmp_path)
    (root / "src" / "sample.py").replace(root / "sample.py")
    monkeypatch.syspath_prepend(str(root))

    with pytest.raises(BuildError, match="under the project's src/"):
        build(root, root / "out", image="x")

    assert not (root / "out").exists()


def test_optional_webhooks_and_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Helm deploys TLS and per-handler admission routes only when opted in."""
    root = project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    output = build(root, root / "out", image="example/operator:1")
    if not shutil.which("helm"):
        if os.environ.get("BASED_OPERATORS_REQUIRE_HELM") == "1":
            pytest.fail("Helm is required to lint the generated chart")
        pytest.skip("Helm is not installed")
    chart = str(output / "helm")
    base = ["helm", "template", "sample", chart, "--namespace", "demo"]
    default = subprocess.run(base, check=True, capture_output=True, text=True).stdout
    assert "livenessProbe:" in default
    assert "kind: ValidatingWebhookConfiguration" not in default
    assert "kind: Service\n" not in default
    options = [
        "--set", "webhooks.enabled=true",
        "--set", "webhooks.caBundle=Y2E=",
        "--set", "webhooks.tlsCrt=certificate",
        "--set", "webhooks.tlsKey=privatekey",
        "--set", "webhooks.validating[0].id=validate-widget",
        "--set", "webhooks.validating[0].resources[0]=widgets.example.com/v1/widgets",
        "--set", "webhooks.mutating[0].id=mutate-widget",
        "--set", "webhooks.mutating[0].resources[0]=widgets.example.com/v1/widgets",
    ]
    enabled = subprocess.run(base + options, check=True, capture_output=True, text=True).stdout
    assert "kind: Secret" in enabled
    assert "kind: Service" in enabled
    assert "kind: ValidatingWebhookConfiguration" in enabled
    assert "kind: MutatingWebhookConfiguration" in enabled
    assert "path: \"/validate-widget\"" in enabled
    assert "path: \"/mutate-widget\"" in enabled
    assert "BASED_OPERATORS_WEBHOOKS" in enabled
    webhook_docs = list(yaml.safe_load_all(enabled))
    validation = next(
        item for item in webhook_docs if item["kind"] == "ValidatingWebhookConfiguration"
    )
    assert validation["webhooks"][0]["rules"][0]["apiGroups"] == ["widgets.example.com"]
    for lint_args in ([], options):
        subprocess.run(
            ["helm", "lint", chart, *lint_args], check=True, capture_output=True, text=True
        )
    missing = subprocess.run(
        base + ["--set", "webhooks.enabled=true"], capture_output=True, text=True
    )
    assert missing.returncode != 0
    existing = subprocess.run(
        base + [
            "--set", "webhooks.enabled=true",
            "--set", "webhooks.caBundle=Y2E=",
            "--set", "webhooks.existingSecret=my-tls",
            "--set", "webhooks.validating[0].id=validate-widget",
            "--set", "webhooks.validating[0].resources[0]=widgets.example.com/v1/widgets",
        ], check=True, capture_output=True, text=True,
    ).stdout
    assert "secretName: my-tls" in existing
    assert "kind: Secret" not in existing
    invalid_ca = subprocess.run(
        base + options[:2] + ["--set", "webhooks.caBundle=not base64"],
        capture_output=True, text=True,
    )
    assert invalid_ca.returncode != 0


def test_webhook_handler_resource_rules(tmp_path: Path) -> None:
    """A handler for Widget must not intercept Gadget admission requests."""
    root = project(tmp_path, models='"sample:Widget", "sample:Gadget"')
    sample = root / "src" / "sample.py"
    sample.write_text(
        sample.read_text()
        + "class Gadget(BaseModel):\n"
        + "    apiVersion: str = 'gadgets.example.com/v1'\n"
        + "    kind: str = 'Gadget'\n"
        + "    namespace: str = 'default'\n"
        + "    spec: Spec\n"
    )
    output = build(root, root / "out", image="x")
    if not shutil.which("helm"):
        pytest.skip("Helm is not installed")
    base = ["helm", "template", "sample", str(output / "helm"), "--namespace", "demo"]
    options = [
        "--set", "webhooks.enabled=true",
        "--set", "webhooks.caBundle=Y2E=",
        "--set", "webhooks.existingSecret=my-tls",
        "--set", "webhooks.validating[0].id=validate-widget",
        "--set", "webhooks.validating[0].resources[0]=widgets.example.com/v1/widgets",
    ]
    rendered = subprocess.run(base + options, check=True, capture_output=True, text=True).stdout
    validation = next(
        item for item in yaml.safe_load_all(rendered)
        if item["kind"] == "ValidatingWebhookConfiguration"
    )
    assert validation["webhooks"][0]["rules"] == [{
        "apiGroups": ["widgets.example.com"],
        "apiVersions": ["v1"],
        "resources": ["widgets"],
        "operations": ["CREATE", "UPDATE"],
        "scope": "Namespaced",
    }]
    unknown = subprocess.run(
        base + options[:-1] + [
            "--set", "webhooks.validating[0].resources[0]=gadgets.example.com/v1/unknown",
        ], capture_output=True, text=True,
    )
    assert unknown.returncode != 0


@pytest.mark.parametrize(
    ("models", "fields", "error"),
    [
        ('"sample:Widget", "sample:Widget"', "value: int", "Duplicate model"),
        ('"sample:Widget", "sample:Duplicate"', "value: int", "Duplicate CRD"),
        ('"sample:Widget"', "value: int | str", "unsupported union"),
        ('"sample:Unknown"', "value: int", "expected a Pydantic"),
    ],
)
def test_invalid_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, models: str, fields: str, error: str
) -> None:
    """Invalid models fail before any output directory is created."""
    root = project(tmp_path, models=models, fields=fields)
    monkeypatch.syspath_prepend(str(root))
    with pytest.raises(BuildError, match=error):
        build(root, root / "dist/operator", image="example/operator:1")
    assert not (root / "dist/operator").exists()


def test_cli_and_ha_rejection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI reports unsupported HA explicitly rather than emitting a fake HA chart."""
    root = project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    assert (
        main(
            [
                "build",
                "--project-root",
                str(root),
                "--output",
                str(root / "out"),
                "--image",
                "x",
                "--mode",
                "ha",
            ]
        )
        == 1
    )
    assert not (root / "out").exists()
    assert (
        main(["build", "--project-root", str(root), "--output", str(root / "out"), "--image", "x"])
        == 0
    )


def test_cluster_scope_and_nested_union(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid generating namespace-limited RBAC or kdantic's lossy union schema."""
    root = project(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    sample = root / "src" / "sample.py"
    sample.write_text(sample.read_text().replace("    namespace: str = 'default'\n", ""))
    with pytest.raises(BuildError, match="Namespaced"):
        build(root, root / "out", image="x")
    assert not (root / "out").exists()

    sys.modules.pop("sample", None)
    sample.write_text(
        sample.read_text()
        .replace("    value: int", "    value: list[int | str]")
        .replace("    spec: Spec", "    namespace: str = 'default'\n    spec: Spec")
    )
    with pytest.raises(BuildError, match="unsupported union"):
        build(root, root / "out", image="x")


def test_handler_path_is_confined(tmp_path: Path) -> None:
    """The generated Docker image copies only app/, not arbitrary handler paths."""
    root = project(tmp_path)
    (root / "pyproject.toml").write_text(
        '[tool.based-operators]\nmodels = ["sample:Widget"]\nhandler = "../escape.py"\n'
    )
    with pytest.raises(BuildError, match="app/"):
        build(root, root / "out", image="x")
    assert not (root / "out").exists()


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ("value: bytes", "unsupported schema type"),
        ("value: dict[int, str]", "string keys"),
        ("value: dict", "unsupported schema type"),
        ("value: list", "unsupported schema type"),
        ("value: list[int | None]", "nullable collection members"),
        ("value: dict[str, int | None]", "nullable collection members"),
        ("value: list[dict[str, int | None]]", "nullable collection members"),
        ("value: object", "unsupported schema type"),
        ("value: int\n    fallback: object = object()", "unsupported schema type"),
    ],
)
def test_reject_lossy_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fields: str, error: str
) -> None:
    """Unsupported leaves and non-string maps must not silently become strings."""
    root = project(tmp_path, fields=fields)
    monkeypatch.syspath_prepend(str(root))
    with pytest.raises(BuildError, match=error):
        build(root, root / "out", image="x")
    assert not (root / "out").exists()


@pytest.mark.parametrize(
    ("original", "replacement", "name"),
    [
        ("class Spec(BaseModel):\n    value: int",
         "class Spec(RootModel[list[str]]):\n    pass", "Spec"),
        ("class Widget(BaseModel):",
         "class Status(RootModel[list[str]]):\n    pass\nclass Widget(BaseModel):", "Status"),
        ("class Spec(BaseModel):\n    value: int",
         "class Nested(RootModel[list[str]]):\n    pass\n"
         "class Spec(BaseModel):\n    value: list[Nested]", "Nested"),
    ],
)
def test_reject_root_models(
    tmp_path: Path, original: str, replacement: str, name: str
) -> None:
    """RootModel schemas differ from kdantic's generated object schemas."""
    root = project(tmp_path)
    sample = root / "src" / "sample.py"
    source = sample.read_text().replace(
        "from pydantic import BaseModel", "from pydantic import BaseModel, RootModel"
    ).replace(original, replacement)
    if name == "Status":
        source = source.replace(
            "    spec: Spec\n", "    spec: Spec\n    status: Status | None = None\n"
        )
    sample.write_text(source)
    with pytest.raises(BuildError, match=f"{name}: RootModel schemas are unsupported"):
        build(root, root / "out", image="x")
    assert not (root / "out").exists()


def test_required_and_default_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Check that payload and envelope requiredness follows Pydantic fields."""
    root = project(
        tmp_path,
        fields="value: int | None\n    label: str = 'ready'\n    optional: int | None = None",
    )
    monkeypatch.syspath_prepend(str(root))
    output = build(root, root / "out", image="x")
    crd = json.loads((output / "helm/crds/widgets.widgets.example.com.yaml").read_text())
    schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
    spec = schema["properties"]["spec"]
    assert spec["required"] == ["value"]
    assert spec["properties"]["label"]["default"] == "ready"
    assert spec["properties"]["optional"]["nullable"] is True
    assert schema["required"] == ["spec"]


def test_required_fields_recurse_with_serialized_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normalize nullable required fields in nested and container schemas."""
    root = project(tmp_path)
    (root / "src" / "sample.py").write_text(
        "from pydantic import BaseModel, Field\n"
        "class Child(BaseModel):\n"
        "    nested_value: str | None\n"
        "    defaulted: int = 1\n"
        "class Spec(BaseModel):\n"
        "    direct_value: int | None\n"
        "    renamed: str | None = Field(validation_alias='serializedName', "
        "serialization_alias='serializedName')\n"
        "    child: Child\n"
        "    children: list[Child]\n"
        "class Widget(BaseModel):\n"
        "    apiVersion: str = 'widgets.example.com/v1'\n"
        "    kind: str = 'Widget'\n"
        "    namespace: str = 'default'\n"
        "    spec: Spec\n"
    )
    monkeypatch.syspath_prepend(str(root))
    output = build(root, root / "out", image="x")
    crd = json.loads((output / "helm/crds/widgets.widgets.example.com.yaml").read_text())
    schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    assert schema["required"] == ["direct_value", "serializedName", "child", "children"]
    assert schema["properties"]["child"]["required"] == ["nested_value"]
    assert schema["properties"]["children"]["items"]["required"] == ["nested_value"]


def test_serialized_alias_round_trips_from_schema_to_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CRD property emitted under an alias is accepted by typed resource validation."""
    from based_operators.validation import validate_resource

    root = project(tmp_path)
    (root / "src" / "sample.py").write_text(
        "from pydantic import BaseModel, Field\n"
        "class Spec(BaseModel):\n"
        "    renamed: str = Field(validation_alias='serializedName', "
        "serialization_alias='serializedName')\n"
        "class Widget(BaseModel):\n"
        "    apiVersion: str = 'widgets.example.com/v1'\n"
        "    kind: str = 'Widget'\n"
        "    namespace: str = 'default'\n"
        "    spec: Spec\n"
    )
    monkeypatch.syspath_prepend(str(root))
    output = build(root, root / "out", image="x")
    crd = json.loads((output / "helm/crds/widgets.widgets.example.com.yaml").read_text())
    spec_schema = crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    property_name = next(iter(spec_schema["properties"]))
    resource = validate_resource(
        _load_model("sample:Widget", root),
        {
            "apiVersion": "widgets.example.com/v1",
            "kind": "Widget",
            "namespace": "default",
            "spec": {property_name: "accepted"},
        },
    )
    assert property_name == "serializedName"
    assert getattr(getattr(resource, "spec"), "renamed") == "accepted"


def test_required_namespace_round_trips_from_kubernetes_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A required model namespace is populated from the Kubernetes envelope."""
    from based_operators.validation import validate_resource

    root = project(tmp_path)
    sample = root / "src" / "sample.py"
    sample.write_text(sample.read_text().replace("namespace: str = 'default'", "namespace: str"))
    monkeypatch.syspath_prepend(str(root))
    build(root, root / "out", image="x")

    resource = validate_resource(
        _load_model("sample:Widget", root),
        {
            "apiVersion": "widgets.example.com/v1",
            "kind": "Widget",
            "metadata": {"namespace": "operator-system"},
            "spec": {"value": 1},
        },
    )

    assert getattr(resource, "namespace") == "operator-system"


def test_required_fields_fall_back_for_non_string_serialization_alias() -> None:
    """Use the field name when a serialization alias is not a string."""

    class Model:
        model_fields = {
            "value": SimpleNamespace(
                serialization_alias=AliasPath("value"),
                alias=None,
                is_required=lambda: True,
                annotation=int,
            )
        }

    schema = {"properties": {"value": {"type": "integer"}}}
    _normalize_model_required(cast(type[BaseModel], Model), schema)
    assert schema["required"] == ["value"]


@pytest.mark.parametrize(
    ("values", "supported"),
    [
        ({"FIRST": "first", "SECOND": "second"}, True),
        ({"FIRST": 1, "SECOND": 2}, True),
        ({"FIRST": 1.0, "SECOND": 2.0}, True),
        ({"FIRST": True, "SECOND": False}, True),
        ({}, False),
        ({"FIRST": b"first"}, False),
        ({"FIRST": "first", "SECOND": 2}, False),
    ],
)
def test_enum_schema_validation(values: dict, supported: bool) -> None:
    """Keep enum validation strict after extracting its primitive-type checks."""
    enum = Enum("Values", values)
    if supported:
        _check_type(enum, "Spec.value", set())
    else:
        with pytest.raises(BuildError, match="enum values must have one supported primitive type"):
            _check_type(enum, "Spec.value", set())


@pytest.mark.parametrize("annotation", [int, int | None, Annotated[int | None, "metadata"]])
def test_envelope_nullability(annotation: object) -> None:
    """Optional envelope types stay nullable without changing the input schema."""
    schema = {"type": "integer"}
    expected = {"type": "integer", "nullable": True} if annotation is not int else schema
    assert _normalize_nullable(annotation, schema) == expected
    assert schema == {"type": "integer"}


def test_build_rejects_incompatible_serialization_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not generate a CRD property that Pydantic handlers cannot validate."""
    root = project(tmp_path, fields="renamed: str = Field(serialization_alias='serializedName')")
    sample = root / "src" / "sample.py"
    sample.write_text(
        sample.read_text().replace(
            "from pydantic import BaseModel\n", "from pydantic import BaseModel, Field\n"
        )
    )
    monkeypatch.syspath_prepend(str(root))
    with pytest.raises(BuildError, match="incompatible input/output aliases"):
        build(root, root / "out", image="x")
    assert not (root / "out").exists()


def test_build_rejects_incompatible_resource_envelope_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do not generate a CRD whose fixed spec key is rejected by Pydantic."""
    root = project(tmp_path)
    sample = root / "src" / "sample.py"
    sample.write_text(
        sample.read_text()
        .replace("from pydantic import BaseModel\n", "from pydantic import BaseModel, Field\n")
        .replace("    spec: Spec\n", "    spec: Spec = Field(alias='desired')\n")
    )
    monkeypatch.syspath_prepend(str(root))
    with pytest.raises(BuildError, match="incompatible input alias for Kubernetes key 'spec'"):
        build(root, root / "out", image="x")
    assert not (root / "out").exists()
