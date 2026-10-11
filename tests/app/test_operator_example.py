"""Test the ``operator_example`` entry point."""

from __future__ import annotations

import subprocess
from pathlib import Path

from based_operators.scaffold import discover_resources, load_operator

ROOT = Path(__file__).resolve().parents[2]
"""The root of the repository, which is the build context of the example."""

EXAMPLE = ROOT / "app" / "operator_example.py"
"""The example entry point the guides hand to the scaffolder."""


def test_example_runs_without_a_cluster() -> None:
    """The example is executable Python, handlers and all."""
    result = subprocess.run(
        ["uv", "run", "app/operator_example.py"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert "Handlers attached to Widget" in result.stdout


def test_example_is_a_scaffoldable_entry_point() -> None:
    """The example declares the resource the scaffolder turns into a CRD."""
    resources = discover_resources(load_operator(EXAMPLE))

    assert [resource.__name__ for resource in resources] == ["Widget"]
