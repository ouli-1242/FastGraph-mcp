"""SQLite storage: files / symbols / relations / file_imports + FTS5."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY,
    path       TEXT NOT NULL UNIQUE,
    language   TEXT NOT NULL,
    hash       TEXT NOT NULL DEFAULT '',
    mtime      REAL NOT NULL DEFAULT 0,
    size       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY,
    file_id        INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    kind           TEXT NOT NULL,
    qualified_name TEXT NOT NULL DEFAULT '',
    signature      TEXT NOT NULL DEFAULT '',
    doc            TEXT NOT NULL DEFAULT '',
    start_line     INTEGER NOT NULL,
    end_line       INTEGER NOT NULL,
    start_col      INTEGER NOT NULL DEFAULT 0,
    end_col        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sym_name    ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_sym_qname   ON symbols(qualified_name);
CREATE INDEX IF NOT EXISTS idx_sym_file    ON symbols(file_id);

CREATE TABLE IF NOT EXISTS relations (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER NOT NULL REFERENCES symbols(id) ON DELETE CASCADE,
    target      TEXT NOT NULL,
    rtype       TEXT NOT NULL,          -- calls | inherits
    target_id   INTEGER REFERENCES symbols(id) ON DELETE CASCADE,
    line        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relations(target_id);

CREATE TABLE IF NOT EXISTS file_imports (
    id       INTEGER PRIMARY KEY,
    file_id  INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    text     TEXT NOT NULL,
    kind     TEXT NOT NULL DEFAULT '',
    line     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fimp_file ON file_imports(file_id);

CREATE TABLE IF NOT EXISTS parse_errors (
    path   TEXT PRIMARY KEY,
    error  TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS fts_symbols USING fts5(
    name, qualified_name, doc, kind, signature,
    file_path,
    content=''
);
"""


class DB:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.index_dir = self.root / ".fastgraph"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.index_dir / "index.sqlite"
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ---------------- files ----------------

    def file_map(self) -> dict[str, float]:
        return {
            p: m
            for p, m in self.conn.execute("SELECT path, mtime FROM files")
        }

    def get_file_id(self, path: str) -> int | None:
        r = self.conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
        return r[0] if r else None

    def upsert_file(self, path: str, language: str, hash_: str, mtime: float, size: int) -> int:
        fid = self.get_file_id(path)
        if fid is None:
            cur = self.conn.execute(
                "INSERT INTO files (path, language, hash, mtime, size) VALUES (?,?,?,?,?)",
                (path, language, hash_, mtime, size),
            )
            return int(cur.lastrowid)
        self.conn.execute(
            "UPDATE files SET language=?, hash=?, mtime=?, size=? WHERE id=?",
            (language, hash_, mtime, size, fid),
        )
        return fid

    def delete_file(self, path: str):
        self.conn.execute("DELETE FROM files WHERE path=?", (path,))
        self.conn.execute("DELETE FROM parse_errors WHERE path=?", (path,))

    def file_path(self, file_id: int) -> str | None:
        r = self.conn.execute("SELECT path FROM files WHERE id=?", (file_id,)).fetchone()
        return r[0] if r else None

    # ---------------- symbols ----------------

    def replace_file_symbols(self, file_id: int, symbols: list[dict]) -> None:
        # any relation pointing at this file's old symbols: drop the id link,
        # the text resolver will re-attach after re-index
        old_ids = [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM symbols WHERE file_id=?", (file_id,)
            )
        ]
        if old_ids:
            self.conn.execute(
                "UPDATE relations SET target_id=NULL WHERE target_id IN (%s)"
                % ",".join("?" * len(old_ids)),
                old_ids,
            )
        self.conn.execute("DELETE FROM symbols WHERE file_id=?", (file_id,))
        if not symbols:
            return
        self.conn.executemany(
            "INSERT INTO symbols (file_id, name, kind, qualified_name, signature, doc, "
            "start_line, end_line, start_col, end_col) VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    file_id, s["name"], s["kind"], s["qualified_name"], s["signature"],
                    s["doc"], s["start_line"], s["end_line"], s["start_col"], s["end_col"],
                )
                for s in symbols
            ],
        )

    def replace_file_imports(self, file_id: int, imports: list[dict]) -> None:
        self.conn.execute("DELETE FROM file_imports WHERE file_id=?", (file_id,))
        if not imports:
            return
        self.conn.executemany(
            "INSERT INTO file_imports (file_id, text, kind, line) VALUES (?,?,?,?)",
            [(file_id, i["text"], i.get("kind", ""), i.get("line", 0)) for i in imports],
        )

    def replace_file_relations(self, file_id: int, rows: list[tuple[int, int]]) -> None:
        """rows: (source_symbol_id, target_text, rtype, line)"""
        # delete old relations of this file (all symbols cascaded already but
        # relations may reference symbols in other files: they carry no file_id,
        # so delete via source ids).
        self.conn.execute(
            "DELETE FROM relations WHERE source_id IN "
            "(SELECT id FROM symbols WHERE file_id=?)",
            (file_id,),
        )
        if rows:
            self.conn.executemany(
                "INSERT INTO relations (source_id, target, rtype, line) VALUES (?,?,?,?)",
                rows,
            )

    def symbol_name_to_id(self, file_id: int) -> dict:
        return {
            r[0]: r[1]
            for r in self.conn.execute(
                "SELECT name, id FROM symbols WHERE file_id=?", (file_id,)
            )
        }

    # ---------------- FTS ----------------

    def register_fts(self, file_id: int, path: str, symbols: list[dict]) -> None:
        self.conn.execute("DELETE FROM fts_symbols WHERE file_path=?", (path,))
        if not symbols:
            return
        self.conn.executemany(
            "INSERT INTO fts_symbols (name, qualified_name, doc, file_path) VALUES (?,?,?,?)",
            [(s["name"], s["qualified_name"], s["doc"][:400], path) for s in symbols],
        )

    # ---------------- resolution ----------------

    def resolve_single_name(self, name: str) -> list[int]:
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM symbols WHERE name=? ORDER BY file_id LIMIT 30", (name,)
            )
        ]

    def resolve_qualified(self, qname: str) -> list[int]:
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM symbols WHERE qualified_name=? OR qualified_name LIKE ? LIMIT 30",
                (qname, qname + "%"),
            )
        ]

    def apply_resolution(self, rel_id: int, target_id: int | None):
        self.conn.execute(
            "UPDATE relations SET target_id=? WHERE id=?", (target_id, rel_id)
        )

    # ---------------- stats ----------------

    def count_symbols(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]

    def count_files(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def commit(self):
        self.conn.commit()

    # ---------------- parse errors ----------------

    def set_parse_error(self, path: str, error: str):
        self.conn.execute(
            "INSERT INTO parse_errors (path, error) VALUES (?, ?) "
            "ON CONFLICT(path) DO UPDATE SET error = excluded.error",
            (path, error),
        )

    def clear_parse_error(self, path: str):
        self.conn.execute("DELETE FROM parse_errors WHERE path=?", (path,))

    def parse_errors(self, limit: int = 50) -> list[dict]:
        return [
            {"file": r[0], "error": r[1]}
            for r in self.conn.execute(
                "SELECT path, error FROM parse_errors ORDER BY path LIMIT ?", (limit,)
            )
        ]