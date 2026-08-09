"""Incremental indexer: walk -> mtime/size diff -> re-parse changed only."""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from fastgraph.config import DEFAULT_EXCLUDES, MAX_FILE_SIZE
from fastgraph.db import DB
from fastgraph.parsers.base import SymbolInfo
from fastgraph.parsers.registry import get_adapter

_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "tsx", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".vue": "vue", ".svelte": "svelte",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "cpp", ".h": "cpp", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
}

MAX_PARSE_WORKERS = min(8, (os.cpu_count() or 2))

# Guard against accidentally walking a huge, unindexed directory (e.g. an
# unactivated default root like a user's home folder): stop once this many
# entries were checked. refresh() reports `skipped` so tools can hint at
# activate_project().
MAX_SCAN_ENTRIES = 200_000


@dataclass
class IndexStats:
    scanned: int = 0
    parsed: int = 0
    deleted: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    total_files: int = 0
    total_symbols: int = 0
    skipped: bool = False


class Indexer:
    def __init__(self, root: Path, db: DB, excludes: set[str] | None = None):
        self.root = root.resolve()
        self.db = db
        self.excludes = excludes or DEFAULT_EXCLUDES
        self._walk_skipped = False
        # Serialize refresh() across threads: every tool call runs _ensure_fresh,
        # and concurrent writes to the same sqlite connection crash with
        # InterfaceError / UNIQUE constraint races (see STRESS_TEST_REPORT P1-1).
        self._refresh_lock = threading.RLock()

    def refresh(self) -> IndexStats:
        with self._refresh_lock:
            return self._refresh()

    def _refresh(self) -> IndexStats:
        start = time.perf_counter()
        stats = IndexStats()
        cached = self.db.file_map()

        to_parse: list[tuple[str, Path]] = []
        seen: set[str] = set()

        for p in self._walk():
            rel = p.relative_to(self.root).as_posix()
            seen.add(rel)
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > MAX_FILE_SIZE:
                continue
            stats.scanned += 1
            if cached.get(rel) == st.st_mtime:
                continue  # untouched: zero IO
            to_parse.append((rel, p))

        stats.skipped = getattr(self, "_walk_skipped", False)
        if not stats.skipped:
            for rel in cached:
                if rel not in seen:
                    self.db.delete_file(rel)
                    stats.deleted += 1

        if to_parse:
            with ThreadPoolExecutor(max_workers=MAX_PARSE_WORKERS) as ex:
                parsed = ex.map(self._parse_only, to_parse)
            for rel, st, hash_, result, source in parsed:
                if result is None:
                    stats.errors += 1
                    self.db.set_parse_error(rel, "parse failed")
                    continue
                self.db.clear_parse_error(rel)
                self._store_file(rel, st, hash_, result)
                stats.parsed += 1

        if stats.parsed or stats.deleted:
            self.db.commit()

        # Re-run resolution on leftover unresolved relations so improved
        # resolver logic (e.g. constructor disambiguation) heals existing
        # indexes without a full rebuild. Cheap when nothing is unresolved.
        if self.db.conn.execute("SELECT 1 FROM relations WHERE target_id IS NULL AND rtype IN ('calls','inherits') LIMIT 1").fetchone():
            _resolve_all(self.db)
        self.db.commit()

        stats.duration_ms = (time.perf_counter() - start) * 1000
        stats.total_files = len(seen)
        stats.total_symbols = self.db.count_symbols()
        stats.skipped = getattr(self, "_walk_skipped", False)
        return stats

    def force_index(self) -> IndexStats:
        """Rebuild from scratch (used once on first run)."""
        for row in self.db.conn.execute("SELECT path FROM files"):
            self.db.delete_file(row[0])
        self.db.commit()
        return self.refresh()

    # ---------------- internals ----------------

    def _walk(self) -> list[Path]:
        out: list[Path] = []
        stack = [self.root]
        checked = 0
        while stack:
            d = stack.pop()
            try:
                entries = os.scandir(d)
            except OSError:
                continue
            with entries as it:
                for e in it:
                    checked += 1
                    if checked > MAX_SCAN_ENTRIES:
                        self._walk_skipped = True
                        return out
                    name = e.name
                    if name in self.excludes or name.startswith("."):
                        continue
                    try:
                        is_dir = e.is_dir(follow_symlinks=False)
                    except OSError:
                        continue
                    if is_dir:
                        if name != ".git":
                            stack.append(Path(e.path))
                    elif e.is_file(follow_symlinks=False):
                        ext = Path(name).suffix.lower()
                        if ext in _EXT_LANG:
                            out.append(Path(e.path))
        return out

    def _parse_only(self, item: tuple) -> tuple:
        """Thread-safe: read + parse, no DB access. Returns (rel, stat, hash, ParseResult|None)."""
        rel, path = item
        lang = _EXT_LANG.get(Path(rel).suffix.lower())
        adapter = get_adapter(lang)
        if adapter is None:
            return rel, None, "", None, ""
        try:
            st = path.stat()
            source = path.read_bytes()
        except OSError:
            return rel, None, "", None, ""
        hash_ = hashlib.sha256(source).hexdigest()[:24]
        try:
            result = adapter.parse(source)
        except Exception:
            result = None
        return rel, st, hash_, result, source

    def _store_file(self, rel: str, st, hash_: str, result) -> None:
        """Single-threaded DB write for one parsed file."""
        lang = result.language
        module_doc = getattr(result, "module_doc", "") or ""
        if module_doc:
            # Adapters return a module-level docstring separately; surface it
            # as a `module` symbol so top-of-file docs are searchable (was
            # silently dropped, so Chinese module docs were invisible).
            stem = Path(rel).stem
            result.symbols.insert(0, SymbolInfo(
                name=stem, kind="module", qualified_name=stem,
                signature="", doc=module_doc,
                start_line=1, end_line=1, start_col=0, end_col=0,
            ))
        symbols = [
            {
                "name": s.name,
                "kind": s.kind,
                "qualified_name": s.qualified_name,
                "signature": s.signature,
                "doc": s.doc,
                "start_line": s.start_line,
                "end_line": s.end_line,
                "start_col": s.start_col,
                "end_col": s.end_col,
            }
            for s in result.symbols
        ]
        fid = self.db.upsert_file(rel, lang, hash_, st.st_mtime, st.st_size)
        self.db.replace_file_symbols(fid, symbols)
        self.db.replace_file_imports(
            fid,
            [{"text": i.text, "kind": i.kind, "line": i.line} for i in result.imports],
        )
        self.db.register_fts(fid, rel, symbols)

        rows: list[tuple] = []
        sym_ids = [
            r[0]
            for r in self.db.conn.execute(
                "SELECT id FROM symbols WHERE file_id=? ORDER BY id", (fid,)
            )
        ]
        if len(sym_ids) != len(result.symbols):
            name_ids = self.db.symbol_name_to_id(fid)
            sym_ids = [name_ids.get(s.name) for s in result.symbols]
        for s, src in zip(result.symbols, sym_ids):
            if src is None:
                continue
            for c in s.calls:
                rows.append((src, c.target, c.rtype, c.line))
            for b in s.bases:
                rows.append((src, b.target, b.rtype, b.line))
        self.db.replace_file_relations(fid, rows)

def _resolve_all(db: DB) -> None:
    """Resolve relations whose target_id is NULL against known symbols.

    Strategy:
      - exact unique name -> link
      - multiple candidates -> pick the one whose class is imported in the
        caller's file (e.g. `from auth.service import AuthService`)
      - dotted target (auth.login) -> match qualified_name suffix
    """
    pending = db.conn.execute(
        "SELECT id, source_id, target, rtype FROM relations WHERE target_id IS NULL"
    ).fetchall()
    if not pending:
        return
    # caller-file imports, grouped
    import_text_by_file: dict[int, str] = {}
    for fid, text in db.conn.execute("SELECT file_id, text FROM file_imports"):
        import_text_by_file[fid] = import_text_by_file.get(fid, "") + " " + text.lower()

    sym_files = {
        r[0]: (r[1], r[2])
        for r in db.conn.execute("SELECT id, name, qualified_name FROM symbols")
    }
    caller_file = {
        sid: fid
        for fid, sid in db.conn.execute("SELECT file_id, id FROM symbols")
    }

    for rel_id, source_id, target, rtype in pending:
        if rtype == "calls":
            candidates = _candidates_for_target(db, sym_files, target)
            if len(candidates) == 1:
                db.apply_resolution(rel_id, candidates[0])
            elif len(candidates) > 1:
                src_fid = caller_file.get(source_id)
                imports = import_text_by_file.get(src_fid or -1, "")
                picked: list[int] = []
                if imports:
                    picked = [
                        cid for cid in candidates
                        if _class_hint(sym_files.get(cid, ("", ""))[1]) in imports
                    ]
                # `self.tutor_agent.run`: the attribute name maps 1:1 onto the
                # snake_cased owner class (TutorAgent -> tutor_agent), which
                # resolves the per-instance dispatch without needing source
                # assignment tracking.
                if len(picked) != 1 and target.startswith("self."):
                    parts = target.split(".")
                    # `self._method(...)`: bare member name, unique -> link it
                    if len(parts) == 2:
                        solo = db.resolve_single_name(parts[1])
                        if len(solo) == 1:
                            picked = solo
                    elif len(parts) >= 3:
                        attr = _snake(parts[1])
                        attr_picked = [
                            cid for cid in candidates
                            if _matches_attr(sym_files.get(cid, ("", ""))[1], attr)
                        ]
                        if len(attr_picked) == 1:
                            picked = attr_picked
                if len(picked) != 1 and "." not in target:
                    # bare-name target: prefer the exact qualified_name match —
                    # `new BizException()` should resolve to the class, not the
                    # same-named constructors (A9 impact_analysis blind spot).
                    exact = [
                        cid for cid in candidates
                        if sym_files.get(cid, ("", ""))[1] == target
                    ]
                    if len(exact) == 1:
                        picked = exact
                if len(picked) == 1:
                    db.apply_resolution(rel_id, picked[0])
        elif rtype == "inherits":
            # bases are class names: unique-name match, else skip (heuristic noise)
            candidates = [
                cid for cid in db.resolve_single_name(target)
                if sym_files.get(cid, ("", ""))[1].rsplit(".", 1)[-1] == target.rsplit(".", 1)[-1]
            ]
            if len(candidates) == 1:
                db.apply_resolution(rel_id, candidates[0])


def _candidates_for_target(db: DB, sym_files: dict[int, tuple], target: str) -> list[int]:
    candidates: list[int] = []
    if "." in target:
        last = target.rsplit(".", 1)[-1]
        cands_by_name = db.resolve_single_name(last)
        # prefer exact qualified suffix match
        for cid in cands_by_name:
            q = sym_files.get(cid, ("", ""))[1]
            if q == target or q.endswith("." + target):
                candidates.append(cid)
        if not candidates:
            candidates = cands_by_name
    else:
        candidates = db.resolve_single_name(target)
    return candidates


def _class_hint(qname: str) -> str:
    parts = qname.split(".")
    return parts[0].lower() if parts else ""


_SNAKE_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

def _snake(name: str) -> str:
    return _SNAKE_RE.sub("_", name).lower()


def _matches_attr(qname: str, attr: str) -> bool:
    """Does the symbol's owner class plausibly back ``self.<attr>``?

    Matches when the snake_cased class name equals the attr, or when the
    attr is the trailing snake token of the class name (self.llm -> DeepSeekLLM,
    self.agent -> BaseAgent) or a known prefix alias (self.llm -> LLMCLIENT).
    """
    if not qname:
        return False
    cls = qname.split(".")[0]
    if not cls:
        return False
    snake = _snake(cls)
    if snake == attr:
        return True
    tokens = snake.split("_")
    if len(tokens) > 1 and tokens[-1] == attr:
        return True
    return False


