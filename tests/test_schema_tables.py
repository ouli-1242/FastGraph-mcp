"""New tables (template_refs, line_content) exist after DB init."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph.db import DB

WORK = Path(__file__).resolve().parent / "work_schema"


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _tables(db: DB) -> set[str]:
    return {
        r[0]
        for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_new_tables_exist():
    _rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    db = DB(WORK)
    names = _tables(db)
    assert "template_refs" in names
    assert "line_content" in names
    db.close()
    _rmtree(WORK)