# 链路节点过滤规则

本文件定义 Phase 3 终点导向 BFS 中的节点分类和过滤规则。

---

## 节点分类（三分类）

判断顺序：**噪声过滤 → 终点判定 → 默认可穿透**

### 噪声节点（直接丢弃）

以下节点不可能通往数据库或 RMB 外调，展开它们只会浪费 BFS 资源：

| # | 类型 | 判断条件 | 示例 |
|---|------|---------|------|
| 1 | JDK/标准库 | `java.*`、`javax.*`、`sun.*` 包前缀 | `java.lang.String.valueOf()` |
| 2 | 框架基础设施 | `org.springframework.*`、`com.alibaba.*`、`lombok.*`、`org.apache.commons.*` | `org.springframework.beans.BeanUtils.copyProperties()` |
| 3 | 数据容器类 | `*DTO`、`*Vo`、`*VO`、`*Entity`、`*Request`、`*Response` | `RiskDTO.getErrorCode()` |
| 4 | 工具/配置类 | `*Util`、`*Helper`、`*Constants`、`*Config`、`*Properties`、`*Enum` | `DateUtil.format()` |
| 5 | Lombok 生成方法 | getter/setter/builder/toString/hashCode/equals | `getXxx()`、`setXxx()` |
| 6 | 构造函数 | 方法名 `<init>` | `new RiskDTO()` |
| 7 | 日志调用 | `log.*`、`logger.*`、`LoggerFactory.*`、`*Logger.*` | `log.info("...")` |
| 8 | 非业务 getter/setter | 非 DAO/Mapper/Repository 类的 `get*`/`set*` 方法 | `request.getStatus()` |

### 终点节点（不展开）

| 类型 | 判断条件 | domainInteraction |
|------|---------|-------------------|
| 数据库操作 | 类名含 `Mapper`/`Dao`/`Repository` | `{type: "DATABASE", operation, table, direction}` |
| RMB 外调 | 类名含 `Client`/`Proxy` | `{type: "EXTERNAL", direction: "OUT", target, protocol: "RMB"}` |

### 可穿透节点（继续展开）

不属于以上两类的所有节点，出现在 chain 中并继续 BFS 展开。

---

## 扇出控制（三层过滤）

每个节点的子节点经过三层过滤后决定是否加入 BFS 队列：

| 层级 | 规则 | 说明 |
|------|------|------|
| 硬过滤 | 噪声节点直接丢弃 | 上述 8 种噪声类型 |
| 置信度过滤 | confidence >= 0.7 保留全部；[0.5, 0.7) 只保留终点；< 0.5 丢弃 | graph.db CALLS 关系的置信度 |
| 扇出上限 | MAX_FANOUT=10 | 每个节点最多展开 10 个子节点 |

---

## BFS 控制参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| maxDepth | 20 | 最大 BFS 深度 |
| maxNodes | 500 | 单入口最大节点数 |
| MAX_FANOUT | 10 | 单节点最大子节点展开数 |
| minConfidence | 0.5 | 最低置信度阈值 |

---

## 默认策略

**未匹配任何噪声规则的节点：可穿透（继续展开）。**
