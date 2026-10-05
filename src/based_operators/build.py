"""Build CRDs, a container recipe, and a standalone Helm chart from explicit models."""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Union, get_args, get_origin
from unittest.mock import patch

from kdantic.cli import _make_version_block, build_crd_object
from kdantic.helpers.schema import build_k8s_model_schema
from kdantic.helpers.settings import Settings
from pydantic import BaseModel

from based_operators.metadata import resolve_metadata


class BuildError(ValueError):
    """Invalid build configuration or unsupported resource schema."""


def _model_type(annotation: object, name: str) -> type[BaseModel]:
    """Unwrap an optional model type, rejecting lossy unions."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (Union, types.UnionType):
        members = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(members) != 1:
            raise BuildError(f"{name}: unions of multiple types are unsupported")
        annotation = members[0]
    if not isinstance(annotation, type) or not issubclass(annotation, BaseModel):
        raise BuildError(f"{name}: expected a Pydantic model type")
    return annotation


def _check_unions(model: type[BaseModel], seen: set[type[BaseModel]]) -> None:
    """Reject unions that kdantic would silently reduce to their first member."""
    if model in seen:
        return
    seen.add(model)
    for name, field in model.model_fields.items():

        def walk(annotation: object) -> None:
            origin = get_origin(annotation)
            if origin in (Union, types.UnionType):
                members = [arg for arg in get_args(annotation) if arg is not type(None)]
                if len(members) != 1:
                    raise BuildError(f"{model.__name__}.{name}: unsupported union {annotation}")
                walk(members[0])
            elif origin is Annotated:
                walk(get_args(annotation)[0])
            elif origin is not None:
                for arg in get_args(annotation):
                    walk(arg)
            elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
                _check_unions(annotation, seen)

        walk(field.annotation)


def _load_model(reference: str, root: Path) -> type[BaseModel]:
    """Import exactly one model class rather than scanning a module."""
    module_name, sep, class_name = reference.partition(":")
    if (
        not sep
        or not all(part.isidentifier() for part in module_name.split("."))
        or not class_name.isidentifier()
    ):
        raise BuildError(f"Invalid model reference {reference!r}; expected module:Class")
    sys.path[:0] = [str(root / "src"), str(root)]
    try:
        module = importlib.import_module(module_name)
        model = getattr(module, class_name, None)
    except (ImportError, AttributeError) as exc:
        raise BuildError(f"Cannot import {reference}: {exc}") from exc
    finally:
        del sys.path[:2]
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise BuildError(f"{reference}: expected a Pydantic BaseModel subclass")
    if not Path(module.__file__).resolve().is_relative_to(root):
        raise BuildError(f"{reference}: module resolves outside the project")
    return model


def _read_config(root: Path) -> tuple[list[str], str]:
    """Read the required explicit build inputs from pyproject.toml."""
    with (root / "pyproject.toml").open("rb") as file:
        config = tomllib.load(file).get("tool", {}).get("based-operators", {})
    if not isinstance(config, dict):
        raise BuildError("[tool.based-operators] must be a table")
    models = config.get("models")
    handler = config.get("handler")
    if not isinstance(models, list) or not models or not all(isinstance(m, str) for m in models):
        raise BuildError(
            "[tool.based-operators].models must be a nonempty list of module:Class strings"
        )
    if len(models) != len(set(models)):
        raise BuildError("Duplicate model references")
    if not isinstance(handler, str) or not handler:
        raise BuildError(
            "[tool.based-operators].handler must name a Python file relative to the project"
        )
    handler_path = Path(handler)
    if (
        handler_path.is_absolute()
        or handler_path.suffix != ".py"
        or ".." in handler_path.parts
        or handler_path.parts[0] != "app"
    ):
        raise BuildError("handler must be a relative .py path under app/")
    if not (root / handler_path).is_file() or not (root / handler_path).resolve().is_relative_to(
        root
    ):
        raise BuildError(f"Handler does not exist inside the project: {handler}")
    return models, handler_path.as_posix()


@contextmanager
def _kdantic_defaults():
    """Keep kdantic's lazy CLI settings from parsing this application's arguments."""
    settings = Settings.model_construct()
    with patch("kdantic.helpers.settings.load_settings", return_value=settings):
        yield


def _dockerfile(handler: str) -> str:
    """Render a production-only uv image running one standalone Kopf process."""
    return (
        "FROM python:3.13-slim\n"
        "COPY --from=ghcr.io/astral-sh/uv:0.9.0 /uv /usr/local/bin/uv\n"
        "WORKDIR /operator\n"
        "COPY pyproject.toml uv.lock README.md ./\n"
        "COPY src/ ./src/\n"
        "COPY app/ ./app/\n"
        "RUN uv sync --frozen --no-dev --no-install-project\n"
        'ENV PATH="/operator/.venv/bin:$PATH" PYTHONPATH="/operator/src"\n'
        f"CMD {json.dumps(['kopf', 'run', '--standalone', '--namespace', 'default', handler])}\n"
    )


def _chart(image: str, handler: str, resources: list[tuple[str, str]]) -> dict[str, str]:
    """Render namespace-scoped RBAC and a deliberately single-replica chart."""
    names = sorted({plural for _, plural in resources})
    groups = sorted({group for group, _ in resources})
    resource_list = json.dumps(names)
    group_list = json.dumps(groups)
    command = json.dumps(
        ["kopf", "run", "--standalone", "--namespace", "$(POD_NAMESPACE)", handler]
    )
    release = "{{ .Release.Name }}"
    return {
        "Chart.yaml": "apiVersion: v2\nname: based-operator\nversion: 0.1.0\ntype: application\n",
        "values.yaml": f"image: {json.dumps(image)}\n",
        "templates/operator.yaml": f"""apiVersion: v1
kind: ServiceAccount
metadata:
  name: {release}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {release}
rules:
  - apiGroups: {group_list}
    resources: {resource_list}
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: {group_list}
    resources: {json.dumps([name + '/status' for name in names])}
    verbs: ["get", "patch", "update"]
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["create", "patch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {release}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {release}
subjects:
  - kind: ServiceAccount
    name: {release}
    namespace: {{{{ .Release.Namespace }}}}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {release}
spec:
  replicas: 1
  strategy:
    type: Recreate
  selector:
    matchLabels:
      app: {release}
  template:
    metadata:
      labels:
        app: {release}
    spec:
      serviceAccountName: {release}
      containers:
        - name: operator
          image: {{{{ .Values.image | quote }}}}
          imagePullPolicy: IfNotPresent
          command: {command}
          env:
            - name: POD_NAMESPACE
              valueFrom:
                fieldRef:
                  fieldPath: metadata.namespace
""",
    }


def _generate_crds(
    root: Path, references: list[str]
) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """Validate each model and build its manifest before writing anything."""
    crds: dict[str, dict] = {}
    resources: list[tuple[str, str]] = []
    with _kdantic_defaults():
        for reference in references:
            model = _load_model(reference, root)
            spec = model.model_fields.get("spec")
            if spec is None:
                raise BuildError(f"{reference}: missing spec field")
            spec_type = _model_type(spec.annotation, f"{reference}.spec")
            status = model.model_fields.get("status")
            status_type = _model_type(status.annotation, f"{reference}.status") if status else None
            seen: set[type[BaseModel]] = set()
            _check_unions(spec_type, seen)
            if status_type:
                _check_unions(status_type, seen)
            meta = resolve_metadata(model)
            if meta.scope != "Namespaced":
                raise BuildError(f"{reference}: only Namespaced resources are supported")
            if not re.fullmatch(r"[a-z0-9]([-a-z0-9.]*[a-z0-9])?", meta.group):
                raise BuildError(f"{reference}: invalid API group {meta.group!r}")
            if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", meta.names.plural):
                raise BuildError(f"{reference}: invalid resource plural {meta.names.plural!r}")
            key = f"{meta.names.plural}.{meta.group}"
            if key in crds:
                raise BuildError(f"Duplicate CRD {key}")
            build_k8s_model_schema(spec_type, {})
            crds[key] = build_crd_object(
                meta, _make_version_block(meta, spec_type, status_type, {})
            )
            resources.append((meta.group, meta.names.plural))
    return crds, resources


def build(
    project_root: Path = Path("."),
    output_dir: Path = Path("dist/operator"),
    *,
    image: str,
    mode: str = "standalone",
) -> Path:
    """Generate artifacts in a new directory; reject unsupported HA and resource layouts."""
    if mode != "standalone":
        raise BuildError("Only standalone mode is supported; HA requires leader election")
    if not image or not isinstance(image, str) or any(c.isspace() for c in image):
        raise BuildError("image must be a nonempty image reference without whitespace")
    root = project_root.resolve()
    output = output_dir.resolve()
    if output.exists():
        raise BuildError(f"Output already exists: {output}")
    if output == root or not output.is_relative_to(root):
        raise BuildError("Output must be a new directory inside the project")
    references, handler = _read_config(root)
    if not (root / "uv.lock").is_file() or not (root / "README.md").is_file():
        raise BuildError("Docker build requires uv.lock and README.md")
    crds, resources = _generate_crds(root, references)

    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".operator-build-", dir=output.parent))
    try:
        for key in sorted(crds):
            path = stage / "helm" / "crds" / f"{key}.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(crds[key], indent=2, sort_keys=True) + "\n")
        (stage / "Dockerfile").write_text(_dockerfile(handler))
        for name, text in _chart(image, handler, resources).items():
            path = stage / "helm" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        if output.exists():
            raise BuildError(f"Output already exists: {output}")
        os.rename(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return output
