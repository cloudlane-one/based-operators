"""Lint and render the Helm chart the scaffolder generates.

``helm lint`` is part of the CI gate; the suite skips only when ``helm`` is not
installed *and* the run is not a CI run, so a CI image that loses ``helm`` fails
loudly instead of silently skipping the check.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable

import pytest

from based_operators.scaffold import Scaffold

HELM = shutil.which("helm")
"""The ``helm`` executable, or ``None`` when it is not installed."""

pytestmark = pytest.mark.skipif(
    HELM is None and not os.environ.get("CI"),
    reason="helm is not installed (it is required in CI)",
)


def _helm(*args: str) -> subprocess.CompletedProcess[str]:
    """Run ``helm`` and return the completed process.

    Args:
        *args: Arguments passed to ``helm``.

    Returns:
        The completed process, with its exit code and captured output.
    """
    return subprocess.run(["helm", *args], capture_output=True, text=True, timeout=180)


def test_chart_lints(scaffold_example: Callable[..., Scaffold]) -> None:
    """``helm lint`` accepts the chart the scaffolder writes."""
    scaffold = scaffold_example()

    result = _helm("lint", str(scaffold.spec.chart_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 chart(s) failed" in result.stdout


def test_webhook_chart_lints(scaffold_example: Callable[..., Scaffold]) -> None:
    """The webhook variant of the chart lints as well."""
    scaffold = scaffold_example(webhook=True)

    result = _helm("lint", str(scaffold.spec.chart_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 chart(s) failed" in result.stdout


def test_chart_renders(scaffold_example: Callable[..., Scaffold]) -> None:
    """``helm template`` renders the operator's deployment and its RBAC."""
    scaffold = scaffold_example()

    result = _helm("template", "release", str(scaffold.spec.chart_dir))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "kind: Deployment" in result.stdout
    assert "kind: ClusterRole" in result.stdout
