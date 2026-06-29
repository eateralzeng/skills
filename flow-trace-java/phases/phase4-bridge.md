# Phase 4: DISPATCH 补充 + 桥接

## 概述

纯脚本阶段。分两步：
1. Phase 4a：将 dispatch-summary 中的终点挂载到剪枝后数据的 DISPATCH 节点下
2. Phase 5b-5e：匹配各种跨调用链的间接调用（RMB、MQ、Spring Event、@Async），合并为统一流程或标记关系

## 输入

- `phase3/{entryId}-pruned.json` — 剪枝后的数据（或 Phase 4 前序脚本的输出）
- `phase2b/dispatch-summary-*.json` — 分发点汇总（Phase 4a 消费）
- `phase1c/pattern-index.json` — 分发点索引（Phase 4a 消费）
- `phase1a/entries.json` — 入口列表（含 RMB 类型入口）
- 项目源码（grep 搜索 @KafkaListener、@EventListener、@Async 注解）

## 输出

- `phase4/dispatch-merge-report.json` — DISPATCH 补充报告（Phase 4a）
- `phase4/bridges.json` — RMB 桥接索引
- `phase4/mq-bridges.json` — MQ 桥接索引
- `phase4/event-bridges.json` — Event 桥接索引
- `phase4/async-bridges.json` — Async 桥接索引
- `phase4/merged-rmb-{senderId}-{receiverId}.json` — 合并后的 RMB 流程
- `phase4/merged-mq-{senderId}-{topic}.json` — 合并后的 MQ 流程
- `phase4/merged-event-{senderId}-{eventClass}.json` — 合并后的 Event 流程
- `phase4/{entryId}.json` — 未合并流程的副本（含异步标记）

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

## 桥接脚本执行步骤

依次运行四个桥接脚本：

### 4b. RMB 桥接

```bash
python3 <skill_dir>/scripts/phase4_rmb_bridge.py --cache-dir <cache_dir> --entries <entries_path>
```

算法：
1. 从 entries.json 收集 RMB 类型入口，按 Topic 建索引
2. 遍历所有 pruned 树，找 domainInteraction.type=="EXTERNAL" 且 protocol=="RMB" 的节点
3. 对每个 RMB 发送端：
   a. 提取 Topic
   b. 在 RMB 入口索引中按 Topic 匹配
   c. 匹配成功：创建合并流程（发送端 chain + BRIDGE 虚拟节点 + 接收端 chain）
   d. 未匹配：保持 STANDALONE_FLOW
4. 非RMB流程：直接复制到 phase4

### 4c. MQ 桥接

```bash
python3 <skill_dir>/scripts/phase4_mq_bridge.py --cache-dir <cache_dir> --entries <entries_path> --project-dir <project_dir>
```

算法：
1. 遍历所有 phase4（或 phase3）的 chain
2. 识别 MQ 发送节点：domainInteraction.type=="MQ" 的终点节点
3. 提取 Topic：从 domainInteraction.target 获取（Phase 3 子代理已提取）
4. 在项目源码中 grep 搜索 @KafkaListener/@JmsListener，按 Topic 匹配
5. 匹配成功：创建合并流程（sender chain + BRIDGE + receiver stub）
6. 未匹配：标记 UNMATCHED

### 4d. Spring Event 桥接

```bash
python3 <skill_dir>/scripts/phase4_event_bridge.py --cache-dir <cache_dir> --entries <entries_path> --project-dir <project_dir>
```

算法：
1. 遍历所有 phase4（或 phase3）的 chain
2. 在项目源码中 grep 搜索 publishEvent 调用，提取 Event 类名
3. 在项目源码中 grep 搜索 @EventListener/@TransactionalEventListener，提取监听的 Event 类
4. 按 Event 类名匹配
5. 匹配成功：创建合并流程（sender chain + BRIDGE + listener stub）
6. 未匹配：标记 UNMATCHED

### 4e. @Async 桥接

```bash
python3 <skill_dir>/scripts/phase4_async_bridge.py --cache-dir <cache_dir> --entries <entries_path> --project-dir <project_dir>
```

算法：
1. 在项目源码中 grep 搜索 @Async 注解，构建异步方法索引
2. 遍历所有 phase4（或 phase3）的 chain
3. 对每个非终点节点，检查其 (filePath, method) 是否在异步方法索引中
4. 匹配到：在节点中添加 `async: true` 标记和 domainInteraction
5. 输出异步桥接索引

## 桥接信息结构

RMB 桥接：
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

MQ / Event 桥接：
```json
{
  "type": "MQ | EVENT",
  "topic": "topic-name (MQ) | eventClass (Event)",
  "matchingStatus": "MATCHED | UNMATCHED",
  "senderHandlerId": "handler-id",
  "receiverHandlerId": "handler-id",
  "isExternal": false
}
```

Async 桥接：
```json
{
  "type": "ASYNC",
  "matchingStatus": "DETECTED",
  "handlerId": "handler-id",
  "nodeId": "node-id",
  "filePath": "...",
  "method": "..."
}
```

## 合并流程的 chain 结构

```
发送端 chain [layer 0..N]
├── BRIDGE 虚拟节点 [layer N+1]
└── 接收端 chain/stub [layer N+2..M]
```

接收端节点的 layer 值从发送端末尾继续递增。

## 错误处理

- 发送端无 Topic / Event 类名无法提取：跳过桥接，标记 UNMATCHED
- 接收端无 chain（NO_ENDPOINT）：标记 UNMATCHED
- 一个发送端可匹配多个接收端（广播模式），每个生成独立的合并流程
- grep 超时：跳过该类型桥接，输出警告
