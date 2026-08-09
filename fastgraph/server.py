"""FastGraph-MCP server: 13-tool surface over SQLite graph."""

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
            "- FIRST: if working on a project folder OTHER than the server default, call "
            "activate_project(root=\"<absolute path>\") to point the server at it (Serena-style; "
            "no other tool needs the path afterwards). When unsure about the current root, call "
            "project_overview() and check its root field.\n"
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
    def activate_project(root: str) -> dict:
        """Point the server at a project folder for this session (Serena-style). Use BEFORE other tools when the folder you work in differs from the server default; afterwards tools run against the activated root."""
        return tools.activate_project(root)

    @server.tool()
    def code_search(query: str, limit: int = 10, kind: str | None = None, root: str | None = None) -> dict:
        """Find where code lives by keywords/natural language. First choice when you don't know the symbol name. Optional root=<absolute path> searches another project (desktop clients)."""
        return tools.code_search(query, limit=limit, kind=kind, root=root)

    @server.tool()
    def symbol_info(symbol: str, root: str | None = None) -> dict:
        """Details for a symbol by name or qualified name (A.B.foo): file, lines, signature, doc, callees. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.symbol_info(symbol, root=root)

    @server.tool()
    def find_callers(symbol: str, limit: int = 30, depth: int = 1, root: str | None = None) -> dict:
        """Who calls this symbol. depth=2+ for transitive callers. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.find_callers(symbol, limit=limit, depth=depth, root=root)

    @server.tool()
    def find_callees(symbol: str, limit: int = 50, depth: int = 1, root: str | None = None) -> dict:
        """What this symbol calls. depth=2+ for the full downstream call tree. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.find_callees(symbol, limit=limit, depth=depth, root=root)

    @server.tool()
    def trace_path(from_symbol: str, to_symbol: str | None = None, depth: int = 3, root: str | None = None) -> dict:
        """Call chain between two symbols; without to_symbol returns the up-chain of callers. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.trace_path(from_symbol, to_symbol, depth=depth, root=root)

    @server.tool()
    def impact_analysis(symbol: str, max_depth: int = 2, limit: int = 40, root: str | None = None) -> dict:
        """MUST call before editing: reverse-BFS blast radius. HIGH=direct callers, MEDIUM=indirect, tests separated. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.impact_analysis(symbol, max_depth=max_depth, limit=limit, root=root)

    @server.tool()
    def changed_context(limit: int = 50, root: str | None = None) -> dict:
        """Call after edits: git diff -> changed symbols -> affected callers. Syncs your changes to the index. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.changed_context(limit=limit, root=root)

    @server.tool()
    def project_overview(root: str | None = None) -> dict:
        """Project map: languages, file/symbol counts, entry points, top-level layout, cross-module dependency direction, parse errors. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.project_overview(root=root)

    @server.tool()
    def file_symbols(path: str, limit: int = 200, root: str | None = None) -> dict:
        """All symbols in one file (line ranges, kinds, signatures). Read this instead of the whole file when possible. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.file_symbols(path, limit=limit, root=root)

    @server.tool()
    def file_deps(path: str, root: str | None = None) -> dict:
        """What a file imports and which files import it. Check before changing imports or module structure. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.file_deps(path, root=root)

    @server.tool()
    def rename_impact(symbol: str, limit: int = 100, root: str | None = None) -> dict:
        """Call BEFORE renaming: all definitions + reference sites with HIGH/MEDIUM/LOW risk grade (public API, test sites). Optional root=<absolute path> for another project (desktop clients)."""
        return tools.rename_impact(symbol, limit=limit, root=root)

    @server.tool()
    def type_hierarchy(symbol: str, root: str | None = None) -> dict:
        """Class inheritance: ancestors (bases) and descendants (subclasses). Check before editing a base class. Optional root=<absolute path> for another project (desktop clients)."""
        return tools.type_hierarchy(symbol, root=root)

    return server


def run_stdio(root):
    server = build_server(root)
    server.run()