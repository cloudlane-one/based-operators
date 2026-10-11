"""Shared fixtures for the tests that drive the tool against real tooling."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from based_operators.scaffold import Scaffold, scaffold_operator

REPO_ROOT = Path(__file__).resolve().parents[1]
"""The repository root; the build context of the example operator's image."""

EXAMPLE_OPERATOR = REPO_ROOT / "app" / "operator_example.py"
"""The complete operator entry point the guides hand to the scaffolder."""

BUILD_DIR = REPO_ROOT / "build"
"""Scratch directory for scaffolds built against the repository (git-ignored)."""

_scaffolds: list[Path] = []


def _scaffold_example(**options: Any) -> Scaffold:
    """Scaffold the repository's example operator into a fresh build directory.

    Args:
        **options: Options passed to
            :func:`~based_operators.scaffold.scaffold_operator` instead of their
            defaults.

    Returns:
        The written scaffold.
    """
    output = BUILD_DIR / f"test-{uuid.uuid4().hex[:10]}"
    _scaffolds.append(output)
    return scaffold_operator(operator=EXAMPLE_OPERATOR, output=output, context=REPO_ROOT, **options)


@pytest.fixture()
def scaffold_example() -> Callable[..., Scaffold]:
    """Return a factory that scaffolds the example operator on demand.

    Returns:
        A callable that scaffolds the example operator and returns the result.
    """
    return _scaffold_example


@pytest.fixture(scope="session", autouse=True)
def _remove_scaffolds() -> Iterator[None]:
    """Remove every scaffold this session wrote, so ``build/`` is left clean."""
    yield
    for path in _scaffolds:
        shutil.rmtree(path, ignore_errors=True)
