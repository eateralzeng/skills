# RMB 桥接匹配规则

本文件定义 RMB 桥接的匹配策略、匹配键和合并规则。
不同项目的 RMB 框架实现不同，通过此文件适配。

---

## 匹配策略

### 发送端识别

以下任一条件匹配即识别为 RMB 发送端：

1. **@RmbClient 注解**：类或字段上标注 `@RmbClient` 注解
2. **rmbClientProxy 调用**：代码中调用 `rmbClientProxy.invoke()` 或类似方法
3. **graph.db 识别**：graph.db 中存在 `CALLS` 关系指向已知 RMB 框架类

### 接收端识别

以下任一条件匹配即识别为 RMB 接收端：

1. **@RmbController 注解**：类上标注 `@RmbController` 注解
2. **Chameleon Flow 接口**：实现 `com.webank.chameleon.frw.rmb.flow` 接口 + execute 方法
3. **graph.db ENTRY_POINT_OF**：graph.db 中标记为 RMB 类型的入口节点

---

## 匹配键

### 数据字段映射

发送端（Phase 2a tree.json 中的 RMB 终点节点）：

```json
"domainInteraction": {
  "type": "EXTERNAL",
  "direction": "OUT",
  "target": "Class.method",
  "protocol": "RMB",
  "routingKeys": {
    "topic": "实际 topic 值",
    "transCode": "transCode 值或 null"
  }
}
```

接收端（entries.json 中的 RMB 入口）：

```json
{
  "rmbTopic": "topic 值",
  "transCode": "transCode 值或 null"
}
```

### 主匹配键：Topic 名称

| 字段 | sender | receiver | 必须匹配 |
|------|--------|----------|---------|
| topic | routingKeys.topic | rmbTopic | 是 |

- **精确匹配**（默认）：发送端和接收端的 Topic 字符串完全相同
- **向后兼容**：如果 sender 缺少 routingKeys，fallback 到 `domainInteraction.target`（旧数据，匹配率低）

### 辅助匹配键：transCode

| 字段 | sender | receiver | 必须匹配 |
|------|--------|----------|---------|
| transCode | routingKeys.transCode | transCode | 否 |

匹配条件：
- 两边都有非 null 值时 → 必须相等
- 其中一方为 null → transCode 不参与匹配，仅靠 topic 匹配
- 应用场景：同一 Topic 下通过 transCode 多路复用

### topicTransform 规则

默认为空（精确匹配）。如需环境前缀剥离，添加正则：

```
# 示例：剥离 dev- 前缀
pattern: "^dev-(.+)$"
replacement: "$1"
```

---

## Topic 提取策略（4 级优先级）

从发送端源码中提取 Topic 信息的优先级：

1. **注解参数**：`@RmbTopic(topic = "xxx")` 中的 topic 字面值
2. **方法参数**：`rmbClientProxy.invoke("topic-name", ...)` 中的字符串常量
3. **常量引用**：`rmbClientProxy.invoke(TopicConstants.XXX, ...)` → 追踪到常量定义处获取值
4. **配置文件**：`@RmbTopic(topic = "${app.rmb.topic.xxx}")` → 从 application.yml/properties 读取

---

## 合并规则

> ⚠️ 决策 10（2026-06-24）：合并策略改为 **in-place 补全**——下方"拼接"机制仍适用（sender chain + BRIDGE + receiver chain），但**产物落点变了**：不再产独立的 `merged-rmb-*.json`，而是 in-place 拼回 sender 入口的 `phase4/{senderId}.json`；多级链路 DFS 递归展开；matched receiver 由 `bridges.json.matchedReceivers` 标记移除（phase5/6 跳过）。

### MERGED_RMB_FLOW（flowType，标在 sender 入口的 phase4/{senderId}.json）

当发送端和接收端都存在于当前代码库中：

1. 发送端 chain（从入口到 RMB_CLIENT 节点）和接收端 chain（从 RMB_CONTROLLER 节点开始）拼接
2. 插入一个 `layerType: "BRIDGE"` 的虚拟连接节点
3. 接收端节点的 layer 值从发送端末尾继续递增
4. rmbBridge 字段记录完整的桥接信息
5. （决策 10）receiver chain 递归展开其下游 RMB（多级链路）；matched receiver 不独立产出

> 注：在 MERGED_RMB_FLOW 中，RMB_CONTROLLER 接收端节点本身的 domainInteraction 标记为 `{"type": "EXTERNAL", "direction": "IN", "target": "<topic>", "protocol": "RMB"}`。桥接虚拟节点（BRIDGE）不携带 domainInteraction，仅作为 chain 结构的连接标记。

### 广播模式（一个发送端对应多个接收端）

- （决策 10）每个 receiver 的 chain 都 in-place 拼进 sender 入口 chain（多段 BRIDGE），sender chain 含所有 receiver 链路
- 共享同一个发送端 chain 前缀

### 未匹配

无法找到对应端：
- 保留为 STANDALONE_FLOW
- rmbBridge.matchingStatus = "UNMATCHED"
- 缺失端标记为 [external]

---

## 桥接信息结构

合并后 flow 的 rmbBridge 字段：

```json
{
  "topic": "topic-name",
  "topicMode": "SYNC | ASYNC",
  "transCode": "xxx | null",
  "matchingStatus": "MATCHED | UNMATCHED",
  "senderModule": "module-name",
  "receiverModule": "module-name",
  "senderHandlerId": "handler-id",
  "receiverHandlerId": "handler-id",
  "isExternal": false
}
```

---

## 嵌套 RMB 调用

当入口类型为 Controller 或 Job，但其调用链中调用了 `@RmbClient` 时，同样需要执行桥接匹配。

**规则**：
- 找到匹配 → 整体流程类型为 MERGED_RMB_FLOW，发送方为 Controller/Job 模块
- 未找到匹配 → 保持 STANDALONE_FLOW，`@RmbClient` 调用记录到 externalCalls
- 一个流程中可能包含多个 `@RmbClient` 调用（多个 RMB Topic），每个都需独立桥接
- 禁止将 `@RmbClient` 调用简单记录为 externalCalls 而跳过桥接匹配

## 共享 Topic 多路复用

当多个 `@RmbController` 共享同一 Topic 但通过不同 transCode 区分时：
- 每组 `topic + transCode` 对应一个独立的业务流程
- 如果发送端不在本代码库中，将每个 handler 作为独立 RMB 接收流程处理
- 发送端统一标记为 `[external - <系统名称>]`

---

## 如何新增自定义桥接规则

如果项目使用不同的 RMB 框架，在下方添加新规则：

```
### 自定义规则：[框架名称]

发送端识别：[描述]
接收端识别：[描述]
匹配键：[描述]
Topic 提取方式：[描述]
```

---

<!-- MQ / Spring Event / @Async 桥接规则已于 2026-06-22 移除（CR-04 选项B：目标项目仅用 RMB）。
     如需恢复，参见 git 历史的 phase4_mq_bridge.py / phase4_event_bridge.py / phase4_async_bridge.py 及对应规则。 -->
