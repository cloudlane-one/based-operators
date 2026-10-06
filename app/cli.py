"""Command-line entry point for generating downstream operator artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from based_operators.build import BuildError, build


def main(argv: list[str] | None = None) -> int:
    """Parse build arguments and return an exit status."""
    parser = argparse.ArgumentParser(prog="based-operators")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="Generate CRDs, Dockerfile, and Helm chart")
    command.add_argument("--project-root", type=Path, default=Path("."))
    command.add_argument("--output", type=Path, default=Path("dist/operator"))
    command.add_argument("--image", required=True, help="Container image reference for Helm values")
    command.add_argument("--mode", default="standalone", choices=["standalone", "ha"])
    args = parser.parse_args(argv)
    try:
        output = build(args.project_root, args.output, image=args.image, mode=args.mode)
    except (BuildError, OSError, ValueError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
