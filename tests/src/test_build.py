"""Tests for downstream operator artifact generation."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from based_operators.build import BuildError, build
from based_operators.cli import main


def project(tmp_path: Path, *, models: str = '"sample:Widget"', fields: str = "value: int") -> Path:
    """Create a minimal isolated project with explicit model imports."""
    sys.modules.pop("sample", None)
    (tmp_path / "src").mkdir()
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "operator.py").write_text("import kopf\n")
    (tmp_path / "README.md").write_text("Test project\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / "sample.py").write_text(
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
    assert "--standalone" in (output / "Dockerfile").read_text()
    assert '"example/operator:1"' in (output / "helm/values.yaml").read_text()
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
    sample = root / "sample.py"
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
