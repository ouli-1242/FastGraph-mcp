"""Incremental indexer: walk -> mtime/size diff -> re-parse changed only."""

from __future__ import annotations

import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from fastgraph.config import DEFAULT_EXCLUDES, MAX_FILE_SIZE
from fastgraph.db import DB
from fastgraph.parsers.registry import get_adapter

_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "tsx", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "cpp", ".h": "cpp", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
}

MAX_PARSE_WORKERS = min(8, (os.cpu_count() or 2))


@dataclass
class IndexStats:
    scanned: int = 0
    parsed: int = 0
    deleted: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    total_files: int = 0
    total_symbols: int = 0


class Indexer:
    def __init__(self, root: Path, db: DB, excludes: set[str] | None = None):
        self.root = root.resolve()
        self.db = db
        self.excludes = excludes or DEFAULT_EXCLUDES

    def refresh(self) -> IndexStats:
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
                    continue
                self._store_file(rel, st, hash_, result)
                stats.parsed += 1

        if stats.parsed or stats.deleted:
            _resolve_all(self.db)
            self.db.commit()

        stats.duration_ms = (time.perf_counter() - start) * 1000
        stats.total_files = len(seen)
        stats.total_symbols = self.db.count_symbols()
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
        while stack:
            d = stack.pop()
            try:
                entries = os.scandir(d)
            except OSError:
                continue
            with entries as it:
                for e in it:
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
        name_ids = self.db.symbol_name_to_id(fid)
        for s in result.symbols:
            src = name_ids.get(s.name)
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
        if rtype != "calls":
            continue
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

        if len(candidates) == 1:
            db.apply_resolution(rel_id, candidates[0])
        elif len(candidates) > 1:
            src_fid = caller_file.get(source_id)
            imports = import_text_by_file.get(src_fid or -1, "")
            if imports:
                picked = [
                    cid for cid in candidates
                    if _class_hint(sym_files.get(cid, ("", ""))[1]) in imports
                ]
                if len(picked) == 1:
                    db.apply_resolution(rel_id, picked[0])


def _class_hint(qname: str) -> str:
    parts = qname.split(".")
    return parts[0].lower() if parts else ""


_EXT_LANG = {
    ".py": "python",
    ".ts": "typescript", ".tsx": "tsx", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "cpp", ".h": "cpp", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
}

