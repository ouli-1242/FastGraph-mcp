"""FTS5-backed symbol search with name-first boost."""

from __future__ import annotations

import re

from fastgraph.db import DB

_STOPWORDS: set[str] = set()


def _tokens(q: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z0-9_]+", q.lower()) if len(t) >= 2]


def code_search(db: DB, query: str, limit: int = 10, kind: str | None = None) -> list[dict]:
    """Search across symbol names, qualified names, docstrings.

    Strategy: exact-name matches first, then FTS prefix matches, then doc prose.
    """
    results: list[dict] = []
    tokens = _tokens(query)
    if not tokens:
        return results

    # 1) exact & prefix name matches
    name_clauses, params = ["s.name = ?", "s.qualified_name = ?"], [tokens[0], tokens[0]]
    for t in tokens[1:3]:
        name_clauses.append("s.name LIKE ?")
        params.append(f"%{t}%")
    if kind:
        name_clauses.append("s.kind = ?")
        params.append(kind)
    sql = (
        """SELECT s.id, s.name, s.kind, s.qualified_name, s.signature, s.start_line, f.path
           FROM symbols s JOIN files f ON f.id = s.file_id
           WHERE """ + " OR ".join(name_clauses) + " LIMIT ?"
    )
    rows = db.conn.execute(sql, params + [limit]).fetchall()

    # 2) FTS doc/signature search
    fts_query = " AND ".join(f'"{t}"*' for t in tokens[:3])
    fts_sql = """SELECT name, qualified_name, file_path FROM fts_symbols WHERE fts MATCH ?"""
    try:
        fts_hits = db.conn.execute(fts_sql, (fts_query,)).fetchall()
    except Exception:
        fts_hits = []
    fts_rows: list[tuple] = []
    for _name, _qname, _path in fts_hits:
        r = db.conn.execute(
            """SELECT id, name, kind, qualified_name, signature, start_line, path
               FROM symbols s JOIN files f ON f.id = s.file_id
               WHERE s.file_id = (SELECT id FROM files WHERE path = ?)
                 AND s.name = ? LIMIT 1""",
            (_path, _name),
        ).fetchone()
        if r:
            fts_rows.append(r)

    seen: set[int] = set()
    for r in list(rows) + list(fts_rows):
        if r[0] in seen:
            continue
        seen.add(r[0])
        results.append(_make_hit(r, query))
        if len(results) >= limit:
            break
    return results


def _make_hit(r: tuple, query: str) -> dict:
    return {
        "symbol": r[1],
        "kind": r[2],
        "qualified_name": r[3],
        "signature": r[4],
        "line": r[5],
        "file": r[6],
        "match": "name",
    }