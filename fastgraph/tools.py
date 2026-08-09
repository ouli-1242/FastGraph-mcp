"""MCP tool implementations (the 8-tool surface)."""

from __future__ import annotations

import json
import time

from fastgraph.db import DB
from fastgraph import graph, gitutil
from fastgraph.index import Indexer
from fastgraph.search import code_search


class Toolbox:
    """Stateful tool handler bound to one project root."""

    def __init__(self, root, db: DB, indexer: Indexer):
        self.root = root
        self.db = db
        self.indexer = indexer

    # ------------------------------------------------------------- helpers

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

    def code_search(self, query: str, limit: int = 10, kind: str | None = None) -> dict:
        t0 = time.perf_counter()
        refresh = self._ensure_fresh()
        hits = code_search(self.db, query, limit=limit, kind=kind)
        return {
            "results": hits,
            "count": len(hits),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def symbol_info(self, symbol: str) -> dict:
        t0 = time.perf_counter()
        refresh = self._ensure_fresh()
        syms = graph.find_symbols(self.db, symbol)
        if not syms:
            return {"found": False, "symbol": symbol, "refresh": refresh, "ms": round((time.perf_counter() - t0) * 1000, 1)}
        out = []
        for s in syms[:3]:
            info = graph.symbol_by_id(self.db, s["id"])
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
                "callees": [c for c in graph.callee_names_with_lines(self.db, info["id"]) if c["rtype"] == "calls"][:20],
            })
        return {"found": True, "symbol": symbol, "matches": out, "refresh": refresh, "ms": round((time.perf_counter() - t0) * 1000, 1)}

    def find_callers(self, symbol: str, limit: int = 30, depth: int = 1) -> dict:
        t0 = time.perf_counter()
        refresh = self._ensure_fresh()
        callers = graph.find_callers(self.db, symbol, limit=limit, depth=depth)
        return {
            "symbol": symbol,
            "callers": [self._brief(s) for s in callers],
            "count": len(callers),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def find_callees(self, symbol: str, limit: int = 50, depth: int = 1) -> dict:
        t0 = time.perf_counter()
        refresh = self._ensure_fresh()
        callees = graph.find_callees(self.db, symbol, limit=limit, depth=depth)
        return {
            "symbol": symbol,
            "callees": [self._brief(s) for s in callees],
            "count": len(callees),
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def trace_path(self, from_symbol: str, to_symbol: str | None = None, depth: int = 3) -> dict:
        t0 = time.perf_counter()
        refresh = self._ensure_fresh()
        if to_symbol:
            path = graph.path_between(self.db, from_symbol, to_symbol)
            return {
                "from": from_symbol,
                "to": to_symbol,
                "path": [self._brief(s) for s in path[0]] if path else None,
                "refresh": refresh,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        # no target: show the symbol's call chain upward
        callers = graph.find_callers(self.db, from_symbol, limit=15, depth=depth)
        return {
            "from": from_symbol,
            "chain": [self._brief(s) for s in callers],
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def impact_analysis(self, symbol: str, max_depth: int = 2, limit: int = 40) -> dict:
        t0 = time.perf_counter()
        refresh = self._ensure_fresh()
        result = graph.impact_analysis(self.db, symbol, max_depth=max_depth, limit=limit)
        result["refresh"] = refresh
        result["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return result

    def changed_context(self, limit: int = 50) -> dict:
        t0 = time.perf_counter()
        refresh = self._ensure_fresh()
        changes = gitutil.changed_files(self.root)
        by_status = gitutil.changed_symbols(self.db, changes)

        affected: list[dict] = []
        seen: set[int] = set()
        for status, syms in by_status.items():
            for s in syms:
                for c in graph.callers(self.db, s["id"]):
                    if c in seen:
                        continue
                    seen.add(c)
                    info = graph.symbol_by_id(self.db, c)
                    if info:
                        affected.append(self._brief(info))
        return {
            "changed_files": changes,
            "changed_symbols": {k: [{"symbol": s["name"], "qualified_name": s["qualified_name"], "file": s["path"], "line": s["start_line"]} for s in v] for k, v in by_status.items()},
            "affected_callers": affected[:limit],
            "refresh": refresh,
            "ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    def project_overview(self) -> dict:
        t0 = time.perf_counter()
        refresh = self._ensure_fresh()
        overview = graph.project_overview(self.db)
        overview["root"] = str(self.root)
        overview["refresh"] = refresh
        overview["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        return overview