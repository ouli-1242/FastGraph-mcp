"""Graph queries: symbol lookup, callers/callees, BFS trace, impact scoring."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from fastgraph.db import DB


def symbol_row(r: tuple) -> dict:
    return {
        "id": r[0],
        "name": r[1],
        "kind": r[2],
        "qualified_name": r[3],
        "signature": r[4],
        "start_line": r[5],
        "path": r[6],
    }


def find_symbols(db: DB, name: str, limit: int = 20) -> list[dict]:
    """Locate symbols by plain name or dotted qualified name.

    Supports ``Class.method`` (exact qualified_name match) and, for 3+ segments,
    ``module.Class.method`` — the leading segment is resolved to its
    module/class symbol's file, then the rest is matched against qualified
    names inside that file (A7).
    """
    q = (
        "SELECT s.id, s.name, s.kind, s.qualified_name, s.signature, s.start_line, "
        "f.path"
        " FROM symbols s JOIN files f ON f.id = s.file_id"
        " WHERE s.name = ? OR s.qualified_name = ?"
        " ORDER BY s.file_id LIMIT ?"
    )
    rows = db.conn.execute(q, (name, name, limit)).fetchall()
    parts = name.split(".")
    if len(parts) >= 3:
        qual = ".".join(parts[1:])
        # Leading segment is a module (file stem) or a class symbol. Resolve it
        # to a set of candidate file ids, then match the remaining segments as
        # a qualified_name inside those files. Does not depend on a module
        # symbol existing (the parser only emits one when the file has imports
        # or a docstring).
        file_ids: set[int] = {
            m[1]
            for m in db.conn.execute(
                "SELECT id, file_id FROM symbols WHERE name = ? AND kind IN ('module', 'class')",
                (parts[0],),
            ).fetchall()
        }
        for (fp,) in db.conn.execute("SELECT path FROM files"):
            if fp.rsplit("/", 1)[-1].rsplit(".", 1)[0] == parts[0]:
                r = db.conn.execute("SELECT id FROM files WHERE path=?", (fp,)).fetchone()
                if r:
                    file_ids.add(r[0])
        seen = {r[0] for r in rows}
        for fid in file_ids:
            extra = db.conn.execute(
                "SELECT s.id, s.name, s.kind, s.qualified_name, s.signature, s.start_line, "
                "f.path FROM symbols s JOIN files f ON f.id = s.file_id"
                " WHERE s.qualified_name = ? AND s.file_id = ? ORDER BY s.start_line LIMIT ?",
                (qual, fid, limit),
            ).fetchall()
            rows.extend(r for r in extra if r[0] not in seen)
            seen.update(r[0] for r in extra)
    return [symbol_row(r) for r in rows[:limit]]


def lookup_exact(db: DB, name: str) -> list[int]:
    ids: list[int] = []
    for r in db.conn.execute(
        "SELECT id FROM symbols WHERE name = ? OR qualified_name = ?", (name, name)
    ).fetchall():
        ids.append(r[0])
    return ids


def symbol_by_id(db: DB, sid: int) -> dict | None:
    r = db.conn.execute(
        """SELECT s.id, s.name, s.kind, s.qualified_name, s.signature, s.start_line,
                  s.end_line, s.doc, f.path, f.language
           FROM symbols s JOIN files f ON f.id = s.file_id WHERE s.id = ?""",
        (sid,),
    ).fetchone()
    if not r:
        return None
    return {
        "id": r[0],
        "name": r[1],
        "kind": r[2],
        "qualified_name": r[3],
        "signature": r[4],
        "start_line": r[5],
        "end_line": r[6],
        "doc": r[7],
        "path": r[8],
        "language": r[9],
    }


def callers(db: DB, sid: int) -> list[int]:
    """Direct in-edges (calls only): relations targeting sid."""
    rows = db.conn.execute(
        "SELECT source_id FROM relations WHERE target_id = ? AND rtype = 'calls'", (sid,)
    ).fetchall()
    return [r[0] for r in rows]


def caller_ids(db: DB, sid: int) -> list[int]:
    """Keep the old helper name for compatibility."""
    return callers(db, sid)


def callee_ids(db: DB, sid: int) -> list[int]:
    # calls only: inheritance is a hierarchy edge, not something the symbol
    # "calls" (was: pulls in `inherits` targets, so find_callees(Derived)
    # reported the base class — inconsistent with symbol_info.callees).
    rows = db.conn.execute(
        "SELECT target_id FROM relations WHERE source_id = ? "
        "AND target_id IS NOT NULL AND rtype = 'calls'",
        (sid,),
    ).fetchall()
    return [r[0] for r in rows]


def callee_names_with_lines(db: DB, sid: int) -> list[dict]:
    rows = db.conn.execute(
        """SELECT r.target, r.rtype, r.line FROM relations r
           WHERE r.source_id = ? ORDER BY r.line""",
        (sid,),
    ).fetchall()
    return [{"target": r[0], "rtype": r[1], "line": r[2]} for r in rows]


def find_callers(db: DB, name: str, limit: int = 30, depth: int = 1) -> list[dict]:
    """Who calls `name` (optionally transitively, BFS up to depth)."""
    roots = find_symbols(db, name)
    if not roots:
        return []
    seen: set[int] = set()
    frontier = _symbol_ids(roots)
    level = 0
    while frontier and level < depth:
        nxt: list[int] = []
        for sid in frontier:
            for c in callers(db, sid):
                if c not in seen:
                    seen.add(c)
                    nxt.append(c)
        frontier = nxt
        level += 1
    out = [symbol_by_id(db, s) for s in seen if s not in _symbol_ids(roots)]
    return [o for o in out if o][:limit]


def _symbol_ids(rows: list[dict]) -> set[int]:
    return {r["id"] for r in rows}


def find_callees(db: DB, name: str, limit: int = 50, depth: int = 1) -> list[dict]:
    roots = find_symbols(db, name)
    if not roots:
        return []
    root_ids = _symbol_ids(roots)
    seen: set[int] = set()
    frontier = list(root_ids)
    level = 0
    while frontier and level < depth:
        nxt: list[int] = []
        for sid in frontier:
            for c in callee_ids(db, sid):
                if c not in seen and c not in root_ids:
                    seen.add(c)
                    nxt.append(c)
        frontier = nxt
        level += 1
    return [o for o in (symbol_by_id(db, s) for s in seen) if o][:limit]


def _callers_with_class(db: DB, sid: int) -> list[int]:
    """Callers of a symbol, plus callers of its enclosing class's members.

    Resolves chains like ``chat() -> orchestrator.handle_chat -> ... -> TutorAgent``
    where the class-level init/instantiation is not recorded as a direct edge:
    anything calling ``Orchestrator.handle_chat`` is also an upstream of
    ``Orchestrator`` and of ``Orchestrator.__init__``.
    """
    out = list(callers(db, sid))
    info = symbol_by_id(db, sid)
    if info:
        qname = info.get("qualified_name") or ""
        cls = qname.rsplit(".", 1)[0] if "." in qname else None
        if cls:
            for r in db.conn.execute(
                "SELECT DISTINCT source_id FROM relations WHERE target = ? OR target LIKE ?",
                (cls, cls + ".%"),
            ):
                if r[0] not in out:
                    out.append(r[0])
    return out


def path_between(db: DB, from_name: str, to_name: str, max_depth: int = 8) -> list[list[dict]] | None:
    """BFS upward from `to_name` until we reach `from_name`. Returns path symbols."""
    src = find_symbols(db, from_name)
    tgt = find_symbols(db, to_name)
    if not src or not tgt:
        return None
    src_ids = _symbol_ids(src)
    # if `from_name` is a class, count its members as valid starting points
    member_ids = {
        r[0]
        for r in db.conn.execute(
            "SELECT s.id FROM symbols s WHERE s.name = ? OR s.qualified_name LIKE ?",
            (from_name, from_name + ".%"),
        )
    }
    src_ids |= member_ids
    tgt_ids = _symbol_ids(tgt)
    # multi-source BFS from targets upward, tracking parents
    parent: dict[int, int] = {}
    frontier = list(tgt_ids)
    visited = set(tgt_ids)
    found: int | None = None
    for depth in range(max_depth):
        nxt: list[int] = []
        for sid in frontier:
            for c in _callers_with_class(db, sid):
                if c in visited:
                    continue
                visited.add(c)
                parent[c] = sid
                if c in src_ids:
                    found = c
                    break
                nxt.append(c)
        if found is not None:
            break
        frontier = nxt
    if found is None:
        return None
    path: list[dict] = []
    cur = found
    while cur is not None:
        path.append(symbol_by_id(db, cur))
        cur = parent.get(cur)
    return [path]


def impact_analysis(db: DB, name: str, max_depth: int = 3, limit: int = 50) -> dict:
    """Reverse BFS from a symbol; bucket results HIGH (direct) / MEDIUM (indirect),
    and flag test-related files."""
    roots = find_symbols(db, name)
    if not roots:
        return {"symbol": name, "error": "not_found", "impact": {"HIGH": [], "MEDIUM": []}, "tests": []}
    root_ids = {r["id"] for r in roots}

    buckets: dict[str, list[dict]] = {"HIGH": [], "MEDIUM": []}
    seen: set[int] = set()
    frontier = list(root_ids)
    for depth in range(max_depth):
        nxt: list[int] = []
        for sid in frontier:
            for c in callers(db, sid):
                if c in seen or c in root_ids:
                    continue
                seen.add(c)
                info = symbol_by_id(db, c)
                if info:
                    key = "HIGH" if depth == 0 else "MEDIUM"
                    buckets[key].append(_impact_brief(info))
                nxt.append(c)
        frontier = nxt
        if not frontier:
            break

    def is_test(info: dict) -> bool:
        p = info.get("path", "").lower()
        return "test" in p or "spec" in p or "tests" in p

    tests = [i for b in buckets.values() for i in b if is_test(i)]
    for k in buckets:
        buckets[k] = [i for i in buckets[k] if not is_test(i)][:limit]

    total = len(buckets["HIGH"]) + len(buckets["MEDIUM"]) + len(tests)
    return {
        "symbol": name,
        "found": True,
        "impact": buckets,
        "tests": tests[:limit],
        "total_affected": total,
    }


def _impact_brief(info: dict) -> dict:
    return {
        "name": info["name"],
        "qualified_name": info["qualified_name"],
        "kind": info["kind"],
        "file": info["path"],
        "lines": f"{info['start_line']}-{info['end_line']}",
        "signature": (info["signature"] or "")[:120],
    }


_IMP_RE = re.compile(r"(?:from\s+|import\s*\{[^}]+\}\s*from\s*|import\s+|require\(|using\s+)(['\"]?)([\w./@~-]+)", re.IGNORECASE)


def _imported_names(text: str) -> list[str]:
    """Symbols actually brought into scope by one import line.

    ``from pkg.mod import A, B as C`` -> [A, B, C]; ``import pkg.mod`` -> [mod].
    Used by rename_impact so ``from app.agents.resource_agents import
    DocumentAgent`` no longer counts as a reference to ``RESOURCE_AGENTS``
    (which only shares the module path).
    """
    m = re.match(r"^\s*from\s+([\w.]+)\s+import\s+(.*)$", text.strip(), re.I)
    if m:
        names: list[str] = []
        for part in m.group(2).split(","):
            part = part.strip()
            if not part:
                continue
            if " as " in part:
                part = part.split(" as ", 1)[-1].strip()
            part = part.strip()
            if part and (part[0] in "\"'(" or part == "*"):
                continue  # 'import *', 'import ("a")', string wildcards
            names.append(part)
        return [n for n in names if n]
    m = re.match(r"^\s*import\s+(.+)$", text, re.I)
    if m:
        tail: list[str] = []
        for part in m.group(1).split(","):
            part = part.strip()
            if " as " in part:
                part = part.split(" as ", 1)[-1].strip()
            tail.append(part.rsplit(".", 1)[-1].strip())
        return [n for n in tail if n]
    return []


def _like_escape(s: str) -> str:
    """Escape LIKE wildcards so a path containing `%`/`_` matches literally
    (was: `my_file_v2.py` wildcard-matched an existing `myXfile_v2.py`)."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_file(db: DB, path: str) -> str | None:
    """Best-effort path lookup: exact match, then path-suffix, then basename.

    Clients (LLMs) often pass a bare filename like ``main.py`` while the
    index stores project-relative paths. If the hint is ambiguous, prefer
    an unambiguous suffix match and otherwise return None (caller reports).
    """
    if not path:
        return None
    p = path.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if p.endswith("/"):
        p = p.rstrip("/")
    exact: list[str] = [r[0] for r in db.conn.execute("SELECT path FROM files WHERE path = ?", (p,))]
    if len(exact) == 1:
        return exact[0]
    suffix = [
        r[0]
        for r in db.conn.execute(
            "SELECT path FROM files WHERE path LIKE ? ESCAPE '\\'", (f"%/{_like_escape(p)}",)
        )
    ]
    if len(suffix) == 1:
        return suffix[0]
    base = p.rsplit("/", 1)[-1]
    basenames = [
        r[0]
        for r in db.conn.execute(
            "SELECT path FROM files WHERE path LIKE ? ESCAPE '\\'", (f"%/{_like_escape(base)}",)
        )
    ]
    if len(basenames) == 1:
        return basenames[0]
    if len(suffix) > 1 and all(x.endswith("/" + p) for x in suffix):
        return None  # ambiguous: caller decides via candidates
    return None


def file_symbols(db: DB, path: str, limit: int = 200) -> list[dict]:
    """All indexed symbols declared in one file, in source order."""
    resolved = resolve_file(db, path)
    if resolved is None:
        return []
    rows = db.conn.execute(
        """SELECT s.id, s.name, s.kind, s.qualified_name, s.signature,
                  s.start_line, s.end_line, f.path
           FROM symbols s JOIN files f ON f.id = s.file_id
           WHERE f.path = ? ORDER BY s.start_line, s.start_col LIMIT ?""",
        (resolved, limit),
    ).fetchall()
    return [
        {"id": r[0], "symbol": r[1], "kind": r[2], "qualified_name": r[3],
         "signature": (r[4] or "")[:120], "lines": f"{r[5]}-{r[6]}", "file": r[7]}
        for r in rows
    ]


def import_targets(db: DB, import_text: str, import_file: str) -> list[str]:
    """Guess which indexed files an import statement refers to."""
    m = _IMP_RE.search(import_text)
    if not m:
        return []
    mod = m.group(2)
    if mod.startswith((".", "/")):
        rel = mod[1:] if mod.startswith(".") else mod
        mod = rel.replace("/", ".")
    else:
        mod = mod.replace("/", ".")
    mod = mod.strip(".")
    # CommonJS require paths often carry a file extension ("./x/index.js")
    # while the index stores extension-less stems; normalize before matching.
    for _ext in (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx"):
        if mod.endswith(_ext):
            mod = mod[: -len(_ext)]
            break
    dir_part = "/".join(import_file.split("/")[:-1])
    rel_root = f"{dir_part}/" if dir_part else ""
    candidates: list[str] = []
    # Match case-insensitively: Java/C# fully-qualified names are mixed-case
    # (com.travel...RateLimiter), and the file stems below are lowercased, so a
    # case-sensitive compare would never match them (A8).
    mod = mod.lower()
    for (p,) in db.conn.execute("SELECT path FROM files"):
        stem = p.rsplit(".", 1)[0].replace("/", ".").lower()
        if stem == mod or stem.endswith("." + mod):
            candidates.append(p)
        elif mod.startswith(".") and p.lower() == rel_root + mod[1:] + ".__init__":
            candidates.append(p)
    # `from pkg import a, b, c` (multi-symbol package import): the regex only
    # captured `pkg`, so also resolve each imported name against pkg's dir.
    _multi = re.match(r"\s*from\s+[\w./@~-]+\s+import\s+(.+)", import_text, re.IGNORECASE)
    if not candidates and _multi:
        names_part = _multi.group(1).split(" as ")[0]
        for name in (n.strip().rstrip(",") for n in names_part.split(",")):
            if not name or "." in name or name in ("*", "(", ")"):
                continue
            target = f"{mod}.{name.lower()}"
            for (p,) in db.conn.execute("SELECT path FROM files"):
                stem = p.rsplit(".", 1)[0].replace("/", ".").lower()
                if stem == target or stem.endswith("." + target):
                    if p not in candidates:
                        candidates.append(p)
    return candidates[:5]


def module_dependencies(db: DB, path: str) -> dict:
    """File-level import view: what a file imports, and who imports it."""
    resolved = resolve_file(db, path)
    if resolved is None:
        ambiguous = [
            r[0]
            for r in db.conn.execute(
                "SELECT path FROM files WHERE path LIKE ? ORDER BY path",
                (f"%/{path.rsplit('/', 1)[-1]}",),
            )
        ]
        if len(ambiguous) > 1:
            return {"found": False, "path": path, "ambiguous": True, "candidates": ambiguous[:10]}
        return {"found": False, "path": path}
    fid = db.get_file_id(resolved)
    if fid is None:
        return {"found": False, "path": resolved}
    imports: list[dict] = []
    for text, line in db.conn.execute(
        "SELECT text, line FROM file_imports WHERE file_id = ? ORDER BY line", (fid,)
    ):
        imports.append({"text": text[:120], "line": line, "resolves_to": import_targets(db, text, path)})

    stem = path.rsplit(".", 1)[0].replace("/", ".").lower()
    importers: list[dict] = []
    for (fid2, p) in db.conn.execute("SELECT id, path FROM files WHERE id != ?", (fid,)):
        for text, line in db.conn.execute(
            "SELECT text, line FROM file_imports WHERE file_id=?", (fid2,)
        ):
            for t in import_targets(db, text, p):
                tstem = t.rsplit(".", 1)[0].replace("/", ".").lower()
                if tstem == stem or tstem.endswith("." + stem):
                    importers.append({"file": p, "line": line, "import": text[:120]})
                    break
    return {"found": True, "imports": imports[:30], "importers": importers[:30]}


def project_overview(db: DB) -> dict:
    files = db.conn.execute(
        "SELECT path, language FROM files ORDER BY path"
    ).fetchall()
    by_lang: dict[str, int] = {}
    for _, lang in files:
        by_lang[lang] = by_lang.get(lang, 0) + 1
    return {
        "files": len(files),
        "symbols": db.count_symbols(),
        "languages": by_lang,
        "top_level": {
            r[0]: r[1]
            for r in db.conn.execute(
                "SELECT substr(path, 1, instr(path, '/') - 1) || '', COUNT(*) FROM files "
                "WHERE instr(path, '/') > 0 GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
            )
        },
        "entry_points": _entry_points(db),
        "layering": _layering(db),
        "parse_errors": [e["file"] for e in db.parse_errors(limit=50)],
    }


_ENTRY_NAMES = {
    "main.py", "__main__.py", "app.py", "cli.py", "manage.py", "serve.py",
    "app.ts", "index.ts", "index.js", "index.tsx", "index.jsx", "main.ts", "main.go", "main.rs",
}


def _entry_points(db: DB) -> list[str]:
    """Files conventionally treated as entry points (main/app/index/cli)."""
    out: list[str] = []
    for (p,) in db.conn.execute("SELECT path FROM files ORDER BY path"):
        if Path(p).name in _ENTRY_NAMES and not p.startswith("test") and "test" not in p.lower():
            out.append(p)
    return out[:10]


def _layering(db: DB) -> dict:
    """Top-level dependency direction summary: dir A -> dir B counts."""
    counts: dict[str, int] = {}
    row_map = {r[0]: r[1] for r in db.conn.execute("SELECT id, path FROM files")}
    # use file-level imports instead: module -> module edges
    pairs: dict[tuple[str, str], int] = {}
    for (fid, text) in db.conn.execute("SELECT file_id, text FROM file_imports"):
        src = row_map.get(fid)
        if not src:
            continue
        src_top = src.split("/")[0] if "/" in src else "(root)"
        for cand in import_targets(db, text, src):
            tgt_top = cand.split("/")[0] if "/" in cand else "(root)"
            if src_top != tgt_top and "/" in cand:
                k = (src_top, tgt_top)
                pairs[k] = pairs.get(k, 0) + 1
    for (a, b), n in sorted(pairs.items(), key=lambda kv: -kv[1])[:10]:
        counts[f"{a} -> {b}"] = n
    return counts


def rename_impact(db: DB, name: str, limit: int = 100) -> dict:
    """Rename/change risk: every definition + every reference of a symbol.

    Uses both resolved relations (calls/inherits) and raw import text.
    """
    defs = find_symbols(db, name)
    refs: list[dict] = []
    if defs:
        # 1) references resolved via relations (callers/inheritors)
        for r in db.conn.execute(
            """SELECT s.name, f.path, r.line, r.rtype, r.target
               FROM relations r JOIN symbols s ON s.id = r.source_id
               JOIN files f ON f.id = s.file_id
               WHERE r.target_id IN (%s) OR r.target = ?
               ORDER BY f.path, r.line LIMIT ?"""
            % ",".join("?" * len(defs)),
            tuple(d["id"] for d in defs) + (name, limit),
        ):
            refs.append({"symbol": r[0], "file": r[1], "line": r[2], "rtype": r[3], "via_text": r[4][:60]})

        # 1b) unresolved same-name call targets (e.g. super().login), only for
        #     qualified requests, so bare-name renames don't explode
        if "." in name:
            short = name.rsplit(".", 1)[-1]
            for r in db.conn.execute(
                """SELECT s.name, f.path, r.line, r.rtype, r.target
                   FROM relations r JOIN symbols s ON s.id = r.source_id
                   JOIN files f ON f.id = s.file_id
                   WHERE r.rtype = 'calls' AND r.target_id IS NULL AND r.target = ?
                   ORDER BY f.path, r.line LIMIT ?""",
                (short, limit),
            ):
                refs.append({"symbol": r[0], "file": r[1], "line": r[2], "rtype": r[3], "via_text": r[4][:60]})

        # 2) raw text occurrences in other files' symbols (docstrings, qualified uses)
        for r in db.conn.execute(
            """SELECT s.name, s.kind, s.qualified_name, s.start_line, f.path
               FROM symbols s JOIN files f ON f.id = s.file_id
               WHERE s.name != ? AND s.qualified_name LIKE ?
               ORDER BY f.path, s.start_line LIMIT ?""",
            (name, f"%.{name}", limit),
        ):
            refs.append({"symbol": r[0], "file": r[4], "line": r[3], "rtype": "uses", "via_text": r[2][:60]})

    # 3) import statements that actually bind the symbol's name (e.g.
    #    `from app.agents.orchestrator import orchestrator` when renaming
    #    Orchestrator, or `from app.services.llm import deepseek_llm` when
    #    renaming the deepseek_llm variable). Only the imported name counts:
    #    a shared *module path* (resource_agents) is not a symbol reference.
    #    Runs even when the symbol is unindexed (module-level instances),
    #    because imports still tell us who is affected.
    import_like = f"%{name.lower()}%"
    for r in db.conn.execute(
        """SELECT text, line, f.path
           FROM file_imports fi JOIN files f ON f.id = fi.file_id
           WHERE LOWER(text) LIKE ?
           ORDER BY f.path, fi.line LIMIT ?""",
        (import_like, limit),
    ):
        imported = _imported_names(r[0])
        if not any(name.lower() in n.lower() for n in imported):
            continue
        refs.append({"symbol": name, "file": r[2], "line": r[1], "rtype": "import", "via_text": r[0][:60]})
        if len(refs) >= limit:
            break

    risk = _risk_grade(defs, refs)
    return {
        "symbol": name,
        "definitions": [_brief(s) for s in defs],
        "references": refs[:limit],
        "definition_count": len(defs),
        "reference_count": len(refs),
        "risk": risk,
    }


def _risk_grade(defs: list[dict], refs: list[dict]) -> dict:
    """Risk hints for symbol-level change: HIGH/MEDIUM/LOW + breakdown.

    HIGH = public API (class/interface/struct) with many callers or tests.
    MEDIUM = internal with test coverage. LOW = leaf/internal usage only.
    """
    public_kinds = {"class", "interface", "struct", "enum", "impl", "type", "delegate"}
    has_public_def = any(d.get("kind") in public_kinds for d in defs)
    tests = sum(1 for r in refs if "test" in r.get("file", "").lower() or "spec" in r.get("file", "").lower())
    callers = sum(1 for r in refs if r.get("rtype") in ("calls", "uses"))
    n = len(refs)

    if has_public_def and (callers or tests):
        grade = "HIGH"
    elif has_public_def or (tests and n):
        grade = "MEDIUM"
    else:
        grade = "LOW"

    return {
        "grade": grade,
        "public_definitions": sum(1 for d in defs if d.get("kind") in public_kinds),
        "reference_sites": n,
        "test_sites": tests,
        "hint": (
            f"{grade} risk: {n} reference sites, {tests} in tests"
            + (", public API" if has_public_def else ", internal")
        ),
    }


def _brief(sym: dict) -> dict:
    return {
        "symbol": sym.get("name"),
        "qualified_name": sym.get("qualified_name"),
        "kind": sym.get("kind"),
        "file": sym.get("path"),
        "lines": f"{sym.get('start_line')}-{sym.get('end_line')}" if sym.get("end_line") else f"{sym.get('start_line')}",
        "signature": (sym.get("signature") or "")[:120],
    }


def type_hierarchy(db: DB, name: str) -> dict:
    """Class hierarchy: ancestors (bases) and descendants (subclasses), BFS."""
    roots = find_symbols(db, name)
    if not roots:
        return {"symbol": name, "found": False}

    def ancestors_of(sid: int) -> list[dict]:
        out: list[dict] = []
        frontier = {sid}
        seen = set(frontier)
        for _ in range(6):
            nxt: set[int] = set()
            for cid in frontier:
                for r in db.conn.execute(
                    """SELECT s.id FROM relations r JOIN symbols s ON s.id = r.target_id
                       WHERE r.rtype = 'inherits' AND r.source_id = ? AND r.target_id IS NOT NULL""",
                    (cid,),
                ):
                    if r[0] not in seen:
                        seen.add(r[0])
                        nxt.add(r[0])
            if not nxt:
                break
            out.extend(symbol_by_id(db, i) for i in nxt)
            frontier = nxt
        return out

    def descendants_of(sid: int) -> list[dict]:
        out: list[dict] = []
        frontier = {sid}
        # seed seen with the root itself so a cyclic hierarchy (A extends B,
        # B extends A) cannot leak the root back into its own descendants.
        seen = {sid}
        for _ in range(6):
            nxt: set[int] = set()
            for cid in frontier:
                for r in db.conn.execute(
                    """SELECT DISTINCT r.source_id FROM relations r
                       WHERE r.rtype = 'inherits' AND r.target_id = ?""",
                    (cid,),
                ):
                    if r[0] not in seen:
                        seen.add(r[0])
                        nxt.add(r[0])
            if not nxt:
                break
            out.extend(symbol_by_id(db, i) for i in nxt)
            frontier = nxt
        return out

    return {
        "symbol": name,
        "found": True,
        "matches": len(roots),
        "ancestors": [_brief(s) for s in ancestors_of(roots[0]["id"])][:50],
        "descendants": [_brief(s) for s in descendants_of(roots[0]["id"])][:50],
    }