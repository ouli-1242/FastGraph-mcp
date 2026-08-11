# FastGraph-MCP 实测缺陷修复与仓库整理 — 设计文档

日期：2026-08-11
状态：已获用户批准（方案 A，三点决策已确认）

## 背景

在真实项目（游迹：Java + Vue + JS 混合，224 文件）上实测 FastGraph-MCP，发现 4 个缺陷，全部可复现：

1. `unused_symbols` 误报率高：Vue 模板绑定函数（如 `handleLogin`）、别名导入函数（`import { logout as apiLogout }` 的 `logout`）被误判为未使用。
2. `code_search` 搜不到代码内容/注释中的词（中文关键词如"限流"返回空）。
3. `file_deps` 解析不了 `@/` 别名导入（`@/utils/format.js` 指向真实存在的文件但 `resolves_to` 为空）。
4. 类级符号的 `find_callers`/`find_callees` 返回空（必须用 `Class.method` 才查得到）。

仓库同时存在 12 个根目录未跟踪调试脚本和 505 行未提交改动（已 review，保留）。

## 用户决策

- 方案：A（4 项修复 + 仓库清理，不做统一 reference 表重构）
- 内容搜索命中返回截断行文本（≤80 字符），`match: "content"`
- `e2e_mcp_test.py` 迁入 `tests/test_e2e_mcp.py`，读 `FASTGRAPH_TEST_ROOT` 环境变量，未设置则 `pytest.skip`
- 未提交 505 行改动：已逐文件 review 完毕（质量合格，含修复并发 bug 的 db.py、4 个新工具等），保留并随本次工作提交
- 定位：FastGraph 管"大概"，Serena 管"仔细"；所有改动保持输出精简、低内存、零 embedding

## 设计

### 1. 仓库清理

- 删除根目录 11 个调试脚本：`dbg_add.py`、`dbg_add2.py`、`dbg_add3.py`、`dbg_callers.py`、`dbg_fts.py`、`dbg_plausible.py`、`dbg_resolve.py`、`dbg_sanity.py`、`repro_cycles.py`、`repro_min.py`、`verify_fixes.py`
- `e2e_mcp_test.py` → `tests/test_e2e_mcp.py`：ROOT 改为 `FASTGRAPH_TEST_ROOT` 环境变量，未设置时 `pytest.skip`
- `git add` 三个未跟踪测试：`test_analysis_tools.py`、`test_gitutil.py`、`test_java_callers.py`
- README 工具数 13 → 17 修正（工具速查表补充 4 个分析工具）
- `INDEX_VERSION` 3 → 4

### 2. Vue/Svelte 模板引用（unused_symbols 误报 1/2）

新表：

```sql
CREATE TABLE IF NOT EXISTS template_refs (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name    TEXT NOT NULL,
    PRIMARY KEY (file_id, name)
);
```

- `parsers/frontend.py` 的 `SfcAdapter`：提取模板块（非 script 部分）中的标识符引用——`{{ expr }}`、`@event="expr"` / `v-on:event`、`:prop="expr"` / `v-bind:prop`、`v-if/v-show/v-for/v-model/v-html/v-text` 指令值；从表达式正则提取标识符，排除字面量
- `ParseResult` 增加 `template_refs: list[str]` 字段（默认为空，其他解析器不受影响）
- `index.py: _store_file` 按文件替换写入 `template_refs`
- `graph.py: unused_symbols`：跳过 `(file_id, name)` 命中 `template_refs` 的方法/函数

### 3. 别名导入解析（unused_symbols 误报 2/2）

- `index.py: _resolve_all` 增加别名映射步骤：对未解析的裸名 calls target，在调用方文件的 `file_imports` 中解析 `import { a as b } from 'mod'` / `import x as y` / `import * as ns`，构建 `本地名 → (模块路径, 导出名)` 映射
- 用 `import_targets` 把模块路径定位到文件，查该文件内导出名符号，唯一则 `apply_resolution`
- 复用 `_resolve_all` 已构建的 `import_text_by_file`、`sym_files`、`caller_file`

### 4. 内容/中文搜索

新表：

```sql
CREATE TABLE IF NOT EXISTS line_content (
    id      INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    line    INTEGER NOT NULL,
    kind    TEXT NOT NULL,          -- comment | string | template
    text    TEXT NOT NULL           -- 截断 200 字符
);
CREATE INDEX IF NOT EXISTS idx_lc_file ON line_content(file_id);
```

- `index.py` 新增按语言族提取注释/字符串的正则（Python `#`/三引号；JS/TS/Java/C/C++/Go/Rust `//`、`/* */`、字符串字面量；`.vue/.svelte` 额外取模板文本）
- `code_search` 第三层：符号命中不足时 LIKE 扫描 `line_content`（`%token%`，天然支持中文），返回 `match: "content"` + `file/line/kind` + 截断 80 字符文本

### 5. `file_deps` 的 `@` 别名

- `graph.py` 新增 `_alias_prefixes(db)`（模块级缓存）：解析目标项目 `tsconfig.json`/`jsconfig.json` 的 `compilerOptions.paths`、`vite.config.(js|ts|mjs)` 的 `resolve.alias`（正则粗解析）；存在 `pages.json` 时按 uni-app 约定 `@` → 项目根
- `import_targets` 匹配前先做别名前缀替换；无配置且非 uni-app 不替换

### 6. 类级 callers/callees

- `graph.py` 新增 `_expand_container_roots(db, rows) -> (root_ids, member_ids)`：根符号 kind ∈ {class, interface, struct, enum, impl} 时，把 `qualified_name LIKE 'qname.%'` 的成员 id 并入根集
- `find_callers`/`find_callees`/`impact_analysis`/`path_between` 统一使用；输出过滤 `member_ids`（类内部自调用是噪音）
- `find_callees(class)`：各成员 callees 的并集，去重、排除类内成员

### 7. 测试

新增 5 个测试（fixture 用临时目录，模式与 `test_analysis_tools.py` 一致）：

1. Vue 模板引用不被误报（.vue fixture：`@click="handleX"` + `function handleX`）
2. 别名导入解析（JS fixture：`import { b as a } from './mod'`，`a()` 解析到 `mod` 的 `b`）
3. 中文注释搜索（Python fixture 带中文注释，搜中文词命中）
4. `@` 别名 file_deps（带 `jsconfig.json` paths 的 fixture）
5. 类级 `find_callers`（Java fixture：`Class.method` 被外部实例调用，查 `Class` 有结果）

## 验证

- 全量 pytest（现有 86 + 新增）
- 在游迹项目回归 4 个实测点：`handleLogin`/`logout` 不再误报、搜"限流"有内容命中、`file_deps` 解析 `@/`、`find_callers("AdminController")` 有结果

## 明确不做（YAGNI）

- 不做统一 reference 表重构（方案 C）
- 内容搜索不做 FTS5 分词（LIKE 足够快且中文正确）
- 别名解析不读 webpack config（覆盖率低）