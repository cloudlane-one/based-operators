"""Smoke-test the generated downstream image when Docker is available."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from based_operators.build import build


def test_generated_container_starts_with_downstream_handlers(tmp_path: Path) -> None:
    """Build the locked image and verify its installed package, user and handler."""
    required = os.environ.get("BASED_OPERATORS_CONTAINER_SMOKE") == "1"
    if not required:
        pytest.skip("Run with BASED_OPERATORS_CONTAINER_SMOKE=1 to build a container")
    if not shutil.which("docker"):
        pytest.fail("Docker is required for the container smoke test")
    daemon = subprocess.run(["docker", "info"], capture_output=True, text=True, check=False)
    if daemon.returncode:
        pytest.fail(f"Docker daemon is unavailable: {daemon.stderr}")

    source = Path(__file__).resolve().parents[2]
    project = tmp_path / "downstream"
    project.mkdir()
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        shutil.copy2(source / name, project / name)
    shutil.copytree(source / "src", project / "src", ignore=shutil.ignore_patterns("__pycache__"))
    (project / "src" / "smoke_models.py").write_text(
        "from pydantic import BaseModel\n"
        "class Spec(BaseModel):\n"
        "    greeting: str\n"
        "class Greeting(BaseModel):\n"
        "    apiVersion: str = 'smoke.example.com/v1'\n"
        "    kind: str = 'Greeting'\n"
        "    namespace: str = 'default'\n"
        "    spec: Spec\n"
    )
    (project / "app").mkdir()
    (project / "app" / "operator.py").write_text(
        "import based_operators as tk\n"
        "from smoke_models import Greeting\n"
        "@tk.on.create(model=Greeting)\n"
        "def reconcile(resource: Greeting, **kwargs):\n"
        "    return resource.spec.greeting\n"
    )
    with (project / "pyproject.toml").open("a") as pyproject:
        pyproject.write(
            '\n[tool.based-operators]\nmodels = ["smoke_models:Greeting"]\n'
            'handler = "app/operator.py"\n'
        )
    artifacts = build(project, project / "dist/operator", image="based-operators:smoke")
    image = f"based-operators-smoke:{os.getpid()}"
    try:
        subprocess.run(
            [
                "docker", "build",
                "--build-arg", "UV_DYNAMIC_VERSIONING_BYPASS=0.0.0",
                "--file", str(artifacts / "Dockerfile"), "--tag", image, ".",
            ],
            cwd=project, check=True, timeout=600,
        )
        subprocess.run(
            [
                "docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image,
                "-c", (
                    "import os, kopf, based_operators; "
                    "os.environ['BASED_OPERATORS_HANDLER'] = 'app/operator.py'; "
                    "import based_operators.runtime; "
                    "assert os.getuid() == 10001; "
                    "assert len(kopf.get_default_registry()._changing.get_all_handlers()) == 1"
                ),
            ],
            cwd=project, check=True, capture_output=True, text=True, timeout=60,
        )
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", image], capture_output=True, check=False
        )
