"""CLI entrypoint: fastgraph [--root <path>] (stdio MCP server).

Without --root, the project root is auto-detected: walk up from the current
directory to the nearest git root (repo boundary); non-git locations fall
back to the current directory. MCP clients that launch the server with their
working directory set to the opened folder therefore index that folder -
open any project, no config per project.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def detect_root(start: Path) -> Path:
    """Nearest git root above ``start``, or ``start`` itself if none found.

    ``.git`` may be a directory (normal repo) or a file (gitfile in
    submodules / worktrees), so ``.exists()`` covers both.
    """
    cur = start
    while True:
        if (cur / ".git").exists():
            return cur
        parent = cur.parent
        if parent == cur:  # filesystem root: nothing found above
            return start
        cur = parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fastgraph",
        description="FastGraph-MCP stdio server (default root: auto-detected from cwd)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="project root to index (default: nearest git root above cwd, or cwd)",
    )
    args = parser.parse_args(argv)

    root = detect_root(args.root or Path.cwd()).resolve()
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