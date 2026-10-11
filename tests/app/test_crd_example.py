"""Test the ``crd_example`` notebook."""

from __future__ import annotations

import subprocess


def test_notebook_runs_without_error() -> None:
    """Make sure the ``crd_example`` notebook runs without error."""
    result = subprocess.run(
        ["uv", "run", "app/crd_example.py"],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, f"Notebook failed with error: {result.stderr}"
    assert "CustomResourceDefinition" in result.stdout
