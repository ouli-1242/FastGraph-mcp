"""FastGraph-MCP server: 8-tool surface over SQLite graph."""

from __future__ import annotations

import sys

from mcp.server import MCPServer

from fastgraph.db import DB
from fastgraph.index import Indexer
from fastgraph.tools import Toolbox


def build_server(root) -> MCPServer:
    db = DB(root)
    indexer = Indexer(root, db)
    tools = Toolbox(root, db, indexer)

    server = MCPServer(
        "fastgraph",
        instructions=(
            "FastGraph: code map, search and impact analysis for coding agents. "
            "Use code_search to locate symbols, symbol_info for details, "
            "find_callers/find_callees for the call graph, type_hierarchy for "
            "inheritance, file_symbols/file_deps to understand a file, "
            "rename_impact before renaming, impact_analysis before editing, "
            "changed_context after edits. Never grep the tree yourself."
        ),
    )

    @server.tool()
    def code_search(query: str, limit: int = 10, kind: str | None = None) -> dict:
        """Find code by natural language or keywords. Returns file:line + symbol hits."""
        return tools.code_search(query, limit=limit, kind=kind)

    @server.tool()
    def symbol_info(symbol: str) -> dict:
        """Locate a symbol by name or qualified name (A.B.foo)."""
        return tools.symbol_info(symbol)

    @server.tool()
    def find_callers(symbol: str, limit: int = 30, depth: int = 1) -> dict:
        """Who calls this symbol (BFS, depth for transitive callers)."""
        return tools.find_callers(symbol, limit=limit, depth=depth)

    @server.tool()
    def find_callees(symbol: str, limit: int = 50, depth: int = 1) -> dict:
        """What this symbol calls."""
        return tools.find_callees(symbol, limit=limit, depth=depth)

    @server.tool()
    def trace_path(from_symbol: str, to_symbol: str | None = None, depth: int = 3) -> dict:
        """Shortest call chain from_symbol -> to_symbol (or up-chain if no target)."""
        return tools.trace_path(from_symbol, to_symbol, depth=depth)

    @server.tool()
    def impact_analysis(symbol: str, max_depth: int = 2, limit: int = 40) -> dict:
        """Reverse BFS blast radius: HIGH=direct callers, MEDIUM=indirect, tests listed."""
        return tools.impact_analysis(symbol, max_depth=max_depth, limit=limit)

    @server.tool()
    def changed_context(limit: int = 50) -> dict:
        """Git-aware: changed files -> changed symbols -> affected callers."""
        return tools.changed_context(limit=limit)

    @server.tool()
    def project_overview() -> dict:
        """Project map: languages, file/symbol counts, top-level layout, parse errors."""
        return tools.project_overview()

    @server.tool()
    def file_symbols(path: str, limit: int = 200) -> dict:
        """All symbols declared in one file (line ranges, kinds, signatures)."""
        return tools.file_symbols(path, limit=limit)

    @server.tool()
    def file_deps(path: str) -> dict:
        """File-level deps: what `path` imports, and which files import it."""
        return tools.file_deps(path)

    @server.tool()
    def rename_impact(symbol: str, limit: int = 100) -> dict:
        """Rename preview: every definition + reference site of `symbol`."""
        return tools.rename_impact(symbol, limit=limit)

    @server.tool()
    def type_hierarchy(symbol: str) -> dict:
        """Class inheritance: ancestors (bases) and descendants (subclasses)."""
        return tools.type_hierarchy(symbol)

    return server


def run_stdio(root):
    server = build_server(root)
    server.run()