"""Smoke-test the image the generated Dockerfile builds.

The build is slow, so the suite is skipped unless ``BASED_OPERATORS_IMAGE_SMOKE=1``
is set — as the CI image-smoke job does — and a Docker daemon is reachable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

import pytest

from based_operators.scaffold import Scaffold

REPO_ROOT = Path(__file__).resolve().parents[1]
"""The repository root; the build context the generated Dockerfile expects."""

DOCKER = shutil.which("docker")
"""The ``docker`` executable, or ``None`` when it is not installed."""

pytestmark = pytest.mark.skipif(
    os.environ.get("BASED_OPERATORS_IMAGE_SMOKE") != "1" or DOCKER is None,
    reason="set BASED_OPERATORS_IMAGE_SMOKE=1 with a working Docker daemon",
)


def _run(*args: str, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess[str]:
    """Run a command from the repository root and return the completed process.

    Args:
        *args: The command and its arguments.
        check: Whether a non-zero exit should raise.
        timeout: The maximum number of seconds to wait.

    Returns:
        The completed process, with its exit code and captured output.

    Raises:
        AssertionError: If ``check`` is set and the command failed.
    """
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=REPO_ROOT)
    if check and result.returncode != 0:
        raise AssertionError(f"{' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


def test_image_builds_and_runs_the_entry_point(scaffold_example: Callable[..., Scaffold]) -> None:
    """The generated Dockerfile builds an image that runs the operator's code."""
    scaffold = scaffold_example(image=f"based-operators-smoke:{uuid.uuid4().hex[:10]}")
    image = scaffold.spec.image
    try:
        _run("docker", "build", "-f", scaffold.spec.dockerfile_ref, "-t", image, ".")

        kopf = _run("docker", "run", "--rm", image, "kopf", "--version")
        assert kopf.returncode == 0, kopf.stdout + kopf.stderr

        imports = _run(
            "docker",
            "run",
            "--rm",
            image,
            "python",
            "-c",
            "import based_operators, kopf; print(based_operators.__name__)",
        )
        assert imports.stdout.strip() == "based_operators"

        # The image's command runs this exact file, so it has to be present and
        # importable inside the image.
        entry = _run(
            "docker",
            "run",
            "--rm",
            image,
            "python",
            "-c",
            "import runpy; runpy.run_path('app/operator_example.py')",
        )
        assert entry.returncode == 0, entry.stdout + entry.stderr
    finally:
        _run("docker", "rmi", "-f", image, check=False)
