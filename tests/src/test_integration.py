"""Version-pinned API-server integration suite.

These tests register generated CRDs on a real Kubernetes API server and verify
establishment, defaulting, pruning, schema rejection and the status subresource.
They are skipped unless ``BASED_OPERATORS_K8S_INTEGRATION=1`` is set and a cluster
is reachable through ``kubectl``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from based_operators.crd import CustomResource, ResourceConfig, Scope, crd_manifest

PINNED_KUBERNETES_VERSION = os.environ.get("BASED_OPERATORS_K8S_VERSION", "1.31")

pytestmark = pytest.mark.skipif(
    os.environ.get("BASED_OPERATORS_K8S_INTEGRATION") != "1",
    reason="set BASED_OPERATORS_K8S_INTEGRATION=1 with a reachable cluster",
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


@pytest.fixture(scope="module")
def namespace() -> Any:
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
