# Phase 1: 入口扫描

> 基于源码注解扫描识别入口方法，再通过 graph.db 对齐节点 ID。

**执行约束：Phase 1 由 LLM 直接读取源码执行扫描，禁止生成临时脚本替代。** LLM 需要理解每个类的注解结构和方法对应关系（如一个类多个 @RmbTopic 方法），这是正则脚本无法可靠处理的场景。

## 概述

Phase 1 通过源码 Grep 扫描识别入口方法。按 `rules/entry-rules.md` 定义的规则逐条执行，汇总去重后生成入口列表。

## 前置条件

- 已知项目源码目录路径（`projectPath`）
- 已加载 `rules/entry-rules.md` 配置文件

## 执行步骤

### 步骤 1: 源码 Grep 扫描

读取 `rules/entry-rules.md`，按其中定义的三类入口规则（Controller / RMB / Job）执行源码扫描。每类入口可能包含多条规则，按 entry-rules.md 中的规则编号逐条执行 Grep，汇总所有匹配结果。

扫描前先应用 entry-rules.md 顶部的噪音排除规则过滤无效文件。

**执行要点**：
- 每条规则的 Grep 搜索模式和验证方法以 entry-rules.md 中的定义为准，本文件不重复
- 多条规则匹配到同一入口方法时，按优先匹配的规则归类，不重复计数
- 同一类中多个入口方法各自独立编号。适用于所有入口类型：Controller 的多个 Mapping 方法、RMB 的多个 @RmbTopic 方法、Job 的多个 @Scheduled 方法

### 步骤 2: 分类编号

- Controller: `controller-001`, `controller-002`, ...
- RMB Handler: `rmb-001`, `rmb-002`, ...
- Job: `job-001`, `job-002`, ...

### 步骤 3: 写入 entries.json

将入口列表写入 `flow-trace-db/.trace-cache/phase1/entries.json`。

**entries.json 格式**：

```json
{
  "version": "2.0",
  "generator": "flow-trace-db",
  "entries": [
    {
      "id": "controller-001",
      "type": "controller",
      "className": "SzCourtFetchResultController",
      "methodName": "fetchQueryResult",
      "filePath": "cbrc-pre-linux/src/main/java/.../SzCourtFetchResultController.java",
      "httpMapping": "PostMapping(/api/autobank/getAccountInfoQryStatus)"
    },
    {
      "id": "rmb-001",
      "type": "rmb",
      "className": "AccountAsideQueryHandler",
      "methodName": "holdCheck",
      "filePath": "cbrc-bs-jrp/src/main/java/.../AccountAsideQueryHandler.java"
    },
    {
      "id": "job-001",
      "type": "job",
      "className": "ResponseReconJob",
      "methodName": "doJob",
      "filePath": "ccp-rcn/src/main/java/.../ResponseReconJob.java",
      "jobType": "CronQuartzJob"
    }
  ],
  "summary": {
    "controller": 6,
    "rmb": 133,
    "job": 38,
    "total": 177
  }
}
```

**字段说明**：

| 字段 | 必填 | 说明 |
|------|------|------|
| id | 是 | 分类编号：controller-NNN / rmb-NNN / job-NNN |
| type | 是 | 入口类型：controller / rmb / job |
| className | 是 | 类名 |
| methodName | 是 | 方法名 |
| filePath | 是 | 相对于项目根目录的源码路径 |
| httpMapping | 否 | controller 类型特有：HTTP 方法和路径 |
| jobType | 否 | job 类型特有：Job 框架类型 |
| nodeId | 对齐后 | graph.db 节点 ID（步骤 4 回填，初始为空） |
| graphDbMatch | 对齐后 | 对齐状态：true / false / error（步骤 4 回填） |
| matchNote | 对齐后 | 匹配备注：null / "ambiguous" / "not_found" / "db_not_found"（步骤 4 回填） |

### 步骤 4: graph.db nodeId 对齐

运行 `scripts/phase1_node_align.py`，将每个入口与 graph.db 节点对齐：

```bash
python3 scripts/phase1_node_align.py <entries_path> <db_path>
```

脚本逻辑：
1. 遍历每个 entry，用 className + methodName 查 graph.db 的 Method 节点
2. 唯一匹配 → 写入 nodeId，graphDbMatch = true
3. 多个匹配 → 按 filePath 进一步解析，写入 nodeId + matchNote = "ambiguous"
4. 无匹配 → nodeId 为空，graphDbMatch = false，matchNote = "not_found"
5. graph.db 不存在 → 全部 graphDbMatch = "error"

对歧义和未匹配的入口，额外生成 `phase1/align-errors.json`。

## 输出

| 文件 | 生成步骤 | 说明 |
|------|---------|------|
| `flow-trace-db/.trace-cache/phase1/entries.json` | 步骤 3 + 步骤 4 | 入口列表（含 nodeId） |
| `flow-trace-db/.trace-cache/phase1/align-errors.json` | 步骤 4 | 歧义和未匹配入口明细（无错误时不生成） |
