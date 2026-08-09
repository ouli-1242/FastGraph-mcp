---
name: fastgraph
description: >
  FastGraph code navigation companion. Use when working inside a code project
  served by the FastGraph MCP server: locating symbols across the project,
  understanding files without reading them, call-graph traversal, impact /
  rename risk analysis before edits, inheritance exploration, and post-edit
  changed-context review. Guides WHEN to call which FastGraph tool instead of
  grepping or reading whole files. Trigger proactively whenever the user is
  in a project backed by FastGraph and wants to find, navigate, or safely
  change code.
---

# FastGraph: 先导航，再操作

FastGraph 是**导航层**（项目级检索 / 调用图 / 影响分析），Serena 是**操作层**（读 / 写 / 重构）。原则：**改代码之前先问 FastGraph，拿到精确影响面再动手**。

## 工具裁决（何时用哪个）

| 场景 | 首选工具 | 不要 |
| --- | --- | --- |
| 陌生代码库概览 | `project_overview()` | grep 找入口 |
| 找某段代码/符号 | `code_search(query)` | 全项目 grep |
| 符号详情（签名/doc） | `file_symbols(path)` 先看结构 | 直接 `read_file` 全文 |
| 谁调用它 / 它调用谁 | `find_callers` / `find_callees` | LSP 单文件引用 |
| 调用链 | `trace_path(from, to)` | 手工 BFS |
| **改之前**：影响面 | `impact_analysis(symbol)` | 猜测 |
| **改之前**：改名风险 | `rename_impact(symbol)` → risk.grade | 直接 rename |
| 继承 / 基类改动 | `type_hierarchy(symbol)` | 忽略子类后果 |
| 文件模块依赖 | `file_deps(path)` | 猜测 import |
| 刚改完 | `changed_context()` → 校验 | 跳过验证 |
| 读正文 / 写代码 | **交给 Serena** | — |

## 强制工作流

### 修改前（必做）

1. `impact_analysis(target)` 得到 HIGH/MEDIUM + 测试文件
2. 若改 API/类：`type_hierarchy` + `rename_impact` 分级
3. 涉及跨文件引用：`module_deps(path)` 定位受影响文件
4. 只读正文确认细节（Serena `read_file` 精确行区间），**不要整文件**

### 修改后（必做）

1. `changed_context()`——确认 diff 符号与影响面
2. 与修改前覆盖影响面（callers/callees/tests）对比，检查遗漏

### 上下文纪律

- 全部 FastGraph 产品**只输 file/symbol/line**，不返回正文
- 大列表先看到 10-20 条；关注点再细查
- 绝不为了"看代码"调 `code_search`；它是检索不是阅读器