"""Derive and write the artifacts that deploy an operator.

The scaffold is assembled from three sources. The resource classes provide the CRD
documents, which are installed with the chart. The Jinja templates under
:mod:`based_operators.scaffold.assets` provide the generated text files. And the
static Helm chart under ``assets/helm`` is copied verbatim, apart from the Unix
line endings its text files are written with — Helm already brings its own
templating, so the chart is not rendered here; the scaffold only fills in the
image it deploys, which is the one thing the chart cannot know.

The layout written into ``<output>`` is::

    Dockerfile                    the operator image
    README.md                     build, push and install instructions
    helm/<chart>/                 the static chart, copied with values applied
    helm/<chart>/crds/*.yaml      the CRDs generated from the resources
    helm/<chart>/values.yaml      the chart defaults with the image filled in

The build context of the Dockerfile is the directory the caller runs in, so the
entry point and the output directory both have to live inside it.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from based_operators.crd import CustomResource, crd_yaml
from based_operators.crd.errors import CrdError
from based_operators.scaffold.errors import ScaffoldError
from based_operators.scaffold.resources import discover_resources, load_operator
from based_operators.scaffold.templating import HELM_DIR, render

__all__ = [
    "CHART_NAME",
    "CRDS_DIRNAME",
    "HEALTH_PATH",
    "HEALTH_PORT",
    "WEBHOOK_MODULE",
    "Scaffold",
    "ScaffoldSpec",
    "build_spec",
    "describe_resource",
    "parse_image_ref",
    "scaffold_operator",
    "write_scaffold",
]

CHART_NAME = "based-operator"
"""The name of the static Helm chart shipped in the assets."""

CRDS_DIRNAME = "crds"
"""The chart directory Helm installs CustomResourceDefinitions from."""

HEALTH_PORT = 8080
"""The port kopf serves its liveness endpoint on.

The value is baked into the image's command *and* hard-coded in the chart's
liveness probe, so the two can never drift apart.
"""

HEALTH_PATH = "/healthz"
"""The path of kopf's liveness endpoint."""

WEBHOOK_MODULE = "based_operators.webhook"
"""The module ``kopf run -m`` imports to serve admission webhooks.

The module is part of this distribution rather than generated, so the image only
has to name it.
"""

_NAME_PATTERN = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
_PYTHON_VERSION_PATTERN = re.compile(r"^\d+\.\d+$")


@dataclass(frozen=True)
class ScaffoldSpec:
    """The resolved inputs of one scaffold.

    Attributes:
        name: The chart, release and object name.
        namespace: The namespace the operator is deployed into.
        output_dir: The absolute directory the scaffold is written into.
        context_dir: The absolute build context, i.e. the working directory.
        operator_ref: The entry point, relative to the build context.
        image_repository: The image repository, without the tag.
        image_tag: The image tag.
        python_version: The ``X.Y`` version of the base image.
        webhook: Whether the deployment serves admission webhooks.
        force: Whether existing files may be overwritten.
    """

    name: str
    namespace: str
    output_dir: Path
    context_dir: Path
    operator_ref: str
    image_repository: str
    image_tag: str
    python_version: str
    webhook: bool
    force: bool

    @property
    def image(self) -> str:
        """The full image reference the chart is asked to deploy."""
        return f"{self.image_repository}:{self.image_tag}"

    @property
    def chart_dir(self) -> Path:
        """The absolute directory the static chart is copied into."""
        return self.output_dir / "helm" / CHART_NAME

    @property
    def operator_path(self) -> Path:
        """The absolute path of the operator entry point."""
        return self.context_dir / self.operator_ref

    @property
    def output_ref(self) -> str:
        """The output directory, relative to the build context, POSIX-style."""
        return _posix_ref(self.output_dir, self.context_dir)

    @property
    def chart_ref(self) -> str:
        """The copied chart, relative to the build context, POSIX-style."""
        return _posix_ref(self.chart_dir, self.context_dir)

    @property
    def dockerfile_ref(self) -> str:
        """The generated Dockerfile, relative to the build context."""
        return f"{self.output_ref}/Dockerfile"

    @property
    def kopf_command(self) -> list[str]:
        """The container command, with kopf's health checks enabled."""
        command = ["kopf", "run", self.operator_ref]
        if self.webhook:
            command += ["-m", WEBHOOK_MODULE]
        return [*command, f"--liveness=http://0.0.0.0:{HEALTH_PORT}{HEALTH_PATH}"]

    @property
    def liveness_url(self) -> str:
        """The liveness endpoint as the container itself reaches it."""
        return f"http://127.0.0.1:{HEALTH_PORT}{HEALTH_PATH}"


@dataclass(frozen=True)
class Scaffold:
    """A written scaffold.

    Attributes:
        spec: The resolved inputs it was derived from.
        resources: The resource classes its CRDs were generated from.
        written: The files it wrote, relative to the output directory and sorted.
    """

    spec: ScaffoldSpec
    resources: tuple[type[CustomResource[Any, Any]], ...]
    written: tuple[str, ...]


def scaffold_operator(
    *,
    operator: Path,
    output: Path,
    context: Path,
    name: str | None = None,
    namespace: str = "default",
    image: str | None = None,
    python_version: str = "3.13",
    webhook: bool = False,
    force: bool = False,
) -> Scaffold:
    """Load an entry point, derive the scaffold and write it out.

    Args:
        operator: The operator entry point.
        output: The directory to write the scaffold into.
        context: The build context, i.e. the working directory.
        name: The chart and release name; derived from the entry point if unset.
        namespace: The namespace the operator is deployed into.
        image: The image reference to deploy; derived from the name if unset.
        python_version: The ``X.Y`` version of the base image.
        webhook: Whether the deployment is to serve admission webhooks.
        force: Whether existing files may be overwritten.

    Returns:
        The resolved inputs, the resources and the files that were written.

    Raises:
        ScaffoldError: If any input cannot be turned into a valid scaffold, if the
            entry point declares no usable resource, or if the output directory is
            taken.
    """
    spec = build_spec(
        operator=operator,
        output=output,
        context=context,
        name=name,
        namespace=namespace,
        image=image,
        python_version=python_version,
        webhook=webhook,
        force=force,
    )
    resources = discover_resources(load_operator(spec.operator_path))
    written = write_scaffold(spec, resources)
    return Scaffold(spec=spec, resources=tuple(resources), written=tuple(written))


def build_spec(
    *,
    operator: Path,
    output: Path,
    context: Path,
    name: str | None = None,
    namespace: str = "default",
    image: str | None = None,
    python_version: str = "3.13",
    webhook: bool = False,
    force: bool = False,
) -> ScaffoldSpec:
    """Resolve the inputs of a scaffold into a :class:`ScaffoldSpec`.

    Args:
        operator: The operator entry point.
        output: The directory to write the scaffold into.
        context: The build context, i.e. the working directory.
        name: The chart and release name; derived from the entry point if unset.
        namespace: The namespace the operator is deployed into.
        image: The image reference to deploy; derived from the name if unset.
        python_version: The ``X.Y`` version of the base image.
        webhook: Whether the deployment is to serve admission webhooks.
        force: Whether existing files may be overwritten.

    Returns:
        The resolved specification.

    Raises:
        ScaffoldError: If any input cannot be turned into a valid scaffold.
    """
    context_dir = context.resolve()
    operator_path = operator.resolve()
    output_dir = output.resolve()

    if not operator_path.is_file():
        raise ScaffoldError(f"{operator} is not a file")

    # The image installs the build context, so both the entry point and the
    # output directory have to live inside it for the generated commands to hold.
    operator_ref = _contained_ref(operator_path, context_dir, "the entry point")
    _contained_ref(output_dir, context_dir, "--output")

    app_name = _resolve_name(name, operator_path, operator_ref)
    repository, tag = parse_image_ref(image or f"{app_name}:latest")
    if not python_version:
        raise ScaffoldError("--python-version must not be empty")
    if not _PYTHON_VERSION_PATTERN.match(python_version):
        raise ScaffoldError(
            f"--python-version {python_version!r} must be a major and minor version, such as 3.13"
        )
    if not namespace:
        raise ScaffoldError("--namespace must not be empty")

    return ScaffoldSpec(
        name=app_name,
        namespace=namespace,
        output_dir=output_dir,
        context_dir=context_dir,
        operator_ref=operator_ref,
        image_repository=repository,
        image_tag=tag,
        python_version=python_version,
        webhook=webhook,
        force=force,
    )


def parse_image_ref(reference: str) -> tuple[str, str]:
    """Split an image reference into its repository and its tag.

    The tag is whatever follows the last colon that comes after the last slash, so
    that a registry port is not mistaken for one: ``registry:5000/app`` has no tag,
    while ``registry:5000/app:v1`` is tagged ``v1``. A reference without a tag
    defaults to ``latest``, as ``docker build`` would.

    Args:
        reference: A Docker image reference, e.g. ``ghcr.io/acme/operator:v1``.

    Returns:
        The repository and the tag.

    Raises:
        ScaffoldError: If the reference is empty, or carries a digest rather than
            a tag, which Helm's ``image.tag`` cannot express.
    """
    if not reference:
        raise ScaffoldError("the image reference must not be empty")
    if "@" in reference:
        raise ScaffoldError(
            f"image reference {reference!r} is pinned by digest; "
            "name a repository and a tag instead"
        )
    head, _, tail = reference.rpartition("/")
    if ":" not in tail:
        return reference, "latest"
    repository, _, tag = tail.rpartition(":")
    if not repository or not tag:
        raise ScaffoldError(f"image reference {reference!r} has an empty repository or tag")
    return f"{head}/{repository}" if head else repository, tag


def write_scaffold(
    spec: ScaffoldSpec,
    resources: Sequence[type[CustomResource[Any, Any]]],
) -> list[str]:
    """Write the whole scaffold into the output directory.

    Args:
        spec: The resolved inputs of the scaffold.
        resources: The resource classes to generate CRDs for.

    Returns:
        The paths written, relative to the output directory and sorted.

    Raises:
        ScaffoldError: If the output directory is taken, or a resource cannot be
            turned into a CRD.
    """
    _guard_output(spec)
    _copy_chart(spec)
    crds = _write_crds(spec, resources)
    values = _apply_values(spec)

    context = _template_context(spec, resources)
    generated = {
        "Dockerfile": render("Dockerfile.j2", **context),
        "README.md": render("README.md.j2", **context),
    }
    for name, content in generated.items():
        _write(spec.output_dir / name, content)

    written = [Path(name) for name in generated]
    return sorted(path.as_posix() for path in [*written, *crds, values])


def describe_resource(resource: type[CustomResource[Any, Any]]) -> dict[str, str]:
    """Summarize a resource for the generated documentation and summaries.

    Args:
        resource: The resource class to describe.

    Returns:
        Its Kubernetes identity, including the CRD name it is installed under.
    """
    config = resource._require_resource_config()
    return {
        "kind": config.kind,
        "group": config.group,
        "version": config.version,
        "plural": config.plural,
        "scope": config.scope.value,
        "api_version": config.api_version,
        "crd_name": config.crd_name,
    }


def _guard_output(spec: ScaffoldSpec) -> None:
    """Refuse to write into an output directory that is already taken.

    Args:
        spec: The resolved inputs of the scaffold.

    Raises:
        ScaffoldError: If the output path exists as a file, or exists as a
            non-empty directory without ``--force``.
    """
    if spec.output_dir.is_file():
        raise ScaffoldError(f"{spec.output_dir} exists and is not a directory")
    if not spec.output_dir.is_dir():
        return
    existing = sorted(path.name for path in spec.output_dir.iterdir())
    if existing and not spec.force:
        raise ScaffoldError(
            f"{spec.output_dir} already contains {', '.join(existing)}; "
            "pass --force to overwrite it"
        )


def _copy_chart(spec: ScaffoldSpec) -> None:
    """Copy the static chart, replacing any chart from an earlier scaffold.

    Text assets are copied with Unix line endings; see :func:`_copy_chart_asset`.

    Args:
        spec: The resolved inputs of the scaffold.

    Raises:
        ScaffoldError: If the shipped chart is missing from the installation.
    """
    source = HELM_DIR / CHART_NAME
    if not source.is_dir():
        raise ScaffoldError(f"the shipped Helm chart is missing from {source}")
    if spec.chart_dir.exists():
        shutil.rmtree(spec.chart_dir)
    shutil.copytree(source, spec.chart_dir, copy_function=_copy_chart_asset)


def _copy_chart_asset(source: str, destination: str) -> str:
    """Copy one chart asset, giving a text asset Unix line endings.

    The generated chart is consumed by Helm and by the Linux container the image
    runs in, so a carriage return in it is at best noise and at worst part of a
    command. The shipped assets are committed with Unix line endings, but a
    checkout that materializes them with CRLF — ``core.autocrlf`` on Windows —
    would otherwise put CRLF into every generated chart file, since a verbatim
    copy has nothing to normalize it. Binary assets are copied byte for byte.

    Args:
        source: The path of the asset to copy.
        destination: The path to copy it to.

    Returns:
        The destination path.
    """
    text = _decode_text(Path(source).read_bytes())
    if text is None:
        return shutil.copy2(source, destination)
    Path(destination).write_text(text.replace("\r\n", "\n"), encoding="utf-8", newline="\n")
    shutil.copystat(source, destination)
    return destination


def _decode_text(data: bytes) -> str | None:
    """Decode an asset as UTF-8 text, or report that it is not text.

    Args:
        data: The raw bytes of an asset.

    Returns:
        The decoded text, or ``None`` when the bytes are not UTF-8 text.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def _write_crds(
    spec: ScaffoldSpec,
    resources: Sequence[type[CustomResource[Any, Any]]],
) -> list[Path]:
    """Write one CRD document per resource into the chart.

    Helm installs everything in ``crds/`` before the rest of the chart and leaves
    it untouched on upgrades, which is exactly the behaviour a CRD wants.

    Args:
        spec: The resolved inputs of the scaffold.
        resources: The resource classes to describe.

    Returns:
        The written paths, relative to the output directory.

    Raises:
        ScaffoldError: If a resource cannot be turned into a CRD.
    """
    directory = spec.chart_dir / CRDS_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for resource in resources:
        try:
            document = crd_yaml(resource)
            config = resource._require_resource_config()
        except CrdError as error:
            raise ScaffoldError(f"{resource.__name__} cannot be generated: {error}") from error
        path = directory / f"{config.crd_name}.yaml"
        _write(path, document)
        written.append(path.relative_to(spec.output_dir))
    return written


def _apply_values(spec: ScaffoldSpec) -> Path:
    """Write the deployed image into the copied chart's values.

    The chart is static, so the image is the one value the scaffold fills in; the
    chart's own defaults provide a valid value for everything else.

    Args:
        spec: The resolved inputs of the scaffold.

    Returns:
        The written path, relative to the output directory.

    Raises:
        ScaffoldError: If the copied chart has no readable ``values.yaml``.
    """
    path = spec.chart_dir / "values.yaml"
    try:
        values = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ScaffoldError(f"the shipped chart's values could not be read: {error}") from error
    if not isinstance(values, dict):
        raise ScaffoldError("the shipped chart's values.yaml is not a mapping")

    values["image"] = {**values.get("image", {}), "repository": spec.image_repository}
    values["image"]["tag"] = spec.image_tag
    values["webhook"] = {**values.get("webhook", {}), "enabled": spec.webhook}

    _write(path, yaml.safe_dump(values, sort_keys=False, default_flow_style=False))
    return path.relative_to(spec.output_dir)


def _template_context(
    spec: ScaffoldSpec,
    resources: Sequence[type[CustomResource[Any, Any]]],
) -> dict[str, Any]:
    """Collect every value the generated files interpolate.

    Args:
        spec: The resolved inputs of the scaffold.
        resources: The resource classes the scaffold describes.

    Returns:
        The template context, shared by all generated files.
    """
    return {
        "name": spec.name,
        "namespace": spec.namespace,
        "image": spec.image,
        "image_repository": spec.image_repository,
        "image_tag": spec.image_tag,
        "python_version": spec.python_version,
        "health_port": HEALTH_PORT,
        "health_path": HEALTH_PATH.replace("/", "", 1),
        "liveness_url": spec.liveness_url,
        "kopf_command": spec.kopf_command,
        "operator_ref": spec.operator_ref,
        "chart_ref": spec.chart_ref,
        "chart_name": CHART_NAME,
        "output_ref": spec.output_ref,
        "dockerfile_ref": spec.dockerfile_ref,
        "webhook": spec.webhook,
        "webhook_module": WEBHOOK_MODULE,
        "resources": [describe_resource(resource) for resource in resources],
    }


def _resolve_name(name: str | None, operator_path: Path, operator_ref: str) -> str:
    """Return the chart name, deriving it from the entry point when unset.

    Args:
        name: The requested name, or ``None``.
        operator_path: The absolute path of the entry point.
        operator_ref: The entry point, relative to the build context.

    Returns:
        A lower-case label usable as a Kubernetes object and chart name.

    Raises:
        ScaffoldError: If the name is neither given nor derivable, or is not a
            valid label.
    """
    if name is None:
        derived = re.sub(r"[^a-z0-9]+", "-", operator_path.stem.lower()).strip("-")
        if not _NAME_PATTERN.match(derived):
            raise ScaffoldError(
                f"{operator_ref!r} does not yield a usable name; pass --name explicitly"
            )
        return derived
    if not _NAME_PATTERN.match(name):
        raise ScaffoldError(
            f"--name {name!r} must be a lower-case label "
            "(lower-case letters, digits and '-', starting with a letter)"
        )
    return name


def _contained_ref(path: Path, context_dir: Path, label: str) -> str:
    """Return a path relative to the build context, or refuse it.

    Args:
        path: The absolute path to describe.
        context_dir: The absolute build context.
        label: The name of the input, used in the error message.

    Returns:
        The path relative to the context, POSIX-style.

    Raises:
        ScaffoldError: If the path lies outside the build context.
    """
    if path == context_dir:
        return ""
    try:
        relative = path.relative_to(context_dir)
    except ValueError as error:
        raise ScaffoldError(
            f"{label} {path} lies outside the build context {context_dir}; run the "
            "command from the project root the image is built from"
        ) from error
    return relative.as_posix()


def _posix_ref(path: Path, context_dir: Path) -> str:
    """Return a path relative to the build context, POSIX-style.

    Args:
        path: The absolute path to describe.
        context_dir: The absolute build context.

    Returns:
        The relative path, or ``"."`` for the context itself.
    """
    return path.relative_to(context_dir).as_posix() or "."


def _write(path: Path, content: str) -> None:
    """Write a generated file, creating its directory.

    Generated files always use Unix line endings, whatever platform the caller
    runs on: they are consumed by Docker, Helm and Linux containers, where a
    carriage return is at best noise and at worst part of a command.

    Args:
        path: The absolute path to write.
        content: The text to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
