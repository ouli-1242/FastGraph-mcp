""".fastgraphignore skips matching entries; works from the project root and
from .fastgraph/ (local-only), with incremental removal on refresh."""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph.db import DB
from fastgraph.index import Indexer

WORK = Path(__file__).resolve().parent / "work_ignore"


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _make_project(files: dict[str, str]):
    _rmtree(WORK)
    for rel, content in files.items():
        p = WORK / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _paths(db: DB) -> list[str]:
    return [r[0] for r in db.conn.execute("SELECT path FROM files ORDER BY path")]


def test_ignore_vendor_dir_from_root():
    _make_project({
        ".fastgraphignore": "vendor\n",
        "src/a.py": "x = 1\n",
        "vendor/lib.py": "y = 2\n",
        "src/vendor/helper.py": "z = 3\n",
    })
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    paths = _paths(db)
    assert "src/a.py" in paths
    assert "vendor/lib.py" not in paths
    assert "src/vendor/helper.py" not in paths  # name match at any depth
    db.close()
    _rmtree(WORK)


def test_ignore_local_config_in_fastgraph_dir():
    _rmtree(WORK)
    (WORK / "src").mkdir(parents=True, exist_ok=True)
    (WORK / "gen").mkdir(parents=True, exist_ok=True)
    (WORK / "src/a.py").write_text("x = 1\n", encoding="utf-8")
    (WORK / "gen/out.js").write_text("y = 2\n", encoding="utf-8")
    (WORK / ".fastgraph").mkdir(parents=True, exist_ok=True)
    (WORK / ".fastgraph/.fastgraphignore").write_text("gen\n", encoding="utf-8")
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    paths = _paths(db)
    assert "src/a.py" in paths
    assert "gen/out.js" not in paths
    db.close()
    _rmtree(WORK)


def test_incremental_removal_when_ignored_later():
    _make_project({
        "src/a.py": "x = 1\n",
        "legacy/b.py": "y = 2\n",
    })
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    assert "legacy/b.py" in _paths(db)
    (WORK / ".fastgraphignore").write_text("legacy\n", encoding="utf-8")
    stats = Indexer(WORK, db).refresh()
    assert stats.deleted >= 1
    assert "legacy/b.py" not in _paths(db)
    assert "src/a.py" in _paths(db)
    db.close()
    _rmtree(WORK)


def test_path_glob_pattern():
    _make_project({
        ".fastgraphignore": "generated/*.min.js\n",
        "generated/bundle.min.js": "x\n",
        "generated/source.js": "y\n",
        "src/main.js": "z\n",
    })
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    paths = _paths(db)
    assert "generated/bundle.min.js" not in paths
    assert "generated/source.js" in paths
    assert "src/main.js" in paths
    db.close()
    _rmtree(WORK)