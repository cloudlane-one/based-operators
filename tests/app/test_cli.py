"""Tests for the artifact-generation CLI."""

from pathlib import Path

import pytest

from app import cli
from based_operators.build import BuildError


def test_main_builds_and_prints_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Forward build options and print the generated artifact path."""
    output = Path("generated/operator")
    calls: list[tuple[Path, Path, str, str]] = []

    def fake_build(
        project_root: Path, output_path: Path, *, image: str, mode: str
    ) -> Path:
        calls.append((project_root, output_path, image, mode))
        return output

    monkeypatch.setattr(cli, "build", fake_build)

    assert cli.main(["build", "--image", "example/operator:1"]) == 0
    assert calls == [(Path("."), Path("dist/operator"), "example/operator:1", "standalone")]
    assert capsys.readouterr().out == f"{output}\n"


@pytest.mark.parametrize(
    "error",
    [BuildError("bad project"), OSError("disk full"), ValueError("bad value")],
)
def test_main_reports_build_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    """Convert expected build exceptions into a diagnostic and failure status."""

    def fail_build(*args: object, **kwargs: object) -> Path:
        raise error

    monkeypatch.setattr(cli, "build", fail_build)

    assert cli.main(["build", "--image", "example/operator:1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"Build failed: {error}\n"


@pytest.mark.parametrize(
    "arguments",
    [
        [],
        ["build"],
        ["build", "--image", "example/operator:1", "--mode", "unsupported"],
    ],
)
def test_main_rejects_invalid_arguments(arguments: list[str]) -> None:
    """Let argparse reject missing commands, required values, and invalid choices."""
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2
