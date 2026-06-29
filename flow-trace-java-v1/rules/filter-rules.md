# 链路节点过滤规则

本文件定义 Phase 2a 终点导向 BFS 中的节点分类和过滤规则。

---

## 节点分类（三分类）

判断顺序：**噪声过滤 → 终点判定 → 默认可穿透**

### 噪声节点（直接丢弃）

以下节点不可能通往数据库或 RMB 外调，展开它们只会浪费 BFS 资源：

| # | 类型 | 判断条件 | 示例 |
|---|------|---------|------|
| 1 | JDK/标准库 | `java.*`、`javax.*`、`sun.*` 包前缀 | `java.lang.String.valueOf()` |
| 2 | 框架基础设施 | `org.springframework.*`、`com.alibaba.*`、`lombok.*`、`org.apache.*`、`com.google.*`、`com.fasterxml.*`、`cn.hutool.*`、`com.github.*`、`io.netty.*`、`org.slf4j.*` | `org.springframework.beans.BeanUtils.copyProperties()` |
| 3 | 数据容器类 | `*DTO`、`*Vo`、`*VO`、`*Entity`、`*Request`、`*Response` | `RiskDTO.getErrorCode()` |
| 4 | 工具/配置类 | `*Util`、`*Helper`、`*Constants`、`*Config`、`*Properties`、`*Enum` | `DateUtil.format()` |
| 5 | Lombok 生成方法 | getter/setter/builder/toString/hashCode/equals | `getXxx()`、`setXxx()` |
| 6 | 构造函数 | 方法名 `<init>` | `new RiskDTO()` |
| 7 | 日志调用 | `log.*`、`logger.*`、`LoggerFactory.*`、`*Logger.*` | `log.info("...")` |
| 8 | 非业务 getter/setter | 非 DAO/Mapper/Repository 类的 `get*`/`set*` 方法 | `request.getStatus()` |
| 9 | 项目外依赖 | 在项目源码目录中找不到目标类对应的 .java 文件 | 第三方 jar 中的类 |

> **注意**：间接调用（通过反射、代理、动态生成等方法）不在噪声规则列表中，需要特殊处理。

**排除**：以下调用不属于噪声，必须保留，交给终点判定：
- 同类内部方法调用（如 `this.uploadFile()`、同类 private 方法）
- 项目内任何有源码的类的方法调用
- 类名含 Client/Proxy/Template 的调用（即使源码不在项目中，它们可能是外调终点）

### 终点节点（不展开）

| 类型 | 判断条件 | domainInteraction |
|------|---------|-------------------|
| 数据库操作 | 类名含 `Mapper`/`Repository` | `{type: "DATABASE", operation, table, direction}` |
| RMB 外调 | 类名含 `Client`/`Proxy`，有 `@RmbClient` 注解 | `{type: "EXTERNAL", direction: "OUT", target, protocol: "RMB"}` |

> **注意**：`*Dao` 类不是终点。Dao 是业务层的数据库访问封装，内部可能包含业务逻辑（事务控制、批量操作、条件判断等），需要继续展开到实际的 `*Mapper` 调用。

### 可穿透节点（继续展开）

不属于以上两类的所有节点，出现在 chain 中并继续 BFS 展开。

---

## 扇出控制（两层过滤）

每个节点的子节点经过两层过滤后决定是否加入 BFS 队列：

| 层级 | 规则 | 说明 |
|------|------|------|
| 硬过滤 | 噪声节点直接丢弃 | 上述 8 种噪声类型 |
| 扇出上限 | MAX_FANOUT=10 | 每个节点最多展开 10 个子节点 |

---

## BFS 控制参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| maxDepth | 20 | 最大 BFS 深度 |
| maxNodes | 500 | 单入口最大节点数 |
| MAX_FANOUT | 10 | 单节点最大子节点展开数 |

---

### 接口/抽象类展开规则

当 BFS 遇到接口或抽象类节点时：
- **不作为终点**，即使有 `@Service` 注解
- **不作为噪声丢弃**，即使无方法体
- **展开所有具体实现类**：搜索 `implements InterfaceName` 和 `extends AbstractClassName`
- 展开产生的实现类节点标注 `callType: "POLYMORPHIC"`，parentId 指向触发展开的 Context 节点

---

## 默认策略

**未匹配任何噪声规则的节点：可穿透（继续展开）。**
