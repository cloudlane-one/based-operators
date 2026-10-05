"""Build CRDs, a container recipe, and a standalone Helm chart from explicit models."""

from __future__ import annotations

import importlib
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import tomllib
import types
from collections.abc import Mapping
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Union, get_args, get_origin
from unittest.mock import patch

from kdantic.cli import _make_version_block, build_crd_object
from kdantic.helpers.schema import build_k8s_model_schema
from kdantic.helpers.settings import Settings
from pydantic import BaseModel

from based_operators.metadata import resolve_metadata


class BuildError(ValueError):
    """Invalid build configuration or unsupported resource schema."""


_MODEL_IMPORT_LOCK = threading.RLock()


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


def _check_container(
    origin: object, args: tuple[object, ...], name: str, seen: set[type[BaseModel]]
) -> None:
    """Validate kdantic's supported array and string-key map containers."""
    if origin is list:
        if len(args) != 1:
            raise BuildError(f"{name}: lists must specify an item type")
        _check_type(args[0], name, seen, allow_none=False)
    else:
        if len(args) != 2 or args[0] is not str:
            raise BuildError(f"{name}: maps require string keys and an explicit value type")
        _check_type(args[1], name, seen, allow_none=False)


def _check_type(
    annotation: object,
    name: str,
    seen: set[type[BaseModel]],
    *,
    allow_none: bool = True,
) -> None:
    """Reject annotations kdantic would coerce to string or silently discard."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        _check_type(args[0], name, seen, allow_none=allow_none)
    elif origin in (Union, types.UnionType):
        members = [arg for arg in args if arg is not type(None)]
        if len(members) != 1:
            raise BuildError(f"{name}: unsupported union {annotation}")
        if not allow_none and len(members) != len(args):
            raise BuildError(f"{name}: nullable collection members are unsupported")
        _check_type(members[0], name, seen, allow_none=allow_none)
    elif annotation is type(None) and not allow_none:
        raise BuildError(f"{name}: nullable collection members are unsupported")
    elif origin is Literal:
        if not args or not all(isinstance(arg, str) for arg in args):
            raise BuildError(f"{name}: only string Literal values are supported")
    elif origin in (list, dict, Mapping):
        _check_container(origin, args, name, seen)
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        _check_schema(annotation, seen)
    elif isinstance(annotation, type) and issubclass(annotation, Enum):
        values = [item.value for item in annotation]
        if (
            not values
            or type(values[0]) not in (str, int, float, bool)
            or any(type(value) is not type(values[0]) for value in values)
        ):
            raise BuildError(f"{name}: enum values must have one supported primitive type")
    elif annotation not in (str, int, float, bool):
        raise BuildError(f"{name}: unsupported schema type {annotation}")


def _check_schema(model: type[BaseModel], seen: set[type[BaseModel]]) -> None:
    """Check every nested field and explicitly serialized default before generation."""
    if model in seen:
        raise BuildError(f"{model.__name__}: recursive schemas are unsupported")
    seen.add(model)
    try:
        for name, field in model.model_fields.items():
            full_name = f"{model.__name__}.{name}"
            serialized_name = field.serialization_alias or field.alias or name
            input_alias = field.validation_alias or field.alias
            accepts_serialized_name = (
                (serialized_name == name and input_alias is None)
                or (
                    model.model_config.get("validate_by_alias", True)
                    and (
                        input_alias == serialized_name
                        or serialized_name in getattr(input_alias, "choices", ())
                    )
                )
                or (
                    serialized_name == name
                    and model.model_config.get(
                        "validate_by_name", model.model_config.get("populate_by_name", False)
                    )
                )
            )
            if not accepts_serialized_name:
                raise BuildError(
                    f"{full_name}: incompatible input/output aliases for {serialized_name!r}"
                )
            _check_type(field.annotation, full_name, seen)
            default = field.get_default()
            if not field.is_required() and default is not None:
                try:
                    json.dumps(default, allow_nan=False)
                except (TypeError, ValueError) as exc:
                    raise BuildError(f"{full_name}: default is not JSON serializable") from exc
    finally:
        seen.remove(model)


def _normalize_nested_required(annotation: object, schema: dict) -> None:
    """Normalize required fields in nested model schemas, including container items."""
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Annotated:
        _normalize_nested_required(args[0], schema)
    elif origin in (Union, types.UnionType):
        members = [arg for arg in args if arg is not type(None)]
        if len(members) == 1:
            _normalize_nested_required(members[0], schema)
    elif origin is list and args:
        items = schema.get("items")
        if isinstance(items, dict):
            _normalize_nested_required(args[0], items)
    elif origin in (dict, Mapping) and len(args) == 2:
        values = schema.get("additionalProperties")
        if isinstance(values, dict):
            _normalize_nested_required(args[1], values)
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        _normalize_model_required(annotation, schema)


def _normalize_model_required(model: type[BaseModel], schema: dict) -> None:
    """Set required property names from Pydantic fields throughout a payload schema."""
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return
    required = []
    for name, field in model.model_fields.items():
        serialized_name = (
            field.serialization_alias
            if isinstance(field.serialization_alias, str)
            else field.alias if isinstance(field.alias, str) else name
        )
        field_schema = properties.get(serialized_name)
        if field_schema is not None:
            if field.is_required():
                required.append(serialized_name)
            if isinstance(field_schema, dict):
                _normalize_nested_required(field.annotation, field_schema)
    if required:
        schema["required"] = required
    else:
        schema.pop("required", None)


def _load_model(reference: str, root: Path) -> type[BaseModel]:
    """Import exactly one model class rather than scanning a module."""
    module_name, sep, class_name = reference.partition(":")
    if (
        not sep
        or not all(part.isidentifier() for part in module_name.split("."))
        or not class_name.isidentifier()
    ):
        raise BuildError(f"Invalid model reference {reference!r}; expected module:Class")
    with _MODEL_IMPORT_LOCK:
        original_sys_path = sys.path.copy()
        sys.path[:0] = [str(root / "src"), str(root)]
        try:
            module = importlib.import_module(module_name)
            model = getattr(module, class_name, None)
        except (ImportError, AttributeError) as exc:
            raise BuildError(f"Cannot import {reference}: {exc}") from exc
        finally:
            sys.path[:] = original_sys_path
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise BuildError(f"{reference}: expected a Pydantic BaseModel subclass")
    location = getattr(module, "__file__", None)
    if location is None or not Path(location).resolve().is_relative_to(root / "src"):
        raise BuildError(f"{reference}: model modules must resolve under the project's src/")
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
    settings = Settings.model_construct(crd_identity_fields=set())
    with patch("kdantic.helpers.settings.load_settings", return_value=settings):
        yield


def _dockerfile(handler: str) -> str:
    """Render an image with an isolated build stage and a non-root runtime."""
    launcher = (
        "import os; os.execvp('kopf', "
        "['kopf', 'run', '--standalone', '--namespace', "
        "os.environ['POD_NAMESPACE'], "
        "'--liveness=http://0.0.0.0:8080/healthz', "
        "'--module', 'based_operators.runtime'])"
    )
    return (
        "FROM python:3.13-slim AS build\n"
        "COPY --from=ghcr.io/astral-sh/uv:0.9.0 /uv /usr/local/bin/uv\n"
        "ARG UV_DYNAMIC_VERSIONING_BYPASS\n"
        "RUN apt-get update && apt-get install -y --no-install-recommends git "
        "&& rm -rf /var/lib/apt/lists/*\n"
        "WORKDIR /operator\n"
        "COPY pyproject.toml uv.lock README.md ./\n"
        "COPY src/ ./src/\n"
        "COPY app/ ./app/\n"
        "RUN uv sync --frozen --no-dev\n"
        "FROM python:3.13-slim\n"
        "WORKDIR /operator\n"
        "COPY --from=build --chown=10001:10001 /operator/.venv/ ./.venv/\n"
        "COPY --from=build --chown=10001:10001 /operator/src/ ./src/\n"
        "COPY --from=build --chown=10001:10001 /operator/app/ ./app/\n"
        'ENV PATH="/operator/.venv/bin:$PATH" PYTHONPATH="/operator/src" '
        f'BASED_OPERATORS_HANDLER="{handler}"\n'
        "EXPOSE 8080\n"
        "USER 10001:10001\n"
        f"CMD {json.dumps(['python', '-c', launcher])}\n"
    )


def _admission_templates(resources: list[tuple[str, str, str]]) -> str:
    """Render explicit Kopf handler-ID routes; Helm never guesses handler code."""
    rules = "\n".join(
        "          - apiGroups: " + json.dumps([group]) + "\n"
        "            apiVersions: " + json.dumps([version]) + "\n"
        "            resources: " + json.dumps([plural]) + "\n"
        '            operations: ["CREATE", "UPDATE"]\n'
        "            scope: Namespaced"
        for group, version, plural in sorted(resources)
    )
    prefix = """{{- if .Values.webhooks.enabled }}
{{- $w := .Values.webhooks }}
{{- if not $w.caBundle }}{{ fail "webhooks.caBundle (base64 PEM) is required" }}{{ end }}
{{- if not (or $w.validating $w.mutating) }}
{{- fail "Configure at least one webhook handler ID" }}
{{- end }}
{{- if and $w.existingSecret (or $w.tlsCrt $w.tlsKey) }}
{{- fail "Use an existing TLS Secret or supplied certificate/key, not both" }}
{{- end }}
{{- if and (not $w.existingSecret) (or (not $w.tlsCrt) (not $w.tlsKey)) }}
{{- fail "Supply a TLS Secret or tlsCrt and tlsKey" }}
{{- end }}
{{- if not $w.existingSecret }}
apiVersion: v1
kind: Secret
metadata:
  name: {{ .Release.Name }}-webhook
type: kubernetes.io/tls
data:
  tls.crt: {{ $w.tlsCrt | b64enc | quote }}
  tls.key: {{ $w.tlsKey | b64enc | quote }}
  ca.crt: {{ $w.caBundle | quote }}
---
{{- end }}
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-webhook
spec:
  selector:
    app: {{ .Release.Name }}
  ports:
    - name: webhook
      port: 443
      targetPort: webhook
"""
    config = """
{{- if $w.KIND }}
---
apiVersion: admissionregistration.k8s.io/v1
kind: CONFIGKIND
metadata:
  name: {{ $.Release.Name }}-{{ $.Release.Namespace }}-TYPE
webhooks:
  {{- range $i, $id := $w.KIND }}
  - name: {{ printf "%d.%s.%s.svc" $i $.Release.Name $.Release.Namespace | quote }}
    admissionReviewVersions: ["v1"]
    sideEffects: None
    failurePolicy: Fail
    matchPolicy: Equivalent
    timeoutSeconds: 10
    clientConfig:
      service:
        name: {{ $.Release.Name }}-webhook
        namespace: {{ $.Release.Namespace }}
        path: {{ printf "/%s" $id | quote }}
        port: 443
      caBundle: {{ $w.caBundle | quote }}
    namespaceSelector:
      matchLabels:
        kubernetes.io/metadata.name: {{ $.Release.Namespace | quote }}
    rules:
RULES
  {{- end }}
{{- end }}
"""
    for kind, configkind in (
        ("validating", "ValidatingWebhookConfiguration"),
        ("mutating", "MutatingWebhookConfiguration"),
    ):
        prefix += config.replace("$w.KIND", f"$w.{kind}").replace(
            "CONFIGKIND", configkind
        ).replace("TYPE", kind).replace("RULES", rules)
    return prefix + "{{- end }}\n"


def _chart(
    image: str, resources: list[tuple[str, str, str]],
) -> dict[str, str]:
    """Render namespace-scoped RBAC and a deliberately single-replica chart."""
    names = sorted({plural for _, _, plural in resources})
    groups = sorted({group for group, _, _ in resources})
    resource_list = json.dumps(names)
    group_list = json.dumps(groups)
    release = "{{ .Release.Name }}"
    admission = _admission_templates(resources)
    return {
        "Chart.yaml": "apiVersion: v2\nname: based-operator\nversion: 0.1.0\ntype: application\n",
        "values.yaml": (
            f"image: {json.dumps(image)}\n"
            "webhooks:\n  enabled: false\n  existingSecret: \"\"\n"
            "  caBundle: \"\"\n  tlsCrt: \"\"\n  tlsKey: \"\"\n"
            "  validating: []\n  mutating: []\n"
        ),
        "values.schema.json": json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "additionalProperties": False,
                "required": ["image"],
                "properties": {
                    "image": {"type": "string", "minLength": 1},
                    "webhooks": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "enabled": {"type": "boolean"},
                            "existingSecret": {"type": "string"},
                            "caBundle": {
                                "type": "string",
                                "pattern": (
                                    "^([A-Za-z0-9+/]{4})*"
                                    "([A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
                                ),
                            },
                            "tlsCrt": {"type": "string"},
                            "tlsKey": {"type": "string"},
                            "validating": {
                                "type": "array",
                                "items": {"type": "string", "pattern": "^[a-zA-Z0-9_.-]+$"},
                            },
                            "mutating": {
                                "type": "array",
                                "items": {"type": "string", "pattern": "^[a-zA-Z0-9_.-]+$"},
                            },
                        },
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "README.md": (
            "# Standalone operator chart\n\n"
            "Install with `helm install RELEASE ./helm --namespace NAMESPACE` after "
            "building and pushing the image in `values.yaml`. Set `image` with "
            "`--set image=REGISTRY/NAME:TAG` if needed. One replica watches only "
            "the release namespace; it does not provide HA. CRDs in `crds/` "
            "are installed on first install but Helm does not upgrade or remove them.\n"
        ),
        "templates/webhooks.yaml": admission,
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
          ports:
            - name: health
              containerPort: 8080
            {{{{- if .Values.webhooks.enabled }}}}
            - name: webhook
              containerPort: 9443
            {{{{- end }}}}
          livenessProbe:
            httpGet:
              path: /healthz
              port: health
            initialDelaySeconds: 20
            periodSeconds: 10
          env:
            - name: POD_NAMESPACE
              valueFrom:
                fieldRef:
                  fieldPath: metadata.namespace
            {{{{- if .Values.webhooks.enabled }}}}
            - name: BASED_OPERATORS_WEBHOOKS
              value: "true"
            {{{{- end }}}}
          {{{{- if .Values.webhooks.enabled }}}}
          volumeMounts:
            - name: webhook-tls
              mountPath: /etc/operator-webhook
              readOnly: true
          {{{{- end }}}}
      {{{{- if .Values.webhooks.enabled }}}}
      volumes:
        - name: webhook-tls
          secret:
            secretName: {{{{ default
              (printf "%s-webhook" .Release.Name)
              .Values.webhooks.existingSecret }}}}
            items:
              - key: tls.crt
                path: tls.crt
              - key: tls.key
                path: tls.key
              - key: ca.crt
                path: ca.crt
      {{{{- end }}}}
""",
    }


def _generate_crds(
    root: Path, references: list[str]
) -> tuple[dict[str, dict], list[tuple[str, str, str]]]:
    """Validate each model and build its manifest before writing anything."""
    crds: dict[str, dict] = {}
    resources: list[tuple[str, str, str]] = []
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
            _check_schema(spec_type, seen)
            if status_type:
                _check_schema(status_type, seen)
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
            version_block = _make_version_block(meta, spec_type, status_type, {})
            schema = version_block["schema"]["openAPIV3Schema"]
            for name, field in (("spec", spec), ("status", status)):
                if field is None:
                    continue
                annotation = field.annotation
                while get_origin(annotation) is Annotated:
                    annotation = get_args(annotation)[0]
                if type(None) in get_args(annotation):
                    schema["properties"][name] = {
                        **schema["properties"][name], "nullable": True,
                    }
            _normalize_model_required(spec_type, schema["properties"]["spec"])
            if status_type:
                _normalize_model_required(status_type, schema["properties"]["status"])
            if spec.is_required():
                schema["required"] = ["spec"]
            crds[key] = build_crd_object(meta, version_block)
            resources.append((meta.group, meta.version, meta.names.plural))
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
        for name, text in _chart(image, resources).items():
            path = stage / "helm" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        checksums = {
            path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(stage.rglob("*"))
            if path.is_file()
        }
        (stage / "manifest.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "image": image,
                    "handler": handler,
                    "models": references,
                    "sha256": checksums,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        if output.exists():
            raise BuildError(f"Output already exists: {output}")
        os.rename(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return output
