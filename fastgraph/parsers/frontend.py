"""Vue / Svelte single-file-component adapter.

Extracts every <script ...> block, blanks the rest of the file so line
numbers stay aligned with the original file, then reuses the TypeScript /
JavaScript adapter to parse the extracted code.
"""

from __future__ import annotations

import re

from fastgraph.parsers.base import ParseResult
from fastgraph.parsers.registry import register_adapter
from fastgraph.parsers.typescript import TSAdapter

_SCRIPT_RE = re.compile(r"<script\b([^>]*)>([\s\S]*?)</script\s*>", re.IGNORECASE)
_LANG_ATTR = re.compile(r'lang\s*=\s*["\']?(tsx|ts|js|jsx)["\' ]', re.IGNORECASE)


class SfcAdapter:
    """Shared logic for .vue / .svelte single-file components."""

    def __init__(self, lang: str, exts: tuple[str, ...]):
        self.lang = lang
        self.exts = exts
        self._ts = TSAdapter(lang="typescript")
        self._js = TSAdapter(lang="javascript")

    def parse(self, source: bytes) -> ParseResult:
        text = source.decode("utf-8", "replace")
        blocks: list[tuple[str, int]] = []  # (code, zero-based start line)
        wants_ts = False
        for m in _SCRIPT_RE.finditer(text):
            attrs, code = m.group(1), m.group(2)
            start_line = text.count("\n", 0, m.start())
            lang_match = _LANG_ATTR.search(attrs)
            if lang_match and lang_match.group(1) in ("ts", "tsx"):
                wants_ts = True
            blocks.append((code, start_line))

        if not blocks:
            return ParseResult(language=self.lang, symbols=[], imports=[])

        # rebuild the file with only script lines kept (blank elsewhere),
        # preserving original line numbers for accurate symbol locations
        line_count = text.count("\n") + 1
        lines = [""] * line_count
        for code, start_line in blocks:
            code_lines = code.split("\n")
            for i, cl in enumerate(code_lines):
                idx = start_line + i
                if idx < line_count:
                    lines[idx] = cl
        synthetic = "\n".join(lines).encode("utf-8", "replace")

        adapter = self._ts if wants_ts else self._js
        result = adapter.parse(synthetic)
        result.language = self.lang
        return result


register_adapter(SfcAdapter("vue", (".vue",)))
register_adapter(SfcAdapter("svelte", (".svelte",)))