"""CLI entrypoint: fastgraph --root <path> (stdio MCP server)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fastgraph", description="FastGraph-MCP stdio server")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="project root to index")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    try:
        from fastgraph.server import run_stdio

        run_stdio(root)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())