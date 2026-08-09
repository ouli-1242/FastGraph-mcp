# FastGraph-MCP

> Lightweight code intelligence MCP for coding agents.
> AST + Incremental Index + Code Graph, zero embeddings, low memory, low context.

FastGraph-MCP 是一个轻量级代码智能 MCP 服务器，补充 Serena 等编辑型 MCP 所缺的**项目级检索、调用图、影响分析与变更感知**。设计定位是"高速代码导航层"：

- **快**：首次索引中型项目数秒~十几秒（实测 525 文件 17.8s）；典型查询 <100ms；**增量更新只重解析改动文件**，编辑保存后无需全量重建
- **轻**：无 embedding 模型、无图数据库、无 LSP；常驻内存 <50MB（实测 525 文件峰值 43MB）
- **省 Context**：所有工具只返回 `file / symbol / line / relation`，绝不返回文件正文
- **补 Serena**：全局符号搜索、call graph、impact_analysis、git diff 变更意识、项目架构概览

## Agent 决策表（用哪个工具）

| 场景 | 用法 |
| --- | --- |
| 陌生代码库，先了解全局 | `project_overview`（语言/顶层布局/入口/依赖方向/解析失败文件） |
| 不知道某段代码在哪 | `code_search` |
| 想知道一个符号是什么 | `symbol_info` |
| 准备读某个文件 | `file_symbols`（先看结构再决定要不要全文读） |
| 谁在调用/它调用谁 | `find_callers` / `find_callees` |
| 两个符号之间有无调用链 | `trace_path` |
| **准备修改前**：影响面 + 风险 | `impact_analysis` + `rename_impact`（风险分级/改名预览） |
| 文件级依赖、改 import 影响 | `file_deps` |
| 继承关系 | `type_hierarchy` |
| 刚改完代码 | `changed_context`（git diff + 受影响调用者） |
| 读/改正文 | 交给 Serena（FastGraph 不碰文件内容） |

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

## MCP 配置

### 参数说明（先读这条）

```
--root <path>  要【被索引】的项目目录。FastGraph 扫描并分析的是这里的代码。
cwd            FastGraph 仓库自身的目录（保证 `import fastgraph` 能成功）。
```

⚠️ 两个路径**完全不同**：`--root` 指向你要分析的项目，`cwd` 指向 FastGraph 本身。把两者填成同一个目录是最常见的错误（结果：FastGraph 把自己的源码当成索引对象）。

约定：

| 项 | 值 | 说明 |
| --- | --- | --- |
| `command` / `args` | `python -m fastgraph --root <项目路径>` | FastGraph 已安装（`pip install -e .`）时，任何目录都可运行 |
| `cwd` | 本仓库路径 | 未安装时兜底：在该目录下可 `import fastgraph`。已安装可省略 |
| `--root` 缺省 | 默认 `Path.cwd()` | 不传时用 cwd 作为索引对象——**不要依赖这个默认值**，永远显式传 `--root` |
| 每项目一个实例 | 一次只能挂载一个 `--root` | 需要分析多个项目，就复制整段配置为多个 MCP server（每个指定不同项目名与 `--root`） |

### Windows 路径写法

JSON 里反斜杠必须转义（`D:\\work\\my-project`），手写容易错。**推荐写成正斜杠，Windows 完全兼容**：

```json
"args": ["-m", "fastgraph", "--root", "D:/work/my-project"]
```

### 安装（可选，推荐）

```bash
cd /path/to/FastGraph-mcp
python -m pip install -e .        # 目的：命令行（含 MCP 配置中的 command）不必依赖 cwd
```

- 安装后 `python -m fastgraph` 在任何目录可用，MCP 配置里 `cwd` 可不写
- 用虚拟环境时，把 `command` 换成 venv 的 python 绝对路径（如 `D:/tools/FastGraph-mcp/.venv/Scripts/python.exe`），避免全局 Python 找不到依赖
- Windows 注意：**不要用微软商店的 `python` 别名**（py 启动器），MCP 配置里的 `command` 用 `python` 时要确认它是真实解释器（`where python` 验证）
- 本仓库已按 editable 方式安装（`pip install -e .`）：`python -m fastgraph` 指向本仓库源码，**改代码即时生效，无需重装**

### Claude Code

`~/.claude.json` 或项目的 `.mcp.json`：

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

Windows：

```json
{
  "mcpServers": {
    "fastgraph": {
      "command": "python",
      "args": ["-m", "fastgraph", "--root", "D:/work/my-project"],
      "cwd": "D:/tools/FastGraph-mcp"
    }
  }
}
```

### OpenCode

`opencode.json`：

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

### 其他客户端（通用 stdio 规则）

上方范例是 Claude Code / OpenCode 的字段命名；其余客户端（Cursor、Zed、VS Code 的 MCP 插件等）同样遵循：

- MCP stdio server 的 `command` + `args` 就是 `python -m fastgraph --root <项目>`，外加可选 `cwd`
- 客户端要求 `cwd` 时必须填 FastGraph 仓库路径；不要求时可不填（前提：已 `pip install -e .`）
- 配置后建议跑一次任意工具（如 `project_overview`）确认 stdout 是 MCP 协议而非报错

### 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 启动即失败 / 工具不可见 | 未安装或 `cwd` 错误导致 import 失败 | 先在仓库目录跑 `python -m fastgraph --root <项目>` 验证；报错多半是缺 `mcp`/`tree-sitter` 依赖 |
| 索引了整个 FastGraph 仓库自身 | cwd 缺省被当作 root | 显式传 `--root 目标项目路径` |
| 改了代码但查询结果旧 | 增量刷新未触发（如文件被 git 操作替换） | 删除目标项目下 `.fastgraph/index.sqlite`，下次调用自动重建 |
| 首个工具调用慢（数秒~十几秒） | 首次全量索引 | 正常；之后增量 <100ms |

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
| `project_overview()` | 项目统计 + 顶层布局 + **入口文件 + 依赖方向** + 解析失败文件 | 概览 |
| `file_symbols(path)` | 单文件全部符号（行号区间/kind/签名），不读正文即可理解文件 | symbol 列表 |
| `file_deps(path)` | **文件级依赖**：import 了什么、被谁 import | 依赖导出/导入面 |
| `rename_impact(symbol)` | **变更风险**：定义/引用清单 + HIGH/MEDIUM/LOW 分级（公开 API、测试占用数） | 定义/引用 + risk |
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
改名前    rename_impact(symbol)   → 风险分级（公开 API/测试面），再调 Serena rename_symbol
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