"""FastGraph-MCP server: 17-tool surface over SQLite graph."""

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
            "FastGraph: lightweight project map, code search, call graph and impact analysis "
            "for the active project. Results are compact locations/symbols/relationships only.\n"
            "Scope: a project-wide navigation layer — global symbol/content search, call graph, "
            "dependency view, impact analysis, git-change awareness. It does NOT read or edit "
            "code; keep precise per-file editing to your editor-side tools. Prefer FastGraph over "
            "grep/read for locating code or planning a change.\n"
            "Typical workflow:\n"
            "- FIRST, if the folder differs from the server default: activate_project(root=\"/abs/path\"); "
            "each tool also accepts root=<abs path> for a one-off cross-project query.\n"
            "- understand the repo: project_overview()\n"
            "- locate: code_search(query) — project-wide, matches names and comment/string content (CJK ok)\n"
            "- inspect a file's shape before reading it: file_symbols(path)\n"
            "- understand a symbol: symbol_info(name); who calls what: find_callers(sym) / find_callees(sym); "
            "chain: trace_path(from, to); inheritance: type_hierarchy(sym)\n"
            "- before editing: impact_analysis(sym); before renaming: rename_impact(sym); imports: file_deps(path)\n"
            "- dead code / hotspots / file size / import cycles: unused_symbols(), hot_symbols(), file_metrics(), module_cycles()\n"
            "- after edits: changed_context()\n"
            "Keep limit small (10-20); every result is compact by design."
        ),
    )

    @server.tool()
    def activate_project(root: str) -> dict:
        """Point the server at a project folder for this session; afterwards all tools run against it. Every tool also accepts root=<abs path> for a one-off cross-project query."""
        return tools.activate_project(root)

    @server.tool()
    def code_search(query: str, limit: int = 10, kind: str | None = None, root: str | None = None) -> dict:
        """Find where code lives by keyword, symbol name, or natural language. First choice when the exact symbol name is unknown."""
        return tools.code_search(query, limit=limit, kind=kind, root=root)

    @server.tool()
    def symbol_info(symbol: str, root: str | None = None) -> dict:
        """Details for a symbol (plain name or Class.method): file, lines, signature, doc, and what it calls."""
        return tools.symbol_info(symbol, root=root)

    @server.tool()
    def find_callers(symbol: str, limit: int = 30, depth: int = 1, root: str | None = None) -> dict:
        """Who calls this symbol. depth>=2 returns transitive callers."""
        return tools.find_callers(symbol, limit=limit, depth=depth, root=root)

    @server.tool()
    def find_callees(symbol: str, limit: int = 50, depth: int = 1, root: str | None = None) -> dict:
        """What this symbol calls. depth>=2 returns the downstream call tree."""
        return tools.find_callees(symbol, limit=limit, depth=depth, root=root)

    @server.tool()
    def trace_path(from_symbol: str, to_symbol: str | None = None, depth: int = 20, root: str | None = None) -> dict:
        """Call chain between two symbols; without to_symbol returns the callers of from_symbol."""
        return tools.trace_path(from_symbol, to_symbol, depth=depth, root=root)

    @server.tool()
    def impact_analysis(symbol: str, max_depth: int = 2, limit: int = 40, root: str | None = None) -> dict:
        """Who is affected if this symbol changes: direct callers (HIGH), indirect (MEDIUM), tests listed separately."""
        return tools.impact_analysis(symbol, max_depth=max_depth, limit=limit, root=root)

    @server.tool()
    def changed_context(limit: int = 50, root: str | None = None) -> dict:
        """After edits: git diff -> changed files/symbols -> affected callers."""
        return tools.changed_context(limit=limit, root=root)

    @server.tool()
    def project_overview(root: str | None = None) -> dict:
        """Project map: languages, file/symbol counts, entry points, top-level layout, cross-module dependency direction, parse errors."""
        return tools.project_overview(root=root)

    @server.tool()
    def file_symbols(path: str, limit: int = 200, root: str | None = None) -> dict:
        """Symbols in one file (kind, signature, lines) - inspect structure before reading the file."""
        return tools.file_symbols(path, limit=limit, root=root)

    @server.tool()
    def file_deps(path: str, root: str | None = None) -> dict:
        """What a file imports (internal resolved, external listed separately) and which files import it."""
        return tools.file_deps(path, root=root)

    @server.tool()
    def rename_impact(symbol: str, limit: int = 100, root: str | None = None) -> dict:
        """Before renaming: all definitions + reference sites with HIGH/MEDIUM/LOW risk grade."""
        return tools.rename_impact(symbol, limit=limit, root=root)

    @server.tool()
    def type_hierarchy(symbol: str, root: str | None = None) -> dict:
        """Class inheritance: ancestors and descendants. Check before editing a base class."""
        return tools.type_hierarchy(symbol, root=root)

    @server.tool()
    def unused_symbols(limit: int = 50, root: str | None = None) -> dict:
        """Potentially dead code: methods/functions with no incoming call edge (tests, entry points, interface members excluded). Candidate list: confirm with find_callers before deleting."""
        return tools.unused_symbols(limit=limit, root=root)

    @server.tool()
    def hot_symbols(limit: int = 20, root: str | None = None) -> dict:
        """Most-referenced symbols by incoming call count, with a test/main split. Quick answer to 'what is the core of this repo'."""
        return tools.hot_symbols(limit=limit, root=root)

    @server.tool()
    def file_metrics(limit: int = 20, root: str | None = None) -> dict:
        """Per-file aggregation (symbol count, outgoing/incoming call edges) to spot large or complex files."""
        return tools.file_metrics(limit=limit, root=root)

    @server.tool()
    def module_cycles(max_cycles: int = 10, root: str | None = None) -> dict:
        """Import cycles between files (strongly connected components), largest first. Architecture health check."""
        return tools.module_cycles(max_cycles=max_cycles, root=root)

    return server


def run_stdio(root):
    server = build_server(root)
    server.run()