# Phase 5: 业务语义填充

> ⚠️ **决策 12 Commit 3a 已重构**（2026-06-22）：`split_into_batches` 已改（**CR-06**：移除 layer 排序 + 连通性约束 → nodeId 字典序分批，决策 10 完全并行）；子代理不传 `parentDescription`（位置无关）；CR-04 后虚拟节点仅 `BRIDGE`。下方「分批策略 / 并行性约定 / parentDescription / MQ_LISTENER / EVENT_LISTENER」相关段落为**存量描述**，新实现以 `design/phase5-design.md`（L9 状态）+ `prompt.md` Phase 5 段命令为准。

## 概述

LLM 子代理重新遍历剪枝/桥接后的调用树，为每个节点生成业务语义描述。这是 flow-trace-java 的核心增值部分。

## 输入

- `phase4/` 中的合并/未合并流程数据
- `phase3/` 中的剪枝后数据（phase4 不存在时的降级）
- `prompts/phase5-describe.md` — 子代理提示词模板

## 输出

- `phase5/{entryId}-semantics.json` — 包含业务描述的流程数据

## 前置条件

- Phase 4 已完成（或 Phase 3 已完成）

## 编排模型

与 Phase 2a 相同的三层模型：

| 角色 | 实体 | 职责 |
|------|------|------|
| 遍历控制 | 编排者 (prompt.md) | 遍历树、派发批次 |
| 描述管理 | 编排者直接更新 | 将描述合并到树中 |
| 源码读取 | 子代理 (prompts/phase5-describe.md) | 读源码、生成描述 |

## 执行流程

```
对每个入口 entry：
  0. 【跳过检查】
     - 如果 entry.id ∈ phase4/bridges.json.matchedReceivers → 跳过（决策 10：链路已 in-place 并入 sender，不独立处理）
     - 如果 entry.id ∈ progress.completedEntries → 跳过（断点续传，见下方"断点续传"）
  1. 运行 prepare 脚本：
     python3 <skill_dir>/scripts/phase5_describe.py \
       --mode prepare --cache-dir <cache_dir> --entry-id <entry.id>
     脚本完成：
     - 加载流程数据（优先 phase4，降级 phase3）
     - 按下方"节点分类"规则标记每个节点类别
     - 生成所有编排者节点描述（脚本确定性输出，写入 phase5/{entryId}-prepare.json）
     - 对子代理节点按 nodeId 字典序分批（决策 10 完全并行，CR-06）
     输出：phase5/{entryId}-prepare.json，包含 orchestratorDescriptions、subagentBatches
  2. 读取 prepare 输出，对每个批次【并行】派发子代理（决策 10：位置无关，批次间无依赖，不传 parentDescription）：
     a. 准备子代理提示词（替换 {{nodes}}、{{project_dir}}、{{output_path}}）
     b. 派发子代理（多批可并发），收集子代理输出 JSON
  3. 运行 merge 脚本（合并所有描述）：
     python3 <skill_dir>/scripts/phase5_describe.py \
       --mode merge --cache-dir <cache_dir> --entry-id <entry.id> \
       --subagent-output <subagent_output_path>
     脚本完成：
     - 加载原始 chain 数据
     - 应用 prepare 的 orchestratorDescriptions
     - 应用子代理输出（优先级高于编排者）
     - 写入 phase5/{entryId}-semantics.json
  4. 【持久化】将 entry.id 追加到 progress.completedEntries，立即写盘 progress.json
```

## 节点分类与描述生成（由 phase5_describe.py --mode prepare 实现）

> **实现说明**：以下分类规则和描述模板由 `scripts/phase5_describe.py` 实现，编排者不再手工判断节点类别或拼接描述。本节作为脚本逻辑的概念文档，便于审查和扩展。

采用两层分类：第 0 层按节点类型（虚拟 vs 真实）分发，第 1 层对真实节点按业务语义过滤。

### 第 0 层：虚拟节点（按 class 字段判定）

Phase 4 桥接脚本注入的虚拟节点（`class` 为类型常量，`filePath` 通常为空）不需要读源码，由编排者按模板生成描述。

**虚拟节点类型注册表**：

| class | 来源 | 描述模板 |
|-------|------|---------|
| `BRIDGE` | Phase 4 RMB/MQ/Event 桥接 | 按桥接子类型细分（见下表） |
| `MQ_LISTENER` | Phase 4 MQ 桥接的接收端 stub | `"MQ 接收端：监听 topic={topic}"` |
| `EVENT_LISTENER` | Phase 4 Event 桥接的接收端 stub | `"事件监听器：处理 {eventClass} 事件"` |

**BRIDGE 节点的子类型细分**（按 nodeId 前缀判定）：

| 桥接类型 | 识别方式 | 描述模板 |
|---------|---------|---------|
| RMB | nodeId 以 `BRIDGE:RMB:` 开头（或对应 RMB 桥接脚本产出的命名） | `"RMB 桥接：通过 {target} 触发接收端"` |
| MQ | nodeId 以 `BRIDGE:MQ:` 开头 | `"MQ 桥接：通过 topic={topic} 触发接收端"` |
| Event | nodeId 以 `BRIDGE:EVENT:` 开头 | `"事件桥接：发布 {eventClass} 事件"` |

**字段提取来源**：
- `topic`：从 nodeId 后缀 `BRIDGE:MQ:...->{topic}` 解析；无法解析时从 `description` 字段提取
- `eventClass`：从 nodeId 后缀 `BRIDGE:EVENT:...->{eventClass}` 解析
- RMB `target`：从 nodeId 后缀 `BRIDGE:{sender}->{receiver}` 解析；无法解析时使用 `description` 字段

> **扩展约定**：未来 Phase 4 引入新的虚拟节点类型时，只需在本注册表新增一行，无需修改第 1 层规则。

### 第 1 层：真实节点的业务语义跳过规则

第 0 层未匹配的节点（`class` 是包名.类名的 FQN）按以下 5 条业务语义规则过滤：

1. **标准 Mapper 方法（terminal=true）**：方法名匹配 `select*`/`insert*`/`update*`/`delete*`/`query*`/`find*`/`get*`/`save*`/`count*`/`exists*`，**且类名包含 `Mapper`/`Repository` 后缀**，**且节点 `terminal=true`**（terminal=true 才是数据访问终点；非 terminal 的 Dao/Service 同等对待，由子代理读源码理解业务含义）
   → 从方法名推断描述

2. **带 domainInteraction 的终端节点**：
   → 从 domainInteraction 推断（如 `{type: DATABASE, operation: SELECT, table: account}` → "查询 account 表"）

3. **getter/setter**：
   → 跳过，描述为空

4. **DISPATCH 分发节点**（endpointType == "DISPATCH"）：
   → 从 patternRef 读取 dispatch-summary 文件生成描述
   → 编排者负责：读取 dispatch-summary-{patternRef}.json，生成 "多态分发：根据 {conditions} 路由到 N 个实现类"
   → 子节点（DISPATCH_IMPL 类型）的描述从 summary 的 endpoints 字段直接推断
   → **字段缺失 fallback**：缺 `patternRef` 或 dispatch-summary 文件不存在时，生成 "多态分发节点（实现细节未识别）"，不让节点进子代理批次

5. **DISPATCH 子节点**（callType == "DISPATCH_IMPL"）：
   → 从 dispatchImpl 和 domainInteraction 推断
   → 不需要读源码
   → **字段缺失 fallback**：缺 `dispatchImpl` 时，生成 "分发实现节点（细节未识别）"

> 这 5 条规则的处理对象是**真实节点**（FQN class）。未命中任何规则的节点进入子代理批次。

## 分批策略（决策 10，CR-06 重构后）

决策 10 废除父上下文传递后，批次划分不再需要连通性约束。`split_into_batches` 改为按 `nodeId` 字典序排序后等分切片，批次间无依赖、可完全并行：

```python
def split_into_batches(nodes, batch_size=15):
    nodes_sorted = sorted(nodes, key=lambda n: n.get("nodeId", ""))
    return [nodes_sorted[i:i + batch_size]
            for i in range(0, len(nodes_sorted), batch_size)]
```

## 并行性约定

| 维度 | 是否可并行 | 理由 |
|------|---------|------|
| 同入口内的批次 | ✅ 可并行 | 决策 10 位置无关，批次间无父子依赖（不传 parentDescription） |
| 不同入口之间 | ✅ 可并行 | 入口间无依赖（RMB 桥接合并的 chain 作为单逻辑入口处理） |

## 断点续传

### 进度跟踪

`progress.json` 的 Phase 5 部分包含 `completedEntries` 数组，记录已完成的入口 ID。

### 持久化策略

- 每个入口处理完成（执行流程步骤 7 写入 semantics.json）后，**立即**将 entryId 追加到 `completedEntries` 并持久化 `progress.json`（不依赖内存）
- 失败的入口不写入 `completedEntries`，下次重入时从头重试该入口

### 恢复流程

重入 Phase 5 时：
1. 读取 `progress.json`
2. 对每个 entry in entries：
   - 如果 `entry.id ∈ completedEntries` → 跳过该入口
   - 否则 → 完整执行该入口的所有步骤（包括失败入口的重试）
3. 所有入口处理完后，标记 Phase 5 整体 `COMPLETED`

### 幂等性约定

- **自动恢复**（默认）：跳过 `completedEntries` 中的入口
- **强制重跑**（用户明确要求"重跑 Phase 5"）：清空 `completedEntries` 后重新跑所有入口

## 子代理输出格式

```json
{
  "descriptions": [
    {
      "nodeId": "模块名:包名.类名:方法名",
      "description": "中文业务描述",
      "source": "source-code | inferred-method-name",
      "businessContext": "流程定位（可选）"
    }
  ]
}
```

## 错误处理

> **脚本错误**：prepare/merge 脚本失败时（文件不存在、JSON 格式错误），编排者捕获 stderr 输出后停止该入口处理，不进入子代理派发环节。

### 子代理批次失败

采用"批次减半重试"策略（与 Phase 2a 一致）：

| 重试轮次 | 批次大小 | 失败后动作 |
|---------|---------|----------|
| 1 | 15 | 缩小到 8 重试 |
| 2 | 8 | 缩小到 4 重试 |
| 3 | 4 | 缩小到 2 重试 |
| 4 | 2 | 缩小到 1 重试 |
| 5 | 1（单节点） | 用方法名降级 |

最终仍失败的单节点：使用方法名作为描述（驼峰拆词），source 标记为 `inferred-method-name`。

### 其他错误

- 源文件不存在：使用 `inferred-method-name` 作为描述来源
- 虚拟节点的 nodeId 后缀解析失败：使用 `description` 字段作为兜底；仍失败则用 `"桥接节点（元数据缺失）"`
