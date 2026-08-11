# FastGraph 实测缺陷修复与仓库整理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 FastGraph-MCP 在真实项目实测发现的 4 个缺陷（unused_symbols 误报、内容/中文搜索、`@` 别名 file_deps、类级 callers/callees）并整理仓库。

**Architecture:** 全部改动落在现有分层内：新表写入 `db.py` SCHEMA、模板引用与内容行在 `index.py` 解析管线收集、查询逻辑在 `graph.py`/`search.py`、Vue 模板提取在 `parsers/frontend.py`。解析器公共模型 `base.py` 增加带默认值字段，不破坏其他语言适配器。

**Tech Stack:** Python 3.11+, sqlite3 (FTS5), tree-sitter, MCP SDK。零新依赖。

## Global Constraints

- 依赖 Python >= 3.11、mcp>=1.11、tree-sitter 系（pyproject.toml 已声明，不新增）
- 不新增第三方依赖（别名配置用 `json` 标准库 + 正则粗解析）
- `ParseResult` 新字段一律带默认值，保证 9 个解析器零改动兼容
- 输出保持精简：内容命中 snippet ≤ 80 字符；不返回文件正文
- `INDEX_VERSION` 从 "3" 升为 "4"（schema 变化触发一次性重建）
- 既有 86 个 pytest 必须保持通过
- 全部新增 fixture 用 `tests/work_*` 临时目录，模式对齐 `tests/test_analysis_tools.py`

---

### Task 1: 仓库清理 + e2e 迁移 + 测试纳入 git

**Files:**
- Delete: `dbg_add.py`, `dbg_add2.py`, `dbg_add3.py`, `dbg_callers.py`, `dbg_fts.py`, `dbg_plausible.py`, `dbg_resolve.py`, `dbg_sanity.py`, `repro_cycles.py`, `repro_min.py`, `verify_fixes.py`, `e2e_mcp_test.py`（移动）
- Create: `tests/test_e2e_mcp.py`（迁移自 `e2e_mcp_test.py`）
- Test: `tests/test_e2e_mcp.py`

**Interfaces:**
- Consumes: 无
- Produces: `tests/test_e2e_mcp.py` 读 `FASTGRAPH_TEST_ROOT` 环境变量

- [ ] **Step 1: 迁移 e2e 测试**，把 `e2e_mcp_test.py` 复制为 `tests/test_e2e_mcp.py`，修改头部：

```python
"""E2E MCP protocol test over stdio (mirrors how Cursor spawns the server).

Requires the package installed (`pip install -e .`) and FASTGRAPH_TEST_ROOT
pointing at a real project; skipped otherwise.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = os.environ.get("FASTGRAPH_TEST_ROOT", "")

pytestmark = pytest.mark.skipif(
    not ROOT,
    reason="FASTGRAPH_TEST_ROOT not set (points at a real project for E2E)",
)

# 原脚本其余部分不变，ROOT 常量改为上方环境变量读取
```

- [ ] **Step 2: 删除 11 个调试脚本**（`dbg_*.py` ×8、`repro_*.py` ×2、`verify_fixes.py`）
- [ ] **Step 3: 运行测试确认没被删除文件影响**

Run: `python -m pytest tests/ -q`
Expected: PASS（86 个，e2e 被 skip）

- [ ] **Step 4: git 纳入测试并提交**

```bash
git add tests/test_analysis_tools.py tests/test_gitutil.py tests/test_java_callers.py tests/test_e2e_mcp.py
git rm dbg_add.py dbg_add2.py dbg_add3.py dbg_callers.py dbg_fts.py dbg_plausible.py dbg_resolve.py dbg_sanity.py repro_cycles.py repro_min.py verify_fixes.py
git commit -m "chore: remove debug scratch scripts, move e2e test into tests/ with env-gated skip"
```

---

### Task 2: DB schema 扩展（template_refs + line_content）+ 索引版本升级

**Files:**
- Modify: `fastgraph/db.py`（SCHEMA 追加两表）
- Modify: `fastgraph/index.py:33`（INDEX_VERSION "3" → "4"）
- Test: `tests/test_schema_tables.py`（新建）

**Interfaces:**
- Produces: `DB.replace_file_template_refs(file_id, names)`、`DB.replace_file_line_content(file_id, rows)`（Task 3/6 使用）

- [ ] **Step 1: 写失败测试** `tests/test_schema_tables.py`：

```python
"""New tables (template_refs, line_content) exist after DB init."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph.db import DB

WORK = Path(__file__).resolve().parent / "work_schema"


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _tables(db: DB):
    return {
        r[0]
        for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_schema_tables.py -q`
Expected: FAIL（`template_refs` not in names）

- [ ] **Step 3: 实现 schema + DB 方法**，在 `db.py` SCHEMA 的 `parse_errors` 表后追加：

```sql
CREATE TABLE IF NOT EXISTS template_refs (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    PRIMARY KEY (file_id, name)
);

CREATE TABLE IF NOT EXISTS line_content (
    id      INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    line    INTEGER NOT NULL,
    kind    TEXT NOT NULL,
    text    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lc_file ON line_content(file_id);
```

并在 DB 类追加两个方法：

```python
def replace_file_template_refs(self, file_id: int, names: list[str]):
    self.conn.execute("DELETE FROM template_refs WHERE file_id=?", (file_id,))
    if names:
        self.conn.executemany(
            "INSERT OR IGNORE INTO template_refs (file_id, name) VALUES (?, ?)",
            [(file_id, n) for n in names],
        )

def replace_file_line_content(self, file_id: int, rows: list[tuple[int, str, str]]):
    """rows: (line, kind, text)"""
    self.conn.execute("DELETE FROM line_content WHERE file_id=?", (file_id,))
    if rows:
        self.conn.executemany(
            "INSERT INTO line_content (file_id, line, kind, text) VALUES (?,?,?,?)",
            [(file_id, ln, kind, txt) for ln, kind, txt in rows],
        )
```

`index.py` 的 `INDEX_VERSION = "3"` 改为 `"4"`。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_schema_tables.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add fastgraph/db.py fastgraph/index.py tests/test_schema_tables.py
git commit -m "feat(db): add template_refs + line_content tables, bump index version to 4"
```

---

### Task 3: Vue/Svelte 模板引用提取

**Files:**
- Modify: `fastgraph/parsers/base.py`（ParseResult 加字段）
- Modify: `fastgraph/parsers/frontend.py`（SfcAdapter 提取模板引用）
- Modify: `fastgraph/index.py`（`_store_file` 写 template_refs）
- Test: `tests/test_frontend_template_refs.py`（新建）

**Interfaces:**
- Consumes: `DB.replace_file_template_refs(file_id, names)`（Task 2）
- Produces: `ParseResult.template_refs: list[str]`（Task 4 的 unused_symbols 消费 `template_refs` 表）

- [ ] **Step 1: 写失败测试** `tests/test_frontend_template_refs.py`：

```python
"""Vue template identifier references are captured into template_refs."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph.db import DB
from fastgraph.index import Indexer

WORK = Path(__file__).resolve().parent / "work_tplrefs"
VUE = """\
<template>
  <view>
    <text @click="handleX">Hi {{ label }}</text>
    <view v-if="visible" :class="cls">A</view>
  </view>
</template>
<script>
export default {
  data() { return { label: 'a', visible: true, cls: 'b' }; },
  methods: {
    handleX() { console.log('x'); },
    unusedY() { console.log('y'); },
  },
};
</script>
"""


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _refs(db: DB) -> set[str]:
    return {r[0] for r in db.conn.execute("SELECT name FROM template_refs")}


def test_vue_template_refs_captured():
    _rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "comp.vue").write_text(VUE, encoding="utf-8")
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    refs = _refs(db)
    assert "handleX" in refs   # @click binding
    assert "visible" in refs   # v-if binding
    db.close()
    _rmtree(WORK)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_frontend_template_refs.py -q`
Expected: FAIL（template_refs 为空）

- [ ] **Step 3: 实现提取**。`base.py` 的 `ParseResult` 追加：

```python
@dataclass
class ParseResult:
    language: str
    symbols: list[SymbolInfo]
    imports: list[ImportRef]
    module_doc: str = ""
    template_refs: list[str] = field(default_factory=list)
```

`frontend.py` 追加两个函数并在 `SfcAdapter.parse` 组装：

```python
_TEMPLATE_EXPR_RE = re.compile(
    r"""\{\{\s*([^{}]+?)\s*\}\}
    |@[A-Za-z0-9_.-]+\s*=\s*"([^"]*)"
    |:[A-Za-z0-9_.-]+\s*=\s*"([^"]*)"
    |v-on:[A-Za-z0-9_.-]+\s*=\s*"([^"]*)"
    |v-bind:[A-Za-z0-9_.-]+\s*=\s*"([^"]*)"
    |v-(?:if|show|for|model|html|text)\s*=\s*"([^"]*)"
    """,
    re.VERBOSE,
)
_TMPL_SKIP = {"true", "false", "null", "undefined", "this", "event",
              "$event", "item", "index", "key", "value"}


def _template_text(source_text: str) -> str:
    """Blank every <script> block (preserving newlines) so only template remains."""
    return _SCRIPT_RE.sub(lambda m: "\n" * m.group(0].count("\n"), source_text) if False else _SCRIPT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), source_text)


def _template_refs(template_text: str) -> list[str]:
    refs: set[str] = set()
    for m in _TEMPLATE_EXPR_RE.finditer(template_text):
        expr = next((g for g in m.groups() if g is not None), "")
        for ident in re.findall(r"[A-Za-z_$][\w$]*", expr):
            if ident not in _TMPL_SKIP:
                refs.add(ident)
    return sorted(refs)
```

`SfcAdapter.parse` 末尾（`return ParseResult(...)` 处）改为：

```python
        result = adapter.parse(synthetic)
        result.language = self.lang
        result.template_refs = _template_refs(_template_text(text))
        return result
```

`index.py` `_store_file` 在 `replace_file_imports` 后追加：

```python
        self.db.replace_file_template_refs(fid, getattr(result, "template_refs", None) or [])
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_frontend_template_refs.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add fastgraph/parsers/base.py fastgraph/parsers/frontend.py fastgraph/index.py tests/test_frontend_template_refs.py
git commit -m "feat(parsers): capture vue/svelte template identifier refs into template_refs"
```

---

### Task 4: unused_symbols 排除模板引用

**Files:**
- Modify: `fastgraph/graph.py:unused_symbols`
- Test: `tests/test_unused_symbols_vue.py`（新建）

**Interfaces:**
- Consumes: `template_refs` 表（Task 3 写入）

- [ ] **Step 1: 写失败测试** `tests/test_unused_symbols_vue.py`：

```python
"""Vue methods referenced only from the template are not flagged unused."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph import graph
from fastgraph.db import DB
from fastgraph.index import Indexer

WORK = Path(__file__).resolve().parent / "work_vueunused"
VUE = """\
<template><view @click="handleX">x</view></template>
<script>
export default {
  methods: {
    handleX() {},
    reallyDead() {},
  },
};
</script>
"""


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def test_template_bound_method_not_unused():
    _rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "comp.vue").write_text(VUE, encoding="utf-8")
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    names = {u["symbol"] for u in graph.unused_symbols(db, limit=50)}
    assert "handleX" not in names
    assert "reallyDead" in names
    db.close()
    _rmtree(WORK)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_unused_symbols_vue.py -q`
Expected: FAIL（`handleX` 被误报在 unused 列表里）

- [ ] **Step 3: 实现**，`graph.py` 的 `unused_symbols` 函数开头（`out` 声明前）追加：

```python
    tpl_refs = {
        (fid, name)
        for fid, name in db.conn.execute("SELECT file_id, name FROM template_refs")
    }
```

在循环内 `if sid in by_id: continue` 之后追加：

```python
        if (fid, name) in tpl_refs:
            continue
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_unused_symbols_vue.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add fastgraph/graph.py tests/test_unused_symbols_vue.py
git commit -m "fix(graph): exclude vue template-bound methods from unused_symbols"
```

---

### Task 5: 别名导入解析

**Files:**
- Modify: `fastgraph/index.py`（`_resolve_all` 增加别名步骤 + 两个 helper）
- Test: `tests/test_alias_imports.py`（新建）

**Interfaces:**
- Consumes: `graph.import_targets(db, import_text, import_file)`（已有）
- Produces: 别名绑定调用被 `apply_resolution` 解析到真实导出符号

- [ ] **Step 1: 写失败测试** `tests/test_alias_imports.py`：

```python
"""import { b as a } from './mod' → call a() resolves to mod.b."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph import graph
from fastgraph.db import DB
from fastgraph.index import Indexer

WORK = Path(__file__).resolve().parent / "work_alias"
MOD = "export function b() { return 1; }\n"
MAIN = """\
import { b as a } from './mod.js';
export function main() { return a(); }
"""


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def test_alias_call_resolves_to_export():
    _rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "mod.js").write_text(MOD, encoding="utf-8")
    (WORK / "main.js").write_text(MAIN, encoding="utf-8")
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    callers = graph.find_callers(db, "b", limit=10)
    files = {c["path"] for c in callers}
    assert "main.js" in files
    # 且调用边已解析：mod.b 的 by_id 入边存在（等价于 find_callers 非空）
    db.close()
    _rmtree(WORK)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_alias_imports.py -q`
Expected: FAIL（callers 为空）

- [ ] **Step 3: 实现**。`index.py` 在 `_resolve_all` 的 `elif len(candidates) > 1:` 分支结束后、`rtype == "inherits"` 分支前插入：

```python
            if len(picked) != 1 and "." not in target:
                alias = _resolve_via_alias(db, source_id, target, import_text_by_file, sym_files, caller_file)
                if alias is not None:
                    db.apply_resolution(rel_id, alias)
```

模块底部追加两个 helper：

```python
_ALIAS_NAMED_RE = re.compile(r"import\s*\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]")
_ALIAS_BARE_RE = re.compile(r"import\s+(\w+)\s+as\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"]")
_ALIAS_NS_RE = re.compile(r"import\s+\*\s+as\s+(\w+)\s+from\s*['\"]([^'\"]+)['\"]")


def _resolve_via_alias(db, source_id, target, import_text_by_file, sym_files, caller_file) -> int | None:
    """Resolve a bare call target through import aliasing in the caller's file.

    `import { logout as apiLogout } from '../api/admin'` + `apiLogout()` →
    the symbol `logout` in the resolved module. Returns a symbol id or None.
    """
    src_fid = caller_file.get(source_id)
    if src_fid is None:
        return None
    src_path = db.file_path(src_fid)
    if not src_path:
        return None
    imp_text = import_text_by_file.get(src_fid, "")
    if not imp_text:
        return None

    def resolve_export(mod_spec: str, export: str) -> int | None:
        for cand in graph.import_targets(db, f"import {{ {export} }} from '{mod_spec}'", src_path):
            rows = db.conn.execute(
                "SELECT id FROM symbols WHERE file_id = (SELECT id FROM files WHERE path = ?) AND name = ?",
                (cand, export),
            ).fetchall()
            if len(rows) == 1:
                return rows[0][0]
        return None

    for m in _ALIAS_NAMED_RE.finditer(imp_text):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            if " as " in part:
                orig, local = (p.strip() for p in part.split(" as ", 1))
            else:
                orig = local = part
            if local == target:
                hit = resolve_export(m.group(2), orig)
                if hit is not None:
                    return hit
    for m in _ALIAS_BARE_RE.finditer(imp_text):
        if m.group(2) == target:
            hit = resolve_export(m.group(3), m.group(1))
            if hit is not None:
                return hit
    for m in _ALIAS_NS_RE.finditer(imp_text):
        ns, mod_spec = m.group(1), m.group(2)
        if target.startswith(ns + "."):
            export = target[len(ns) + 1:]
            hit = resolve_export(mod_spec, export)
            if hit is not None:
                return hit
    return None
```

`index.py` 顶部已有 `from fastgraph import graph`？没有——需在 `_resolve_all` 内用 `graph.import_targets`，改为 `from fastgraph import graph` 放模块级 import（index.py 当前只 `from fastgraph.db import DB` 等，追加一行）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_alias_imports.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add fastgraph/index.py tests/test_alias_imports.py
git commit -m "fix(index): resolve calls through import aliasing (as bindings, star namespaces)"
```

---

### Task 6: 内容/中文搜索

**Files:**
- Modify: `fastgraph/index.py`（内容行提取 + `_store_file` 写入）
- Modify: `fastgraph/search.py`（`code_search` 第三层 + `_content_hits`）
- Test: `tests/test_content_search.py`（新建）

**Interfaces:**
- Consumes: `DB.replace_file_line_content(file_id, rows)`（Task 2）
- Produces: `code_search` 返回 `match: "content"` 的命中（含 `snippet ≤ 80`）

- [ ] **Step 1: 写失败测试** `tests/test_content_search.py`：

```python
"""code_search finds keywords that appear only in comments/strings."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph.db import DB
from fastgraph.index import Indexer
from fastgraph.search import code_search

WORK = Path(__file__).resolve().parent / "work_content"
PY = """\
# 限流：每秒最多 5 次
def fetch():
    return "https://example.com"
"""


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def test_chinese_comment_searchable():
    _rmtree(WORK)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "svc.py").write_text(PY, encoding="utf-8")
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    hits = code_search(db, "限流", limit=10)
    assert any(h["match"] == "content" and h["file"] == "svc.py" for h in hits)
    assert any("限流" in h.get("snippet", "") for h in hits)
    db.close()
    _rmtree(WORK)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_content_search.py -q`
Expected: FAIL（无 content 命中）

- [ ] **Step 3: 实现提取**。`index.py` 模块级追加：

```python
_LINE_COMMENT_MARK = {
    "python": "#",
    "typescript": "//", "tsx": "//", "javascript": "//",
    "java": "//", "cpp": "//",
    "go": "//", "rust": "//",
}
_BLOCK_COMMENT = re.compile(r"/\*[\s\S]*?\*/|'[\s\S]*?(?<!\\)(?:'''|\"\"\"|(?<!\\)\")") if False else None  # 占位，见下
```

（真实实现直接用下方函数，不用上面的 `_BLOCK_COMMENT` 占位行——实施时删除该行。）

```python
def _extract_content_lines(lang: str, text: str) -> list[tuple[int, str, str]]:
    """Collect (1-based line, kind, text) for content search.

    kinds: comment (line + block), string (incl. triple-quoted spans),
    template (vue/svelte non-script lines). Text capped at 200 chars.
    """
    out: list[tuple[int, str, str]] = []
    lines = text.split("\n")

    def add(ln: int, kind: str, content: str):
        c = content.strip().strip("\"'")
        if c and len(c) <= 200:
            out.append((ln, kind, c))

    # block comments: map every span line
    for m in re.finditer(r"/\*[\s\S]*?\*/", text):
        start = text.count("\n", 0, m.start())
        for i, sub in enumerate(m.group(0).split("\n")):
            add(start + i + 1, "comment", sub)
    # triple-quoted python strings
    for m in re.finditer(r'"""([\s\S]*?)"""|\'\'\'([\s\S]*?)\'\'\'', text):
        start = text.count("\n", 0, m.start())
        s = next((g for g in m.groups() if g is not None), "")
        for i, sub in enumerate(s.split("\n")):
            add(start + i + 1, "string", sub)
    # line comments + single-line strings
    for i, line in enumerate(lines, 1):
        stripped = line.lstrip()
        mark = _LINE_COMMENT_MARK.get(lang)
        if mark and stripped.startswith(mark):
            add(i, "comment", line[line.find(mark):])
        if lang in ("vue", "svelte") and stripped.startswith("<!--"):
            add(i, "comment", line)
        if lang == "python" and line.strip().startswith('"""'):
            continue  # span already captured above
    # string literals (single-line) — rough, skip quotes inside comments
    for m in re.finditer(r'"(?:[^"\\\n]|\\.)*"|\'(?:[^\'\\\n]|\\.)*\'', text):
        ln = text.count("\n", 0, m.start()) + 1
        add(ln, "string", m.group(0))
    # vue/svelte: template lines (script blocks blanked)
    if lang in ("vue", "svelte"):
        t = _SCRIPT_BLANK.sub(lambda m: "\n" * m.group(0).count("\n"), text)
        for i, line in enumerate(t.split("\n"), 1):
            s = line.strip()
            if s and not s.startswith("</") and not s.startswith("<") and not s.startswith("</template"):
                add(i, "template", s)
    return out
```

`index.py` 模块级追加 `_SCRIPT_BLANK`（与 frontend.py 同款正则，避免跨模块依赖）：

```python
_SCRIPT_BLANK = re.compile(r"<script\b[^>]*>[\s\S]*?</script\s*>", re.IGNORECASE)
```

`_store_file` 在 `replace_file_template_refs` 后追加：

```python
        self.db.replace_file_line_content(
            fid,
            _extract_content_lines(lang, source.decode("utf-8", "replace")),
        )
```

注意 `_store_file` 目前没有 `source` 参数——把 `_parse_only` 返回的 `source` 传入：改 `_store_file(rel, st, hash_, result)` 为 `_store_file(rel, st, hash_, result, source)`，调用处 `self._store_file(rel, st, hash_, result)` 同步传 `source`。`_parse_only` 已返回 `source`。

- [ ] **Step 4: 实现搜索第三层**。`search.py` 模块级追加：

```python
def _content_hits(db: DB, query: str, limit: int) -> list[dict]:
    """LIKE scan over stored comment/string/template lines (CJK-safe)."""
    tokens = [t for t in _tokens(query) if len(t) >= 2][:3]
    if not tokens:
        return []
    like = " AND ".join("lc.text LIKE ?" for _ in tokens)
    rows = db.conn.execute(
        f"""SELECT lc.line, lc.kind, lc.text, f.path
            FROM line_content lc JOIN files f ON f.id = lc.file_id
            WHERE {like} ORDER BY f.path, lc.line LIMIT ?""",
        [f"%{t}%" for t in tokens] + [limit],
    ).fetchall()
    return [
        {
            "symbol": "", "kind": r[1], "qualified_name": "", "signature": "",
            "line": r[0], "file": r[3], "match": "content", "snippet": r[2][:80],
        }
        for r in rows
    ]
```

`code_search` 末尾（`_import_hits` 分支前）追加：

```python
    if len(results) < limit and kind is None:
        results.extend(_content_hits(db, query, limit - len(results)))
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/test_content_search.py -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add fastgraph/index.py fastgraph/search.py tests/test_content_search.py
git commit -m "feat(search): index comments/strings/template lines, add content tier to code_search"
```

---

### Task 7: `@` 别名解析（file_deps）

**Files:**
- Modify: `fastgraph/graph.py`（`_alias_prefixes` + `import_targets` 别名替换）
- Test: `tests/test_alias_deps.py`（新建）

**Interfaces:**
- Consumes: `db.root`（DB 已持有）
- Produces: `import_targets` 对配置化别名（`@/`、tsconfig paths、vite alias）返回正确文件

- [ ] **Step 1: 写失败测试** `tests/test_alias_deps.py`：

```python
"""file_deps resolves @/ imports via jsconfig paths."""
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph.db import DB
from fastgraph.index import Indexer
from fastgraph.tools import Toolbox

WORK = Path(__file__).resolve().parent / "work_aliasdeps"
JSCONFIG = {"compilerOptions": {"paths": {"@/*": ["src/*"]}}}
MAIN = "import { fmt } from '@/utils/format';\nexport function main() { return fmt(); }\n"
UTIL = "export function fmt() { return 1; }\n"


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def test_alias_import_resolves():
    _rmtree(WORK)
    (WORK / "src/utils").mkdir(parents=True, exist_ok=True)
    (WORK / "jsconfig.json").write_text(json.dumps(JSCONFIG), encoding="utf-8")
    (WORK / "src" / "main.js").write_text(MAIN, encoding="utf-8")
    (WORK / "src/utils/format.js").write_text(UTIL, encoding="utf-8")
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    tb = Toolbox(WORK, db, Indexer(WORK, db))
    deps = tb.file_deps("src/main.js")
    assert deps["found"] is True
    resolved = [imp for imp in deps.get("imports", [])]
    assert any("src/utils/format.js" in t for imp in resolved for t in [imp["text"]] for imp in [imp])
    assert any(r for r in resolved if r["resolves_to"] and "src/utils/format.js" in r["resolves_to"][0])
    db.close()
    _rmtree(WORK)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_alias_deps.py -q`
Expected: FAIL（import 落在 external_imports，resolves_to 为空）

- [ ] **Step 3: 实现**。`graph.py` 顶部加 `import json`，模块级追加缓存与读取函数：

```python
_alias_cache: dict[Path, dict[str, str]] = {}


def _alias_prefixes(db: DB) -> dict[str, str]:
    """Alias prefix → root-relative dir (from tsconfig/jsconfig paths, vite
    resolve.alias, or the uni-app `@` = project-root convention). Cached per
    project root; never guesses when no config exists."""
    root = db.root
    if root in _alias_cache:
        return _alias_cache[root]
    m: dict[str, str] = {}
    for cfg_name in ("jsconfig.json", "tsconfig.json"):
        cfg = root / cfg_name
        if not cfg.is_file():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        paths = (data.get("compilerOptions") or {}).get("paths") or {}
        for key, targets in paths.items():
            if not targets or not isinstance(targets, list):
                continue
            alias = key.split("/*")[0].rstrip("*")
            tgt = str(targets[0]).split("/*")[0].rstrip("*")
            if alias and tgt:
                m[alias] = tgt.strip("./")
    for vname in ("vite.config.js", "vite.config.ts", "vite.config.mjs"):
        vcfg = root / vname
        if not vcfg.is_file():
            continue
        txt = vcfg.read_text(encoding="utf-8", errors="replace")
        for mm in re.finditer(r"alias\s*:\s*\{([\s\S]*?)\}", txt):
            for am in re.finditer(
                r"['\"]([@\w/-]+)['\"]\s*:\s*['\"]?([^'\"\s,}]+)", mm.group(1)
            ):
                m[am.group(1)] = am.group(2).strip("'\"")
    if not any(k == "@" for k in m) and (root / "pages.json").is_file():
        m["@"] = ""  # uni-app: @ → project root
    _alias_cache[root] = m
    return m
```

`import_targets` 在 `m = _IMP_RE.search(import_text)` 成功后、`mod` 的 normalize 逻辑之前插入别名替换：

```python
    for alias, tgt in sorted(_alias_prefixes(db).items(), key=lambda kv: -len(kv[0])):
        if mod == alias or mod.startswith(alias + "/"):
            rest = mod[len(alias):].lstrip("/")
            mod = f"{tgt}/{rest}" if tgt else rest
            break
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_alias_deps.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add fastgraph/graph.py tests/test_alias_deps.py
git commit -m "feat(graph): resolve configured import aliases (@/, tsconfig paths, vite alias) in file_deps"
```

---

### Task 8: 类级 callers/callees

**Files:**
- Modify: `fastgraph/graph.py`（`_expand_container_roots` + 4 个查询函数接入）
- Test: `tests/test_class_level_graph.py`（新建）

**Interfaces:**
- Produces: `find_callers("Class")` / `find_callees("Class")` 返回成员级聚合结果

- [ ] **Step 1: 写失败测试** `tests/test_class_level_graph.py`：

```python
"""Class-level queries aggregate member call edges."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastgraph import graph
from fastgraph.db import DB
from fastgraph.index import Indexer

WORK = Path(__file__).resolve().parent / "work_classlevel"
SVC = """\
package app;
public class Greeter {
    public String greet() { return "hi"; }
}
"""
CTRL = """\
package app;
public class HomeController {
    private final Greeter greeter;
    public HomeController(Greeter g) { this.greeter = g; }
    public String home() { return greeter.greet(); }
}
"""


def _rmtree(path: Path):
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def test_class_level_callers():
    _rmtree(WORK)
    (WORK / "app").mkdir(parents=True, exist_ok=True)
    (WORK / "app/Greeter.java").write_text(SVC, encoding="utf-8")
    (WORK / "app/HomeController.java").write_text(CTRL, encoding="utf-8")
    db = DB(WORK)
    Indexer(WORK, db).force_index()
    callers = graph.find_callers(db, "Greeter", limit=10)
    files = {c["path"] for c in callers}
    assert any("HomeController.java" in f for f in files)
    callees = graph.find_callees(db, "HomeController", limit=10)
    callee_names = {c["name"] for c in callees}
    assert "greet" in callee_names
    db.close()
    _rmtree(WORK)
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_class_level_graph.py -q`
Expected: FAIL（callers/callees 为空）

- [ ] **Step 3: 实现**。`graph.py` 模块级追加：

```python
_CONTAINER_KINDS = {"class", "interface", "struct", "enum", "impl"}


def _expand_container_roots(db: DB, rows: list[dict]) -> tuple[set[int], set[int]]:
    """Return (root_ids, member_ids): for container roots, member symbols are
    added to the root set so class-level queries aggregate member edges."""
    root_ids: set[int] = set()
    member_ids: set[int] = set()
    for r in rows:
        root_ids.add(r["id"])
        if r.get("kind") not in _CONTAINER_KINDS:
            continue
        qname = r.get("qualified_name") or ""
        if not qname:
            continue
        for (mid,) in db.conn.execute(
            "SELECT id FROM symbols WHERE qualified_name LIKE ?", (qname + ".%",)
        ):
            if mid != r["id"]:
                member_ids.add(mid)
    return root_ids | member_ids, member_ids
```

`find_callers` 改为：

```python
    roots = find_symbols(db, name)
    if not roots:
        return []
    root_ids, member_ids = _expand_container_roots(db, roots)
    seen: set[int] = set()
    frontier = list(root_ids)
    level = 0
    while frontier and level < depth:
        nxt: list[int] = []
        for sid in frontier:
            for c in _callers_with_class(db, sid):
                if c not in seen:
                    seen.add(c)
                    nxt.append(c)
        frontier = nxt
        level += 1
    out = [symbol_by_id(db, s) for s in seen if s not in root_ids and s not in member_ids]
    return [o for o in out if o][:limit]
```

`find_callees` 同理：

```python
    roots = find_symbols(db, name)
    if not roots:
        return []
    root_ids, member_ids = _expand_container_roots(db, roots)
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
    out = [symbol_by_id(db, s) for s in seen if s not in member_ids]
    return [o for o in out if o][:limit]
```

`impact_analysis` 的 `root_ids = {r["id"] for r in roots}` 改为：

```python
    root_ids, member_ids = _expand_container_roots(db, roots)
```

且 BFS 内 `if c in seen or c in root_ids:` 后追加 `or c in member_ids`，buckets 只统计外部影响面。

`path_between` 的成员展开段替换为：

```python
    root_ids, member_ids = _expand_container_roots(db, src)
    src_ids = root_ids
```

（删除原 `member_ids = {...LIKE...}` 块）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_class_level_graph.py -q`
Expected: PASS

- [ ] **Step 5: 全量回归**

Run: `python -m pytest tests/ -q`
Expected: PASS（新增约 10 个测试全绿）

- [ ] **Step 6: 提交**

```bash
git add fastgraph/graph.py tests/test_class_level_graph.py
git commit -m "feat(graph): class-level callers/callees/impact/path aggregate member edges"
```

---

### Task 9: README 同步 + 全量回归 + 游迹实测

**Files:**
- Modify: `README.md`（工具数 13 → 17、工具速查表补 4 个分析工具、路线图勾选）

**Interfaces:**
- Consumes: 全部前面任务的最终行为

- [ ] **Step 1: README 更新**
  - "工具速查（13 个）" → "（17 个）"，表格补 4 行：`unused_symbols` / `hot_symbols` / `file_metrics` / `module_cycles`
  - "仅 13 个工具" 统计处 → "仅 17 个工具"
  - 路线图补勾选：`- [x] Phase 3.5：unused_symbols / hot_symbols / file_metrics / module_cycles 分析工具`
  - 服务器 intro 文案（server.py 已同步 17，README 对应）
- [ ] **Step 2: 全量 pytest**

Run: `python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 3: 游迹项目实测回归**（用真实项目验证修复效果）

Run:
```bash
cd "c:\Users\ouli\Desktop\project\游迹 - AI智能旅游规划与分享平台"
python -m pytest --version  # 环境确认
# 然后通过 FastGraph API 验证：
python - <<'PY'
PY
```
用 Python REPL 脚本验证 4 个点（脚本路径 `scripts/verify_fixes.py` 删除后改用一次性内联验证）：
1. `graph.unused_symbols(db)`：`handleLogin`、`logout`、`handleSubmit` 等模板/别名绑定函数不在列表
2. `search.code_search(db, "限流")`：有 `match == "content"` 命中
3. `Toolbox.file_deps("travel-miniapp/utils/plan.js")`：`@/utils/format` import 的 `resolves_to` 非空
4. `graph.find_callers(db, "TravelNoteService")`：count > 0

验证后删除目标项目 `.fastgraph/index.sqlite` 重建一次确认升级路径无异常。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "docs: sync tool count 13->17, list analysis tools in quickstart"
```

---

## Self-Review

**1. Spec coverage（对照设计文档）：**
- 仓库清理 → Task 1 ✅
- e2e 迁移（环境变量 skip）→ Task 1 ✅
- README 13→17 → Task 9 ✅
- INDEX_VERSION 4 → Task 2 ✅
- template_refs 表 + SfcAdapter 提取 → Task 2/3 ✅
- unused_symbols 排除模板引用 → Task 4 ✅
- 别名导入解析 → Task 5 ✅
- line_content 表 + code_search 第三层（snippet ≤80）→ Task 2/6 ✅
- `_alias_prefixes`（tsconfig/vite/uni-app）→ Task 7 ✅
- `_expand_container_roots` + 4 查询函数 → Task 8 ✅
- 5 个新测试 → Task 3/4/5/6/7/8（共 6 个测试文件）✅
- 全量回归 + 游迹实测 → Task 9 ✅

**2. Placeholder scan：** 无 TBD/TODO；每个实现步骤带具体代码。Task 6 中一处 `_BLOCK_COMMENT` 占位行已注明实施时删除。

**3. Type consistency：**
- `DB.replace_file_template_refs(file_id, names: list[str])` / `replace_file_line_content(file_id, rows: list[tuple[int,str,str]])` 在 Task 2 定义，Task 3/6 调用 ✅
- `ParseResult.template_refs: list[str]` 在 Task 3 定义，`index._store_file` 用 `getattr(result, "template_refs", None) or []` 兼容 ✅
- `_expand_container_roots -> tuple[set[int], set[int]]` 在 Task 8 定义并统一使用 ✅
- `_store_file` 签名增加 `source` 参数：Task 6 内同步更新调用点 ✅
- `_resolve_via_alias(db, source_id, target, import_text_by_file, sym_files, caller_file)` 在 Task 5 定义，`_resolve_all` 内调用 ✅