"""Shared tree-sitter helpers used across adapters."""

from __future__ import annotations

import re
from typing import Optional

try:
    from tree_sitter import Language, Parser
except ImportError:  # pragma: no cover
    Language = Parser = None  # type: ignore


class TSParser:
    """Cached per-species tree-sitter parser."""

    _parsers: dict[str, Parser] = {}

    @classmethod
    def for_lang(cls, lang) -> Parser:
        key = lang.name
        p = cls._parsers.get(key)
        if p is None:
            p = Parser(lang)
            cls._parsers[key] = p
        return p


def node_text(node, source: bytes, limit: int = 300) -> str:
    if node is None:
        return ""
    try:
        return source[node.start_byte : node.end_byte].decode("utf-8", "replace")[:limit]
    except Exception:
        return ""


def kind_for(node, is_method: bool) -> str:
    if is_method:
        return "method"
    t = node.type
    if "class" in t or t == "struct_specifier" or t == "struct_item":
        return "class"
    return "function"


def walk(node, fn, depth: int = 0) -> None:
    if depth > 100:
        return
    fn(node, depth)
    for child in node.named_children:
        walk(child, fn, depth + 1)


def split_identifier(text: str) -> list[str]:
    """Split identifiers on dots/colons/slashes for resolution."""
    parts = re.split(r"[.:/]", text)
    return [p for p in parts if p]


def display_line(line: int) -> str:
    return str(line + 1) if line >= 0 else "?"