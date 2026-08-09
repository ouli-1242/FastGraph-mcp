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
            "FastGraph: lightweight project map, code search, call graph and impact analysis."
            "Use FastGraph for codebase understanding."
            "Use Serena or native tools for source inspection and code modification."
            "Prefer FastGraph over manual grep/search when locating unknown code."
            "Avoid scanning large parts of the repository or reading many files without first using FastGraph."
            "Results are compact and include file locations and symbols when applicable, Results should be compact.\n"
            "Return locations, symbols and relationships by default."
            "Only include source snippets when explicitly requested or necessary."
            "Pick tool by task:\n"
            "- new to repo / big picture: project_overview() (entry points, top-level layout, "
            "cross-module dependency direction, parse errors)\n"
            "- find where a symbol/feature lives: code_search()\n"
            "- symbol detail (signature, doc): symbol_info(); whole file layout first: file_symbols()\n"
            "- who calls / what it calls: find_callers() / find_callees(); chain between two: trace_path()\n"
            "- inherited family: type_hierarchy()\n"
            "- BEFORE touching code: impact_analysis(symbol) for blast radius; "
            "rename_impact(symbol) before renaming to evaluate change risk(risk grade HIGH/MEDIUM/LOW)\n"
            "- file-level imports: file_deps(path)\n"
            "- AFTER edits: changed_context() to re-sync with git diff.\n"
            "Output size discipline: prefer limit=10-20; Prioritize relevance over completeness.; Avoid flooding context with low-value results.; results are compact by design."
        ),
    )

    @server.tool()
    def code_search(query: str, limit: int = 10, kind: str | None = None) -> dict:
        """Find where code lives by keywords/natural language. First choice when you don't know the symbol name."""
        return tools.code_search(query, limit=limit, kind=kind)

    @server.tool()
    def symbol_info(symbol: str) -> dict:
        """Details for a symbol by name or qualified name (A.B.foo): file, lines, signature, doc, callees."""
        return tools.symbol_info(symbol)

    @server.tool()
    def find_callers(symbol: str, limit: int = 30, depth: int = 1) -> dict:
        """Who calls this symbol. depth=2+ for transitive callers (who calls the callers)."""
        return tools.find_callers(symbol, limit=limit, depth=depth)

    @server.tool()
    def find_callees(symbol: str, limit: int = 50, depth: int = 1) -> dict:
        """What this symbol calls. depth=2+ for the full downstream call tree."""
        return tools.find_callees(symbol, limit=limit, depth=depth)

    @server.tool()
    def trace_path(from_symbol: str, to_symbol: str | None = None, depth: int = 3) -> dict:
        """Call chain between two symbols; without to_symbol returns the up-chain of callers."""
        return tools.trace_path(from_symbol, to_symbol, depth=depth)

    @server.tool()
    def impact_analysis(symbol: str, max_depth: int = 2, limit: int = 40) -> dict:
        """MUST call before editing: reverse-BFS blast radius. HIGH=direct callers, MEDIUM=indirect, tests separated."""
        return tools.impact_analysis(symbol, max_depth=max_depth, limit=limit)

    @server.tool()
    def changed_context(limit: int = 50) -> dict:
        """Call after edits: git diff -> changed symbols -> affected callers. Syncs your changes to the index."""
        return tools.changed_context(limit=limit)

    @server.tool()
    def project_overview() -> dict:
        """Project map: languages, file/symbol counts, entry points, top-level layout, cross-module dependency direction, parse errors."""
        return tools.project_overview()

    @server.tool()
    def file_symbols(path: str, limit: int = 200) -> dict:
        """All symbols in one file (line ranges, kinds, signatures). Read this instead of the whole file when possible."""
        return tools.file_symbols(path, limit=limit)

    @server.tool()
    def file_deps(path: str) -> dict:
        """What a file imports and which files import it. Check before changing imports or module structure."""
        return tools.file_deps(path)

    @server.tool()
    def rename_impact(symbol: str, limit: int = 100) -> dict:
        """Call BEFORE renaming: all definitions + reference sites with HIGH/MEDIUM/LOW risk grade (public API, test sites)."""
        return tools.rename_impact(symbol, limit=limit)

    @server.tool()
    def type_hierarchy(symbol: str) -> dict:
        """Class inheritance: ancestors (bases) and descendants (subclasses). Check before editing a base class."""
        return tools.type_hierarchy(symbol)

    return server


def run_stdio(root):
    server = build_server(root)
    server.run()