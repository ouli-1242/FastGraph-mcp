"""End-to-end tests: index a sample project, exercise the 8 tools."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph.db import DB
from fastgraph.index import Indexer
from fastgraph.tools import Toolbox

SAMPLE = Path(__file__).resolve().parent / "sample_project"
WORK = Path(__file__).resolve().parent / "work"


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def build_sample(root: Path):
    toplevel = root / "src"
    write(toplevel / "auth/service.py", '''"""Auth service."""
from user.repo import UserRepository

class BaseService:
    pass

class AuthService(BaseService):
    """Handles login."""

    def login(self, username, password):
        repo = UserRepository()
        user = repo.find_by_username(username)
        return user is not None and user.verify_password(password)

    def _create_session(self, username):
        return Session().start(username)

class OAuthService(AuthService):
    def login(self, username, password):
        return super().login(username, password)
''')
    write(toplevel / "auth/controller.py", '''\
from auth.service import AuthService

class LoginController:
    def login(self, req):
        auth = AuthService()
        return auth.login(req["username"], req["password"])
''')
    write(toplevel / "models/user.py", '''\
class User:
    def verify_password(self, pw):
        return pw == "secret"
''')
    write(toplevel / "db/repo.py", '''\
from user.model import User

class UserRepository:
    def find_by_username(self, name):
        return User(name)

    def find_by_id(self, uid):
        return User(uid)
''')
    write(toplevel / "user/model.py", '''\
class User:
    def __init__(self, name=""):
        self.name = name

    def verify_password(self, pw):
        return pw == "secret"
''')
    write(root / "tests/test_auth.py", '''\
from auth.controller import LoginController

def test_login_ok():
    assert LoginController().login({"username": "u", "password": "secret"}) is True
''')


@pytest.fixture(scope="module")
def toolbox():
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    build_sample(WORK)
    db = DB(WORK)
    indexer = Indexer(WORK, db)
    tools = Toolbox(WORK, db, indexer)
    indexer.refresh()
    yield tools
    db.close()
    shutil.rmtree(WORK, ignore_errors=True)


def test_code_search(toolbox):
    hits = toolbox.code_search("login")["results"]
    names = {h["symbol"] for h in hits}
    assert "login" in names


def test_symbol_info(toolbox):
    info = toolbox.symbol_info("AuthService.login")
    assert info["found"]
    assert info["matches"][0]["qualified_name"] == "AuthService.login"


def test_find_callers(toolbox):
    callers = toolbox.find_callers("AuthService.login")["callers"]
    assert any(c["qualified_name"] == "LoginController.login" for c in callers)


def test_find_callees(toolbox):
    callees = toolbox.find_callees("AuthService.login")["callees"]
    assert any(c["qualified_name"] == "UserRepository.find_by_username" for c in callees)
    # per-call targets include the local-variable call, even if not class-resolved
    info = toolbox.symbol_info("AuthService.login")
    targets = {c["target"] for c in info["matches"][0]["callees"]}
    assert "verify_password" in targets


def test_trace_path(toolbox):
    path = toolbox.trace_path("LoginController", "find_by_username")["path"]
    assert path is not None
    qnames = [p["qualified_name"] for p in path]
    assert "LoginController.login" in qnames
    assert "AuthService.login" in qnames


def test_impact_high(toolbox):
    imp = toolbox.impact_analysis("AuthService.login")
    high = {s["name"] for s in imp["impact"]["HIGH"]}
    assert "login" in high  # LoginController.login


def test_project_overview(toolbox):
    ov = toolbox.project_overview()
    assert ov["files"] == 6
    assert "python" in ov["languages"]
    assert "parse_errors" in ov


def test_file_symbols(toolbox):
    syms = toolbox.file_symbols("src/auth/service.py")["symbols"]
    names = {s["symbol"] for s in syms}
    assert "AuthService" in names
    assert "login" in names
    assert all("lines" in s for s in syms)


def test_file_deps(toolbox):
    deps = toolbox.file_deps("src/auth/service.py")
    assert deps["found"]
    assert any("user.repo" in i["text"] for i in deps["imports"])
    assert any(i["file"] == "src/auth/controller.py" for i in deps["importers"])


def test_rename_impact(toolbox):
    imp = toolbox.rename_impact("AuthService.login")
    assert imp["definition_count"] == 1
    assert imp["reference_count"] >= 1
    files = {r["file"] for r in imp["references"]}
    assert "src/auth/controller.py" in files  # call site
    assert "src/auth/service.py" in files     # OAuthService super() usage


def test_type_hierarchy(toolbox):
    hier = toolbox.type_hierarchy("AuthService")
    assert hier["found"]
    anc = {a["qualified_name"] for a in hier["ancestors"]}
    desc = {d["qualified_name"] for d in hier["descendants"]}
    assert "BaseService" in anc
    assert "OAuthService" in desc


def test_incremental_update(toolbox):
    before = toolbox.db.count_symbols()
    # edit one file -> only that file re-parsed
    write(WORK / "src/auth/service.py", "class AuthService:\n    def login(self):\n        return True\n")
    stats = toolbox._ensure_fresh()
    assert stats["parsed"] == 1
    assert stats["scanned"] == 6
    # symbol still found, new shape
    info = toolbox.symbol_info("AuthService.login")
    assert info["found"]


def test_parse_errors_reported(toolbox):
    write(WORK / "src/broken.py", "def (\n")
    stats = toolbox._ensure_fresh()
    assert stats["errors"] == 1
    ov = toolbox.project_overview()
    assert "src/broken.py" in ov["parse_errors"]


def test_changed_context_git(toolbox):
    """changed_files requires a git repo; skip if git unavailable."""
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    git_init = subprocess.run(["git", "init"], cwd=str(WORK), capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=str(WORK), capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
        cwd=str(WORK), capture_output=True,
    )
    write(WORK / "src/auth/service.py", "class \n    pass\n")
    res = toolbox.changed_context()
    assert "src/auth/service.py" in res["changed_files"]
    assert res["changed_symbols"]