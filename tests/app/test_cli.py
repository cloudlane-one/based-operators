"""Tests for the ``based-operators`` command-line application."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner, Result

from based_ops_cli.__main__ import app

ROOT = Path(__file__).resolve().parents[2]
"""The root of the repository the CLI is run from."""

RUNNER = CliRunner()

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
"""Matches the SGR escape sequences rich styles the rendered help with."""

OPERATOR_SOURCE = '''
"""The operator entry point of the test project."""

from typing import Any

from pydantic import BaseModel

import based_operators as bk


class WidgetSpec(BaseModel):
    """The spec of the widget."""

    message: str


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
async def reconcile(resource: Any, **kwargs: Any) -> None:
    """Reconcile a widget."""
'''


@pytest.fixture(autouse=True)
def _restore_imports() -> Iterator[None]:
    """Undo what loading an entry point leaves behind in :mod:`sys`."""
    paths = list(sys.path)
    yield
    sys.path[:] = paths
    for name in list(sys.modules):
        if name.startswith("__based_operator_script__"):
            del sys.modules[name]


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a one-resource project and make it the working directory.

    The command derives the build context from the working directory, so the
    working directory is exactly what a user's project root would be.

    Args:
        tmp_path: The temporary directory pytest provides.
        monkeypatch: The pytest patcher, used to change the working directory.

    Returns:
        The root of the temporary project.
    """
    (tmp_path / "operator.py").write_text(OPERATOR_SOURCE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*args: str) -> Result:
    """Invoke the command, as the console script would.

    Args:
        *args: The arguments of the ``scaffold`` command.

    Returns:
        The click result, with the exit code and the captured output.
    """
    return RUNNER.invoke(app, ["scaffold", *args])


def test_scaffold_writes_and_reports(project: Path) -> None:
    """One command scaffolds the operator and says how to deploy it."""
    result = run("operator.py", "-o", "deploy", "--image", "ghcr.io/acme/demo:v1")

    assert result.exit_code == 0, result.output
    assert "Scaffolded the Widget operator into deploy/:" in result.output
    assert "  Dockerfile" in result.output
    assert "docker build -f deploy/Dockerfile -t ghcr.io/acme/demo:v1 ." in result.output
    assert (
        "helm install operator ./deploy/helm/based-operator --namespace default --create-namespace"
    ) in result.output
    assert (project / "deploy" / "Dockerfile").is_file()
    assert (project / "deploy" / "helm" / "based-operator" / "crds").is_dir()


def test_webhook_flag_reaches_the_image(project: Path) -> None:
    """The webhook switch is forwarded to the generated command."""
    result = run("operator.py", "-o", "deploy", "--webhook")

    assert result.exit_code == 0, result.output
    assert "-m" in (project / "deploy" / "Dockerfile").read_text(encoding="utf-8")


def test_failures_are_one_line_and_non_zero(project: Path) -> None:
    """A scaffold that cannot be written is reported without a traceback."""
    (project / "empty.py").write_text('"""Nothing here."""\n', encoding="utf-8")

    result = run("empty.py", "-o", "deploy")

    assert result.exit_code == 1
    assert "declares no custom resource" in result.output
    assert "Traceback" not in result.output


def test_help_documents_the_options() -> None:
    """The command documents its switches.

    The width is pinned and the styling stripped: rich lays the help out for the
    terminal it finds, abbreviating a long option name on a narrow one and
    splitting a name across escape sequences when it colours it, so the rendered
    text is not a stable place to look for the names.
    """
    result = RUNNER.invoke(app, ["scaffold", "--help"], env={**os.environ, "COLUMNS": "200"})

    assert result.exit_code == 0
    help_text = _ANSI.sub("", result.output)
    assert "--webhook" in help_text
    assert "--image" in help_text
    assert "--python-version" in help_text


def test_entry_point_is_required() -> None:
    """The operator entry point is a required argument."""
    result = RUNNER.invoke(app, ["scaffold"])

    assert result.exit_code == 2
    assert "Missing argument 'operator'" in result.output


def test_version_is_reported() -> None:
    """--version prints the installed version."""
    result = RUNNER.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.output.startswith("based-operators ")


def test_runs_as_the_console_script() -> None:
    """The installed ``based-operators`` script runs the command."""
    completed = subprocess.run(
        ["uv", "run", "based-operators", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("based-operators ")


def test_runs_as_a_module() -> None:
    """``python -m based_ops_cli`` runs the command as well."""
    completed = subprocess.run(
        [sys.executable, "-m", "based_ops_cli", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("based-operators ")


def test_runs_as_a_script() -> None:
    """The module is runnable as a file, as the guides show."""
    completed = subprocess.run(
        ["uv", "run", "app/based_ops_cli/__main__.py", "--version"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("based-operators ")
