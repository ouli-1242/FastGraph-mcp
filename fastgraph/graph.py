"""Graph queries: symbol lookup, callers/callees, BFS trace, impact scoring."""

from __future__ import annotations

import re
from collections import deque

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
    """Locate symbols by plain name or dotted qualified name."""
    q = (
        "SELECT s.id, s.name, s.kind, s.qualified_name, s.signature, s.start_line, "
        "f.path"
        " FROM symbols s JOIN files f ON f.id = s.file_id"
        " WHERE s.name = ? OR s.qualified_name = ?"
        " ORDER BY s.file_id LIMIT ?"
    )
    rows = db.conn.execute(q, (name, name, limit)).fetchall()
    return [symbol_row(r) for r in rows]


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
    """Direct in-edges: relations targeting sid."""
    rows = db.conn.execute("SELECT source_id FROM relations WHERE target_id = ?", (sid,)).fetchall()
    return [r[0] for r in rows]


def caller_ids(db: DB, sid: int) -> list[int]:
    """Keep the old helper name for compatibility."""
    return callers(db, sid)


def callee_ids(db: DB, sid: int) -> list[int]:
    rows = db.conn.execute(
        "SELECT target_id FROM relations WHERE source_id = ? AND target_id IS NOT NULL", (sid,)
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
            for c in callers(db, sid):
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


def file_symbols(db: DB, path: str, limit: int = 200) -> list[dict]:
    """All indexed symbols declared in one file, in source order."""
    rows = db.conn.execute(
        """SELECT s.id, s.name, s.kind, s.qualified_name, s.signature,
                  s.start_line, s.end_line, f.path
           FROM symbols s JOIN files f ON f.id = s.file_id
           WHERE f.path = ? ORDER BY s.start_line, s.start_col LIMIT ?""",
        (path, limit),
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
    dir_part = "/".join(import_file.split("/")[:-1])
    rel_root = f"{dir_part}/" if dir_part else ""
    candidates: list[str] = []
    for (p,) in db.conn.execute("SELECT path FROM files"):
        stem = p.rsplit(".", 1)[0].replace("/", ".").lower()
        if stem == mod or stem.endswith("." + mod):
            candidates.append(p)
        elif mod.startswith(".") and p.lower() == rel_root + mod[1:] + ".__init__":
            candidates.append(p)
    return candidates[:5]


def module_dependencies(db: DB, path: str) -> dict:
    """File-level import view: what a file imports, and who imports it."""
    fid = db.get_file_id(path)
    if fid is None:
        return {"found": False}
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
        "parse_errors": [e["file"] for e in db.parse_errors(limit=50)],
    }


def rename_impact(db: DB, name: str, limit: int = 100) -> dict:
    """Every definition + every reference of a symbol, for rename preview.

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
            refs.append({"symbol": r[0], "file": r[4], "line": r[3], "rtype": "uses", "via": r[2][:60]})

    return {
        "symbol": name,
        "definitions": [_brief(s) for s in defs],
        "references": refs[:limit],
        "definition_count": len(defs),
        "reference_count": len(refs),
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
        seen = set()
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