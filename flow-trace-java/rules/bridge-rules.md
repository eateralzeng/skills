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

### 主匹配键：Topic 名称

- **精确匹配**（默认）：发送端和接收端的 Topic 字符串完全相同
- **正则变换**（可选）：在下方 `topicTransform` 规则中定义正则，对 Topic 做预处理后匹配

### 辅助匹配键：transCode

- 仅当 `@RmbTopic` 和 `@AppHeaderArg` 中定义了 `transCode` 时启用
- 匹配条件：`topic + transCode` 同时相同
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

### MERGED_RMB_FLOW

当发送端和接收端都存在于当前代码库中：

1. 发送端 chain（从入口到 RMB_CLIENT 节点）和接收端 chain（从 RMB_CONTROLLER 节点开始）拼接
2. 插入一个 `layerType: "BRIDGE"` 的虚拟连接节点
3. 接收端节点的 layer 值从发送端末尾继续递增
4. rmbBridge 字段记录完整的桥接信息

> 注：在 MERGED_RMB_FLOW 中，RMB_CONTROLLER 接收端节点本身的 domainInteraction 标记为 `{"type": "EXTERNAL", "direction": "IN", "target": "<topic>", "protocol": "RMB"}`。桥接虚拟节点（BRIDGE）不携带 domainInteraction，仅作为 chain 结构的连接标记。

### 广播模式

一个发送端对应多个接收端：
- 每个接收端生成独立的 MERGED_RMB_FLOW
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

## MQ 桥接规则

### 发送端识别

Phase 3 中已被标记为 MQ_PUBLISH 终点的节点（domainInteraction.type="MQ"）。

### 接收端识别

方法标注 @KafkaListener / @JmsListener / @RabbitListener / @RocketMQMessageListener 注解。

### 匹配键

主匹配键：Topic 名称
- 发送端：domainInteraction.target（Phase 3 子代理已提取）
- 接收端：@KafkaListener 的 topics 属性

### Topic 提取策略

发送端 Topic 已由 Phase 3 子代理从源码提取，存储在 domainInteraction.target 中。
接收端 Topic 从注解属性或配置文件中提取。

---

## Spring Event 桥接规则

### 发送端识别

调用链中存在 ApplicationEventPublisher.publishEvent(event) 调用。
Phase 3 子代理将 publishEvent 作为普通穿透节点保留在链中。
Phase 5 脚本需要识别这些节点并提取 Event 类名。

### 接收端识别

方法标注 @EventListener 或 @TransactionalEventListener 注解，且参数类型与发送端 Event 类匹配。

### 匹配键

主匹配键：Event 类名

### Event 提取策略

1. **直接类引用**：`publishEvent(new XxxEvent(...))` → Event 类名为 XxxEvent
2. **变量引用**：`publishEvent(event)` → 从变量声明类型推断

---

## @Async 桥接规则

### 调用端识别

调用链中存在对 @Async 标注方法的调用。Phase 5 脚本通过 grep 检查目标方法源码中的注解。

### 被调端识别

方法标注 @Async 注解。

### 匹配键

方法签名（class + method）+ filePath 精确定位

### 注意事项

- @Async 桥接不需要跨入口拼接，只需标记调用关系为异步
- CompletableFuture.supplyAsync() 内的调用链需要特殊处理（Lambda 内提取）
