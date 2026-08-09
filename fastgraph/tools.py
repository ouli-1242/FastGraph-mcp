"""MCP tool implementations (the 13-tool surface)."""

from __future__ import annotations

import time
from pathlib import Path

from fastgraph.db import DB
from fastgraph import graph, gitutil
from fastgraph.index import Indexer
from fastgraph.search import code_search


class Toolbox:
    """Stateful tool handler bound to one project root.

    An optional per-call ``root`` argument switches to another project:
    a dedicated (DB, indexer) pair is lazily created and cached per root.
    This makes the server usable from desktop clients, which launch MCP
    processes with a fixed cwd and cannot pass the working folder.
    """

    def __init__(self, root, db: DB, indexer: Indexer):
        self.root = root
        self.db = db
        self.indexer = indexer
        self._active_root: Path | None = None
        self._roots: dict[Path, "Toolbox"] = {}

    # ------------------------------------------------------------- helpers

    def activate_project(self, root: str | None) -> dict:
        """Set the session-wide default project root (Serena-style activation).

        Works without any prior state, so desktop agents can point the server
        at the folder they are working on. Returns a description of what the
        new active root indexes.
        """
        path = Path(root).resolve() if root else self.root.resolve()
        if not path.is_dir():
            return {"ok": False, "error": f"root {path} is not an existing directory", "active_root": str(self._active_root) if self._active_root else str(self.root)}
        if path == self.root.resolve():
            self._active_root = None
        else:
            if path not in self._roots:
                db = DB(path)
                self._roots[path] = Toolbox(path, db, Indexer(path, db))
            self._active_root = path
        return {"ok": True, "active_root": str(self._active_root or self.root)}

    def _for_root(self, root: str | None):
        """Return the toolbox for ``root`` (default: active root, else this one)."""
        if root is None:
            if self._active_root is not None and self._active_root != self.root.resolve():
                return self._roots[self._active_root]
            return self
        path = Path(root).resolve()
        if path == self.root.resolve():
            return self
        if path in self._roots:
            return self._roots[path]
        if not path.is_dir():
            raise ValueError(f"root {path} is not an existing directory")
        db = DB(path)
        sub = Toolbox(path, db, Indexer(path, db))
        self._roots[path] = sub
        return sub

    def _ensure_fresh(self) -> dict:
        """Lazy incremental refresh: only changed files re-parsed."""
        stats = self.indexer.refresh()
        return {
            "scanned": stats.scanned,
            "parsed": stats.parsed,
            "deleted": stats.deleted,
            "errors": stats.errors,
            "refresh_ms": round(stats.duration_ms, 1),
        }

    def _brief(self, sym: dict) -> dict:
        """Compact symbol view: no source body, just location + signature."""
        return {
            "symbol": sym.get("name"),
            "qualified_name": sym.get("qualified_name"),
            "kind": sym.get("kind"),
            "file": sym.get("path"),
            "lines": f"{sym.get('start_line')}-{sym.get('end_line')}",
            "signature": (sym.get("signature") or "")[:120],
        }

    def _locate(self, symbol: str) -> list[dict]:
        rows = graph.find_symbols(self.db, symbol)
        return [self._brief(s) for s in rows]

    # --------------------------------------------------------------- tools

    def code_search(self, query: str, limit: int = 10, kind: str | None = None, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        hits = code_search(tb.db, query, limit=limit, kind=kind)
        return {
            "results": hits,
            "count": len(hits),
            "root": str(tb.root),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def symbol_info(self, symbol: str, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        syms = graph.find_symbols(tb.db, symbol)
        if not syms:
            return {"found": False, "symbol": symbol, "root": str(tb.root), "refresh": refresh, "ms": round((time.perf_counter() - t0) * 1000, 1)}
        out = []
        for s in syms[:3]:
            info = graph.symbol_by_id(tb.db, s["id"])
            if not info:
                continue
            out.append({
                "symbol": info["name"],
                "qualified_name": info["qualified_name"],
                "kind": info["kind"],
                "file": info["path"],
                "lines": f"{info['start_line']}-{info['end_line']}",
                "signature": (info["signature"] or "")[:120],
                "doc": (info["doc"] or "")[:200],
                "callees": [c for c in graph.callee_names_with_lines(tb.db, info["id"]) if c["rtype"] == "calls"][:20],
            })
        return {"found": True, "symbol": symbol, "matches": out, "root": str(tb.root), "refresh": refresh, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def find_callers(self, symbol: str, limit: int = 30, depth: int = 1, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        callers = graph.find_callers(tb.db, symbol, limit=limit, depth=depth)
        return {
            "symbol": symbol,
            "callers": [tb._brief(s) for s in callers],
            "count": len(callers),
            "root": str(tb.root),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def find_callees(self, symbol: str, limit: int = 50, depth: int = 1, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        callees = graph.find_callees(tb.db, symbol, limit=limit, depth=depth)
        return {
            "symbol": symbol,
            "callees": [tb._brief(s) for s in callees],
            "count": len(callees),
            "root": str(tb.root),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def trace_path(self, from_symbol: str, to_symbol: str | None = None, depth: int = 3, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        if to_symbol:
            path = graph.path_between(tb.db, from_symbol, to_symbol)
            return {
                "from": from_symbol,
                "to": to_symbol,
                "path": [tb._brief(s) for s in path[0]] if path else None,
                "root": str(tb.root),
                "refresh": refresh,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        # no target: show the symbol's call chain upward
        callers = graph.find_callers(tb.db, from_symbol, limit=15, depth=depth)
        return {
            "from": from_symbol,
            "chain": [tb._brief(s) for s in callers],
            "root": str(tb.root),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def impact_analysis(self, symbol: str, max_depth: int = 2, limit: int = 40, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        result = graph.impact_analysis(tb.db, symbol, max_depth=max_depth, limit=limit)
        result["root"] = str(tb.root)
        result["refresh"] = refresh
        result["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return result

    def changed_context(self, limit: int = 50, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        changes = gitutil.changed_files(tb.root)
        by_status = gitutil.changed_symbols(tb.db, changes)

        affected: list[dict] = []
        seen: set[int] = set()
        for status, syms in by_status.items():
            for s in syms:
                for c in graph.callers(tb.db, s["id"]):
                    if c in seen:
                        continue
                    seen.add(c)
                    info = graph.symbol_by_id(tb.db, c)
                    if info:
                        affected.append(tb._brief(info))
        return {
            "changed_files": changes,
            "changed_symbols": {k: [{"symbol": s["name"], "qualified_name": s["qualified_name"], "file": s["path"], "line": s["start_line"]} for s in v] for k, v in by_status.items()},
            "affected_callers": affected[:limit],
            "root": str(tb.root),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def project_overview(self, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        overview = graph.project_overview(tb.db)
        overview["root"] = str(tb.root)
        overview["refresh"] = refresh
        overview["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return overview

    # ---------------- Serena 补充工具 ----------------

    def file_symbols(self, path: str, limit: int = 200, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        syms = graph.file_symbols(tb.db, path, limit=limit)
        return {
            "file": path,
            "symbols": syms,
            "count": len(syms),
            "root": str(tb.root),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def file_deps(self, path: str, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        deps = graph.module_dependencies(tb.db, path)
        deps["file"] = path
        deps["root"] = str(tb.root)
        deps["refresh"] = refresh
        deps["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return deps

    def rename_impact(self, symbol: str, limit: int = 100, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        impact = graph.rename_impact(tb.db, symbol, limit=limit)
        impact["root"] = str(tb.root)
        impact["refresh"] = refresh
        impact["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return impact

    def type_hierarchy(self, symbol: str, root: str | None = None) -> dict:
        t0 = time.perf_counter()
        tb = self._for_root(root)
        refresh = tb._ensure_fresh()
        hier = graph.type_hierarchy(tb.db, symbol)
        hier["root"] = str(tb.root)
        hier["refresh"] = refresh
        hier["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return hier