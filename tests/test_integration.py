"""End-to-end integration suite: model, image, deployment, reconciliation.

The suite drives the whole tool against real infrastructure. It is skipped unless
``BASED_OPERATORS_K8S_INTEGRATION=1`` is set and a Kubernetes cluster is reachable
through ``kubectl``.

It has two halves:

* the **API-server** tests register a generated CRD on the cluster and verify
  establishment, defaulting, pruning, schema rejection and the status subresource;
* the **end-to-end** tests scaffold the repository's example operator, build its
  image from the generated Dockerfile, deploy the generated Helm chart and wait
  for the running operator to reconcile a live custom resource.

The end-to-end half additionally needs ``docker`` and ``helm``. Where the cluster
shares the Docker daemon (Docker Desktop, Rancher Desktop, ...) the freshly built
image is visible to it immediately; a ``kind`` or ``minikube`` cluster gets the
image loaded explicitly.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from based_operators.crd import CustomResource, ResourceConfig, Scope, crd_manifest
from based_operators.scaffold import Scaffold, scaffold_operator

PINNED_KUBERNETES_VERSION = os.environ.get("BASED_OPERATORS_K8S_VERSION", "1.31")

REPO_ROOT = Path(__file__).resolve().parents[1]
"""The repository root; the build context of the example operator's image."""

EXAMPLE_OPERATOR = REPO_ROOT / "app" / "operator_example.py"
"""The complete operator entry point the end-to-end tests deploy."""

HELM = shutil.which("helm")
"""The ``helm`` executable, or ``None`` when it is not installed."""

DOCKER = shutil.which("docker")
"""The ``docker`` executable, or ``None`` when it is not installed."""

KIND = shutil.which("kind")
"""The ``kind`` executable, or ``None`` when it is not installed."""

pytestmark = pytest.mark.skipif(
    os.environ.get("BASED_OPERATORS_K8S_INTEGRATION") != "1",
    reason="set BASED_OPERATORS_K8S_INTEGRATION=1 with a reachable cluster",
)

requires_image_tooling = pytest.mark.skipif(
    DOCKER is None or HELM is None,
    reason="the end-to-end tests need docker and helm",
)


class IntegrationSpec(BaseModel):
    """A specification exercising constraints and defaults."""

    name: str
    replicas: int = Field(default=1, ge=0, le=5)
    mode: Literal["fast", "slow"] = "fast"


class IntegrationStatus(BaseModel):
    """A minimal status."""

    ready: bool = False


class Integration(CustomResource[IntegrationSpec, IntegrationStatus]):
    """The representative resource registered by this suite."""

    resource_config = ResourceConfig(
        group="integration.example.com",
        version="v1",
        kind="Integration",
        plural="integrations",
        scope=Scope.NAMESPACED,
    )


def _kubectl(
    *args: str, check: bool = True, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run ``kubectl`` and return the completed process.

    Args:
        *args: Arguments passed to ``kubectl``.
        check: Whether a non-zero exit should raise.
        stdin: Optional standard input.

    Returns:
        The completed process.
    """
    result = subprocess.run(
        ["kubectl", *args], capture_output=True, text=True, input=stdin, timeout=60
    )
    if check and result.returncode != 0:
        raise AssertionError(f"kubectl {' '.join(args)} failed: {result.stderr}")
    return result


def _server_version() -> str:
    """Return the server ``major.minor`` version string."""
    payload = json.loads(_kubectl("version", "-o", "json").stdout)
    server = payload["serverVersion"]
    return f"{server['major']}.{server['minor']}"


def _wait_for(description: str, probe: Callable[[], bool], timeout: float = 300.0) -> None:
    """Poll ``probe`` until it is true, or fail after ``timeout`` seconds.

    Args:
        description: What is being awaited, used in the failure message.
        probe: A callable returning whether the awaited condition holds.
        timeout: The maximum number of seconds to wait.

    Raises:
        AssertionError: If the condition does not hold before the timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe():
            return
        time.sleep(5)
    raise AssertionError(f"timed out after {timeout:.0f}s waiting for {description}")


# -- the API server --------------------------------------------------------------


@pytest.fixture(scope="module")
def namespace() -> Iterator[str]:
    """Create a throwaway namespace and register the generated CRD."""
    name = f"crd-integration-{uuid.uuid4().hex[:8]}"
    try:
        _kubectl("create", "namespace", name)
        _kubectl("apply", "-f", "-", stdin=json.dumps(crd_manifest(Integration)))

        for _ in range(30):
            result = _kubectl(
                "get", "crd", "integrations.integration.example.com", "-o", "json", check=False
            )
            if result.returncode == 0:
                conditions = json.loads(result.stdout).get("status", {}).get("conditions", [])
                if any(c["type"] == "Established" and c["status"] == "True" for c in conditions):
                    break
            time.sleep(2)
        else:
            raise AssertionError("CRD was not established in time")

        yield name
    finally:
        # Runs even if setup fails, so a failed run cannot leak the cluster-scoped
        # CRD or the namespace and contaminate later runs.
        _kubectl(
            "delete",
            "crd",
            "integrations.integration.example.com",
            "--ignore-not-found",
            check=False,
        )
        _kubectl("delete", "namespace", name, "--ignore-not-found", check=False)


def test_server_version_is_pinned() -> None:
    """The cluster matches the pinned Kubernetes version."""
    assert _server_version() == PINNED_KUBERNETES_VERSION


def test_valid_resource_defaults_and_pruning(namespace: str) -> None:
    """A valid resource is stored, defaulted and pruned."""
    name = f"ok-{uuid.uuid4().hex[:8]}"
    body = {
        "apiVersion": "integration.example.com/v1",
        "kind": "Integration",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"name": "hello", "unknownField": "pruned"},
    }
    _kubectl("apply", "-f", "-", stdin=json.dumps(body))

    stored = json.loads(_kubectl("get", "integration", name, "-n", namespace, "-o", "json").stdout)

    assert stored["spec"]["replicas"] == 1
    assert stored["spec"]["mode"] == "fast"
    assert "unknownField" not in stored["spec"]


def test_invalid_resource_is_rejected(namespace: str) -> None:
    """A resource violating a constraint is rejected."""
    body = {
        "apiVersion": "integration.example.com/v1",
        "kind": "Integration",
        "metadata": {"name": f"bad-{uuid.uuid4().hex[:8]}", "namespace": namespace},
        "spec": {"name": "hello", "replicas": 99},
    }
    result = _kubectl("apply", "-f", "-", stdin=json.dumps(body), check=False)

    assert result.returncode != 0
    assert "replicas" in (result.stderr + result.stdout)


def test_status_subresource(namespace: str) -> None:
    """The status subresource is served and accepts a status patch."""
    name = f"status-{uuid.uuid4().hex[:8]}"
    body = {
        "apiVersion": "integration.example.com/v1",
        "kind": "Integration",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"name": "hello"},
    }
    _kubectl("apply", "-f", "-", stdin=json.dumps(body))
    _kubectl(
        "patch",
        "integration",
        name,
        "-n",
        namespace,
        "--subresource=status",
        "--type=merge",
        "-p",
        json.dumps({"status": {"ready": True}}),
    )

    stored = json.loads(_kubectl("get", "integration", name, "-n", namespace, "-o", "json").stdout)

    assert stored["status"]["ready"] is True


# -- the whole tool --------------------------------------------------------------


def _current_context() -> str:
    """Return the name of the current ``kubectl`` context."""
    return _kubectl("config", "current-context").stdout.strip()


def _make_image_visible(image: str) -> None:
    """Make a locally built image visible to the cluster.

    A cluster that shares the Docker daemon needs nothing; a ``kind`` or
    ``minikube`` cluster gets the image loaded into it.

    Args:
        image: The image reference that was just built.
    """
    context = _current_context()
    if context.startswith("kind-") and KIND is not None:
        subprocess.run(
            ["kind", "load", "docker-image", image, "--name", context[len("kind-") :]],
            check=True,
            timeout=600,
        )
    elif "minikube" in context and shutil.which("minikube") is not None:
        subprocess.run(["minikube", "image", "load", image], check=True, timeout=600)


@pytest.fixture(scope="module")
def deployed() -> Iterator[dict[str, Any]]:
    """Scaffold, build, deploy and later remove the example operator.

    Yields:
        The namespace, release name and image the operator was deployed as.
    """
    namespace = f"e2e-{uuid.uuid4().hex[:8]}"
    release = "widget-operator"
    deployment = f"{release}-based-operator"
    image = f"based-operators-e2e:{uuid.uuid4().hex[:10]}"
    output = REPO_ROOT / "build" / f"e2e-{uuid.uuid4().hex[:10]}"

    scaffold: Scaffold = scaffold_operator(
        operator=EXAMPLE_OPERATOR,
        output=output,
        context=REPO_ROOT,
        image=image,
        namespace=namespace,
    )
    try:
        subprocess.run(
            ["docker", "build", "-f", scaffold.spec.dockerfile_ref, "-t", image, "."],
            cwd=REPO_ROOT,
            check=True,
            timeout=1800,
        )
        _make_image_visible(image)

        _kubectl("create", "namespace", namespace)
        subprocess.run(
            ["helm", "install", release, str(scaffold.spec.chart_dir), "--namespace", namespace],
            check=True,
            timeout=600,
        )
        _kubectl("rollout", "status", f"deployment/{deployment}", "-n", namespace, "--timeout=180s")

        yield {"namespace": namespace, "release": release, "deployment": deployment, "image": image}
    finally:
        subprocess.run(
            ["helm", "uninstall", release, "--namespace", namespace], check=False, timeout=600
        )
        _kubectl("delete", "namespace", namespace, "--ignore-not-found", check=False)
        # Helm never deletes the CRDs it installs from crds/, so remove it by hand.
        _kubectl("delete", "crd", "widgets.example.com", "--ignore-not-found", check=False)
        subprocess.run(["docker", "rmi", "-f", image], check=False, timeout=600)
        shutil.rmtree(output, ignore_errors=True)


@requires_image_tooling
def test_generated_crd_is_installed(deployed: dict[str, Any]) -> None:
    """The chart installs the CRD generated from the example's model."""
    result = _kubectl("get", "crd", "widgets.example.com", check=False)

    assert result.returncode == 0, result.stderr
    assert "widgets.example.com" in result.stdout


@requires_image_tooling
def test_operator_pod_is_running(deployed: dict[str, Any]) -> None:
    """The deployed operator pod becomes ready."""
    namespace = deployed["namespace"]
    pods = _kubectl("get", "pods", "-n", namespace, "-l", "app.kubernetes.io/name=based-operator")

    assert (
        deployed["deployment"]
        in _kubectl("get", "deployment", deployed["deployment"], "-n", namespace).stdout
    )
    assert "1/1" in pods.stdout


@requires_image_tooling
def test_operator_reconciles_a_resource(deployed: dict[str, Any]) -> None:
    """A live custom resource is reconciled by the running operator."""
    namespace = deployed["namespace"]
    name = f"widget-{uuid.uuid4().hex[:8]}"
    body = {
        "apiVersion": "example.com/v1",
        "kind": "Widget",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"message": "hello", "replicas": 3},
    }
    _kubectl("apply", "-f", "-", stdin=json.dumps(body))

    def reconciled() -> bool:
        result = _kubectl("get", "widget", name, "-n", namespace, "-o", "json", check=False)
        if result.returncode != 0:
            return False
        status = json.loads(result.stdout).get("status") or {}
        return status.get("phase") == "Ready" and status.get("ready") == 3

    _wait_for("the operator to reconcile the widget", reconciled)

    stored = json.loads(_kubectl("get", "widget", name, "-n", namespace, "-o", "json").stdout)
    assert stored["status"]["phase"] == "Ready"
    assert stored["status"]["ready"] == 3
