# Phase 5: 源码校验 + 业务描述填充

> LLM 子代理读取源码，校验 discardedEdges/unexpandedNodes，填充业务描述

## 概述

Phase 5 是唯一读取源码的阶段。LLM 子代理定点读取 Java 源码文件，完成：
1. 校验 discardedEdges 中的子调用是否应该恢复到 chain
2. 展开 unexpandedNodes 中未展开的节点
3. 为 chain 中缺少 description 的节点生成业务描述

本阶段不直接调用 LLM，而是通过 `phase5_source_verify.py` 的 prepare/merge 两步模式，配合 `prompt.md` 编排的子代理完成。

## 前置条件

- Phase 4 已完成（RMB 桥接已完成，可利用桥接信息）
- chain 中节点包含 `file_path` 信息

## 执行流程

### 步骤 1: 准备（prepare）

运行 `scripts/phase5_source_verify.py --mode prepare`：

```bash
python3 scripts/phase5_source_verify.py <cache_dir> <project_dir> --mode prepare --entries-path <entries_path>
```

生成 `verify-tasks.json`，包含三类任务：
- `discardedEdgeTasks`：待验证的被丢弃边
- `unexpandedNodeTasks`：待展开的未展开节点
- `descriptionTasks`：待生成描述的节点

### 步骤 2: LLM 子代理读源码

`prompt.md` 编排子代理，对每个入口流程：
1. 读入口方法源码 → 理解整体业务 → 生成入口 description
2. 对每个子调用，判断父方法源码是否已充分说明：
   - 已充分 → 不读子方法，直接生成 description
   - 不充分 → 读子方法源码，重复判断
3. 校验 discardedEdges（先探后提交）：
   - child 是终点 → 直接补充
   - child 是可穿透 → 递归探查是否可达终点 → 可达则补充
4. 展开 unexpandedNodes（先探后提交）

### 步骤 3: 合并（merge）

运行 `scripts/phase5_source_verify.py --mode merge`：

```bash
python3 scripts/phase5_source_verify.py <cache_dir> <project_dir> --mode merge --verify-results <results_path>
```

将 LLM 结果合并回 `{entryId}.json`：
- 恢复验证通过的节点到 chain
- 填充 description 字段
- 清理已验证的 discardedEdges 和 unexpandedNodes

## 先探后提交

对 discardedEdges 和 unexpandedNodes 中的可穿透节点，先递归探查其子调用是否可达终点：
- 可达终点 → 补充到 chain（含沿途节点和终点）
- 不可达终点 → 不补充，确认丢弃/展开正确

## 业务描述提取策略

优先级：Java 注释/Javadoc > 代码逻辑推断 > 方法名推断（fallback）

### 不需要读源码的场景

- 父方法注释已充分说明子调用目的
- 标准 Mapper 方法（selectByXxx/insertXxx 等）
- RMB 调用（Topic 名称已足够推断）
- 终点节点的 description 从父节点上下文推断

### 源码读取量

每个入口预计 3-5 个源码文件：
- 入口方法（必读）
- 1-3 个核心 Service/Handler（按需）
- Dao/Client 按上下文充分性决定

## 输出

| 文件 | 生成步骤 | 说明 |
|------|---------|------|
| `phase5/verify-tasks.json` | 步骤 1 | 校验任务清单 |
| `phase5/{entryId}.json` | 步骤 3 | 修正后的 chain 数据（不修改 phase3 原始文件） |

下游阶段（Phase 6/7）读取链路数据时，优先从 `phase5/` 读取，不存在则降级读 `phase3/`。
