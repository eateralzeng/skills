# Phase 3: graph.db 终点导向链路提取

> 从入口 BFS 展开，只保留能到达终点（数据库操作/RMB外调）的路径

## 核心原则

- **终点导向**：只保留能到达终点的路径，不在终点路径上的节点不出现在 chain 中
- **终点类型**：数据库操作（Mapper/Dao/Repository）或 RMB 外调（Client/Proxy）
- **节点三分类**：噪声 → 终点 → 可穿透，按顺序判断

## 节点分类

### 噪声节点（直接丢弃）

1. **JDK/标准库**：`java.*`、`javax.*`、`sun.*` 包下节点
2. **框架基础设施**：`org.springframework.*`、`com.alibaba.*`、`lombok.*`、`org.apache.*`（非业务包）
3. **数据容器**：`*DTO`、`*Vo`、`*VO`、`*Entity`、`*Request`、`*Response` 类的方法
4. **工具/配置**：`*Util`、`*Helper`、`*Constants`、`*Config`、`*Properties`、`*Enum` 类
5. **Lombok 生成**：getter/setter/builder/toString/hashCode/equals
6. **构造函数**：`<init>` 方法
7. **日志调用**：`log.*`、`logger.*`、`LoggerFactory.*`
8. **Getter/Setter**：非 DAO/Mapper/Repository 类的 `get*`/`set*` 方法

### 终点节点（不展开）

- 类名含 `Mapper`/`Dao`/`Repository` 的方法（数据库操作终点）
- 类名含 `Client`/`Proxy` 的方法（RMB 外调终点）

### 可穿透节点（继续展开）

- 不属于以上两类的所有节点

## BFS 流程

```
1. 初始化队列：[入口节点]
2. While 队列非空:
   a. 弹出节点
   b. 查询 CALLS 关系获取子节点
   c. 对每个子节点:
      - 噪声 → 跳过
      - 终点 → 加入 chain，标记 terminal=true，填充 domainInteraction
      - 可穿透 → 加入 chain，加入队列继续展开
   d. 应用扇出控制（三层过滤）
3. BFS 结束后执行终点回溯剪枝
```

## 扇出控制（三层过滤）

| 层级 | 规则 |
|------|------|
| 硬过滤 | 噪声节点直接丢弃 |
| 置信度过滤 | confidence >= 0.7 保留全部；[0.5, 0.7) 只保留终点；< 0.5 丢弃 |
| 扇出上限 | MAX_FANOUT=10，每个节点最多展开 10 个子节点 |

## 终点回溯剪枝

BFS 结束后，从所有 terminal 节点沿 parentId 回溯到入口。不可达入口的节点移入 `discardedEdges`（reason: `not_on_terminal_path`）。

## domainInteraction 赋值

5 条路径，按顺序匹配，命中即停止：
1. **QUERIES 关系**：Method → CodeElement，提取表名和操作类型
2. **db-schema lookup**：从 db-schema.json 查找类名/方法名对应的表
3. **Dao→Mapper 代理解析**：Dao 类未直接命中 lookup 时，查其 CALLS 子节点中是否有 Mapper 匹配 lookup（source 标记为 `delegate-dao-to-mapper`）
4. **RMB Client 识别**：类名含 Client/Proxy，从源码注解提取 Topic
5. **RMB Controller 识别**：`@RmbController` 注解的入口

## 输出格式

每个入口生成 `{entryId}.json`：

```json
{
  "entryId": "controller-001",
  "entryType": "controller",
  "status": "COMPLETE",
  "chain": [
    {
      "nodeId": "...",
      "layer": 0,
      "layerType": "ENTRY",
      "class": "...",
      "method": "...",
      "package": "...",
      "file_path": "...",
      "parentId": null,
      "callSiteLine": null,
      "terminal": false,
      "description": "",
      "domainInteraction": null
    }
  ],
  "discardedEdges": [...],
  "unexpandedNodes": [...]
}
```

## 执行

运行 `scripts/phase3_chain_extract.py`：

```bash
python3 scripts/phase3_chain_extract.py <db_path> <entries_path> <cache_dir> [--db-schema <db_schema_path>]
```
