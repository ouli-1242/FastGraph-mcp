"""Project configuration and defaults."""

from __future__ import annotations

from os import getenv
from pathlib import Path

DEFAULT_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    "vendor",
    "third_party",
    ".fastgraph",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "Pods",
    ".gradle",
    ".cargo",
    ".tox",
}

MAX_FILE_SIZE = 2 * 1024 * 1024  # skip files larger than 2MB

RESERVED_ENTRIES = {
    ".fastgraph",
}


def user_home() -> Path:
    """Resolve the user's home dir (Windows-safe USERPROFILE check)."""
    return Path(getenv("USERPROFILE") or Path.home()).resolve()