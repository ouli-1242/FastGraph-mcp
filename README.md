# FastGraph-MCP

> Lightweight code intelligence MCP for coding agents.
> AST + Incremental Index + Code Graph, zero embeddings, low memory, low context.

FastGraph-MCP 是一个轻量级代码智能 MCP 服务器，补充 Serena 等编辑型 MCP 所缺的**项目级检索、调用图、影响分析与变更感知**。设计定位是"高速代码导航层"：

- **快**：首次索引一个中型项目数秒~十几秒；查询 <10ms；**增量更新只重解析改动文件**，编辑保存后无需全量重建
- **轻**：无 embedding 模型、无图数据库、无 LSP，常驻内存 ~3MB（峰值 <50MB）
- **省 Context**：所有工具只返回 `file / symbol / line / relation`，绝不返回文件正文
- **补 Serena**：全局符号搜索、call graph、impact_analysis、git diff 变更意识、项目架构概览

## 安装

仓库即项目根，无需进入子目录：

```bash
cd /path/to/FastGraph-mcp
python -m pip install -e .        # 或 python -m pip install .
```

也可以不安装，直接 `python -m fastgraph`（需要仓库在 PYTHONPATH 或先 `pip install -e .`）。依赖：`mcp`、`tree-sitter` + 各语言 grammar。

## 使用

### 首次索引与增量

- 首次运行 `project_overview` / 任意工具：自动扫描项目、解析符号与调用关系（>2MB 文件跳过，Node 黑名单目录排除）
- 之后每次工具调用前做**懒增量刷新**：mtime+size 比对，只重解析真正变化的文件
- 索引存放在项目下 `.fastgraph/index.sqlite`（自动忽略，不污染 git）

### Claude Code

`cwd` 填克隆本仓库的路径：

```json
{
  "mcpServers": {
    "fastgraph": {
      "command": "python",
      "args": ["-m", "fastgraph", "--root", "/path/to/project"],
      "cwd": "/path/to/fastgraph-repo"
    }
  }
}
```

### OpenCode

```json
{
  "mcp": {
    "fastgraph": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "fastgraph", "--root", "/path/to/project"],
      "cwd": "/path/to/fastgraph-repo"
    }
  }
}
```

## MCP 工具（12 个）

| 工具 | 作用 | 输出 |
| --- | --- | --- |
| `code_search(query, kind?, limit)` | 自然语言/关键词找代码（精确名 > FTS 前缀 > doc） | symbol / file / line / signature |
| `symbol_info(symbol)` | 定位符号（支持 `A.B.foo`） | 文件/行/签名/doc/callee 摘要 |
| `find_callers(symbol, depth?)` | 谁调用它（BFS 可传递） | caller 列表 |
| `find_callees(symbol, depth?)` | 它调用谁 | callee 列表 |
| `trace_path(from, to?)` | 调用链（缺省返回向上链条） | 路径符号列表 |
| `impact_analysis(symbol, max_depth?)` | **核心**：反向 BFS 影响面，HIGH/MEDIUM 分级，测试文件单列 | 风险分组 |
| `changed_context()` | **Git 感知**：diff → 变更符号 → 受调用者 | 变更快照 + 影响 |
| `project_overview()` | 项目语言/文件/符号统计 + 顶层布局 + 解析失败文件 | 概览 |
| `file_symbols(path)` | 单文件全部符号（行号区间/kind/签名），不读正文即可理解文件 | symbol 列表 |
| `file_deps(path)` | **文件级依赖**：import 了什么、被谁 import | 依赖导出/导入面 |
| `rename_impact(symbol)` | **改名预览**：所有定义点 + 所有引用点（含 unresolved 裸名调用） | 定义/引用清单 |
| `type_hierarchy(symbol)` | **继承层级**：祖先类 + 子类（BFS） | 层级列表 |

## 与 Serena 分工

```
AI Agent
   |
FastGraph ── 项目理解：代码在哪里、关系的、影响范围
Serena    ── 代码操作：读代码、改代码、重构、diagnostics
```

FastGraph 不实现 LSP / rename / edit / refactor（那是 Serena 的职责）；Serena 没有的**全局检索、call graph、影响分析、git 变更感知**由本工具补充。跟踪其他同类工具定位它不是 RAG：

- 无 embedding、无 vector store（对比 CocoIndex/Vera：不跑模型）
- 无图数据库、无 docker 服务（对比 CodeGraphContext 的 docker-compose）
- 仅 12 个工具、输出极小（对比 CodeGraph 45 个工具 + 大输出）
- 增量秒级，无全量重索引（对比常见 RAG 的更新成本）

## 与 Serena 的编排

```
改名前    rename_impact(symbol)   → 预览影响面，再调 Serena rename_symbol
理解文件  file_symbols(path)      → 不读正文先看结构
文件依赖  file_deps(path)         → 改的是 API 还是内部实现
继承关系  type_hierarchy(symbol)  → 改基类前的族谱风险
复查      changed_context()       → 改完看 git diff 波及
```

## 支持语言

Python、TypeScript/TSX、JavaScript、**Vue (`.vue`)、Svelte (`.svelte`)**、Go、Rust、Java、C/C++（`.c/.h/.cpp/.cc/.hpp`）

Vue/Svelte 通过提取 `<script>` 块解析（支持 `lang="ts"`），符号行号对齐原始 `.vue/.svelte` 文件；JSX/TSX 直接支持。

## 开发

```bash
pip install -e ".[dev]"   # pytest
python -m pytest tests/
```

测试覆盖：索引、搜索、callers/callees、trace、impact、增量更新、git 变更感知、Vue/Svelte 前端组件。

## 路线图

- [x] Phase 1：MCP Server + tree-sitter + SQLite + `code_search`/`project_overview`
- [x] Phase 2：call graph（calls/imports/inherits）+ `find_callers/find_callees/trace_path`
- [x] Phase 3：`impact_analysis` + `changed_context`（git diff 集成）
- [ ] Phase 4：可选 BM25/embedding 语义搜索（--no-embed 模式默认关闭）
- [ ] Phase 5：多项目 workspace 支持