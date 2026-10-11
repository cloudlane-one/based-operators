"""The ``based-operators`` command-line interface.

This module holds nothing but the command and its options: the work itself is a
set of plain functions in :mod:`based_operators.scaffold`, which the command
simply calls. The package ships as the ``based-operators`` console script of the
distribution, so it runs wherever the package is installed::

    uvx based-operators scaffold operator.py -o deploy

Inside a checkout, the same command runs as a module or a script through the
project's own environment::

    uv run python -m based_ops_cli scaffold operator.py -o deploy
    uv run app/based_ops_cli/__main__.py scaffold operator.py -o deploy
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

import typer

from based_operators.scaffold import ScaffoldError, describe_resource, scaffold_operator

app = typer.Typer(
    name="based-operators",
    help="Scaffold deployable kopf operators from typed Pydantic custom resources.",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
"""The Typer application behind the ``based-operators`` command."""


def _show_version(requested: bool) -> None:
    """Print the version and stop, when ``--version`` was given.

    Args:
        requested: Whether the flag was passed.

    Raises:
        typer.Exit: After printing the version.
    """
    if not requested:
        return
    try:
        installed = version("based-operators")
    except PackageNotFoundError:  # pragma: no cover - only outside an install
        installed = "unknown"
    typer.echo(f"based-operators {installed}")
    raise typer.Exit


@app.callback()
def root(
    version_requested: Annotated[
        bool,
        typer.Option(
            "--version",
            "-V",
            is_eager=True,
            callback=_show_version,
            help="Show the version and exit.",
        ),
    ] = False,
) -> None:
    """Scaffold the image and the chart that deploy an operator."""


@app.command()
def scaffold(
    operator: Annotated[
        Path,
        typer.Argument(
            help=(
                "The operator entry point: a Python file that defines or imports the "
                "handlers. It is loaded here exactly as 'kopf run' loads it in the image."
            ),
            file_okay=True,
            dir_okay=False,
            readable=True,
            resolve_path=True,
        ),
    ],
    output: Annotated[
        Path,
        typer.Option(
            "-o",
            "--output",
            help="The directory to write the scaffold into, inside the build context.",
            resolve_path=True,
        ),
    ] = Path("deploy"),
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="The chart and release name; defaults to the entry point's file name.",
        ),
    ] = None,
    namespace: Annotated[
        str,
        typer.Option("--namespace", help="The namespace the operator is deployed into."),
    ] = "default",
    image: Annotated[
        str | None,
        typer.Option(
            "--image",
            help="The image the chart deploys, e.g. 'ghcr.io/acme/operator:v1'.",
        ),
    ] = None,
    python_version: Annotated[
        str,
        typer.Option("--python-version", help="The Python version of the base image."),
    ] = "3.13",
    webhook: Annotated[
        bool,
        typer.Option(
            "--webhook/--no-webhook",
            help="Also serve admission webhooks, for resources with admission handlers.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Overwrite an output directory that is not empty."),
    ] = False,
) -> None:
    """Scaffold a Dockerfile, a CRD-complete Helm chart and an operator entrypoint.

    Raises:
        typer.Exit: With a non-zero code if the entry point cannot be loaded, or
            if the scaffold cannot be written.
    """
    try:
        scaffold = scaffold_operator(
            operator=operator,
            output=output,
            context=Path.cwd(),
            name=name,
            namespace=namespace,
            image=image,
            python_version=python_version,
            webhook=webhook,
            force=force,
        )
    except ScaffoldError as error:
        typer.echo(f"error: {error}", err=True)
        raise typer.Exit(code=1) from error

    spec = scaffold.spec
    kinds = ", ".join(describe_resource(resource)["kind"] for resource in scaffold.resources)
    typer.echo(f"Scaffolded the {kinds} operator into {spec.output_ref}/:")
    for path in scaffold.written:
        typer.echo(f"  {path}")
    typer.echo("")
    typer.echo(f"  next: docker build -f {spec.dockerfile_ref} -t {spec.image} .")
    typer.echo(
        f"  then: helm install {spec.name} ./{spec.chart_ref} --namespace {spec.namespace} "
        "--create-namespace"
    )


def main() -> None:
    """Run the command, as the ``based-operators`` console script does."""
    app()


if __name__ == "__main__":
    main()
