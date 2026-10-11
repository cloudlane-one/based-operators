"""Jinja rendering of the files a scaffold generates.

Every artifact that is generated as *text* — the Dockerfile and the scaffold
README — is a Jinja template under :data:`ASSETS_DIR` rather than a string built
in Python. The generated files are what people read, review and edit, so they live
as files that can be read, reviewed and edited too, and the code only supplies the
values the templates interpolate.

The Helm chart is *not* rendered: it is a static chart that is copied verbatim,
because Helm already brings its own templating. See
:mod:`based_operators.scaffold.project`.

Rendering needs Jinja2, which is part of the ``scaffold`` extra of the
distribution.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

__all__ = [
    "ASSETS_DIR",
    "HELM_DIR",
    "render",
]

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
"""The directory holding the templates and the static Helm chart."""

HELM_DIR = ASSETS_DIR / "helm"
"""The directory holding the static Helm chart."""

_ENVIRONMENT = Environment(
    loader=FileSystemLoader(ASSETS_DIR),
    undefined=StrictUndefined,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
    autoescape=False,
)
"""The environment shared by every template.

``StrictUndefined`` makes a template that references an unknown variable fail
loudly instead of rendering an empty string into a generated file, and the block
trimming keeps the rendered output free of the blank lines a template's control
tags would otherwise leave behind.
"""


def render(template: str, **context: Any) -> str:
    """Render one shipped template.

    Args:
        template: The template path relative to :data:`ASSETS_DIR`.
        **context: The values the template interpolates.

    Returns:
        The rendered text.

    Raises:
        jinja2.TemplateNotFound: If the template does not exist.
        jinja2.UndefinedError: If the template references an unknown value.
    """
    return _ENVIRONMENT.get_template(template).render(**context)
