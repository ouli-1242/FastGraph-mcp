"""Project configuration and defaults."""

from __future__ import annotations

import fnmatch
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


# Project-local ignore file, read from the indexed root (gitignore-style).
IGNORE_FILENAME = ".fastgraphignore"


def parse_ignore(text: str) -> list[str]:
    """One pattern per line; '#' comments. A pattern matches a directory/file
    name at any depth, or a relative path, via fnmatch globbing (`towxml`,
    `miniprogram/vendor/*`, `*.min.js`)."""
    out: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def matches_ignore(patterns: list[str], rel: str, name: str) -> bool:
    """True when an entry (project-relative path + bare name) is ignored."""
    for pat in patterns:
        p = pat.rstrip("/")
        if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(rel, p):
            return True
        # pattern matches one of the entry's parent directories
        if "/" not in p and any(
            seg == p or fnmatch.fnmatch(seg, p) for seg in rel.split("/")[:-1]
        ):
            return True
    return False