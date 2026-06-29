# Phase 4: DISPATCH 补充 + RMB 桥接

## 概述

纯脚本阶段。分两步：
1. Phase 4a：将 dispatch-summary 中的终点挂载到剪枝后数据的 DISPATCH 节点下
2. Phase 4b：RMB 跨进程调用桥接，把发送端 chain 与接收端 chain 拼接成统一流程

> ⚠️ 2026-06-22 精简（CR-04 选项B）：目标项目仅使用 RMB，已移除 MQ / Spring Event / @Async 桥接（原 4c/4d/4e 脚本及规则）。如需恢复参见 git 历史的 `phase4_mq_bridge.py` / `phase4_event_bridge.py` / `phase4_async_bridge.py`。

## 输入

- `phase3/{entryId}-pruned.json` — 剪枝后的数据（或 Phase 4 前序脚本的输出）
- `phase2b/dispatch-summary-*.json` — 分发点汇总（Phase 4a 消费）
- `phase1c/pattern-index.json` — 分发点索引（Phase 4a 消费）
- `phase1a/entries.json` — 入口列表（含 RMB 类型入口）

## 输出

- `phase4/dispatch-merge-report.json` — DISPATCH 补充报告（Phase 4a）
- `phase4/bridges.json` — RMB 桥接索引（含 `matchedReceivers`，phase5/6 跳过这些 receiver）
- `phase4/{entryId}.json` — 入口流程（决策 10：in-place 补全，sender 入口 chain 直接含跨进程 receiver 链路；matched receiver 不独立产出）

> ⚠️ 决策 10（2026-06-24）：RMB 桥接改为 in-place 补全——receiver chain 拼回 sender 入口 chain，**不再产 `merged-rmb-*.json` 新文件**；多级链路 DFS 递归展开；matched receiver 由 `bridges.json.matchedReceivers` 标记移除。

## 前置条件

- Phase 3（路径剪枝）已完成
- Phase 2b（分发点分析）已完成

## 执行步骤

### 4a. DISPATCH 补充

```bash
python3 <skill_dir>/scripts/phase4_dispatch_merge.py --cache-dir <cache_dir> --entries <entries_path>
```

算法：
1. 加载所有 `phase2b/dispatch-summary-*.json`
2. 遍历 phase3 中的 chain，找 `endpointType == "DISPATCH"` 的节点
3. 通过 `patternRef` 匹配 dispatch-summary
4. 将 dispatch-summary 中的 endpoints 去重后挂载为子节点
5. DISPATCH 父节点从 terminal 变为 intermediate
6. 输出到 `phase4/{entryId}.json`

挂载后的子节点结构：
```json
{
  "nodeId": "DISPATCH:MapperClass:methodName",
  "class": "MapperClass",
  "method": "methodName",
  "terminal": true,
  "endpointType": "DATABASE",
  "domainInteraction": {"type": "DATABASE", "table": "xxx", "operation": "SELECT"},
  "dispatchImpl": "ImplA, ImplB",
  "dispatchCondition": "OrgType=PERSON, OrgType=CORP"
}
```

去重规则：多个实现类共享同一个 Mapper 方法时，只保留一个子节点，`dispatchImpl` 列出所有实现类。

### 4b. RMB 桥接

```bash
python3 <skill_dir>/scripts/phase4_rmb_bridge.py --cache-dir <cache_dir> --entries <entries_path>
```

算法：
1. 从 entries.json 收集 RMB 类型入口，按 Topic 建索引
2. 遍历所有 pruned 树，找 `domainInteraction.type=="EXTERNAL"` 且 `protocol=="RMB"` 的节点
3. 对每个 RMB 发送端：
   a. 提取 Topic（`routingKeys.topic`，fallback `domainInteraction.target`）
   b. 在 RMB 入口索引中按 Topic 匹配（transCode 二次过滤）
   c. 匹配成功：创建合并流程（发送端 chain + BRIDGE 虚拟节点 + 接收端 chain，layer 连续递增）
   d. 未匹配：保持 STANDALONE_FLOW
4. 非 RMB 流程：直接复制到 phase4

## 桥接信息结构

RMB 桥接（写入 bridges.json 及 merged flow 的 rmbBridge 字段）：
```json
{
  "topic": "topic-name",
  "topicMode": "SYNC | ASYNC",
  "transCode": "xxx | null",
  "matchingStatus": "MATCHED | UNMATCHED",
  "senderHandlerId": "handler-id",
  "receiverHandlerId": "handler-id",
  "isExternal": false
}
```

## 合并流程的 chain 结构

```
发送端 chain [layer 0..N]
├── BRIDGE 虚拟节点 [layer N+1]
└── 接收端 chain [layer N+2..M]
```

接收端节点的 layer 值经 `_remap_layers` 从发送端末尾继续递增。

## 错误处理

- 发送端无 Topic：跳过桥接，标记 UNMATCHED
- 接收端无 chain（NO_ENDPOINT）：标记 UNMATCHED
- 一个发送端可匹配多个接收端（广播模式），每个生成独立的合并流程
