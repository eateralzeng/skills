# flow-trace-java 定制化指南

本文档记录将 flow-trace-java 适配到不同 Java 工程时，需要调整的配置项和文件。

## 目录结构

```
flow-trace-java/
├── rules/                    # 规则配置（主要定制点）
│   ├── entry-rules.json      # 机器可读入口扫描规则
│   ├── entry-rules.md        # 人类可读入口判断规则
│   ├── filter-rules.md       # Phase 2a 噪声过滤和终点判定规则
│   ├── endpoint-rules.md     # 终点类型定义
│   └── bridge-rules.md       # RMB 桥接匹配规则
├── prompts/                  # LLM 子代理提示词模板
│   ├── phase2a-discover.md    # Phase 2a 调用发现提示词
│   └── phase5-describe.md    # Phase 5 业务语义提示词
├── scripts/                  # Python 辅助脚本
│   ├── phase1a_entry_scan.py  # Phase 1a 入口扫描
│   ├── phase1b_db_schema.py   # Phase 1b DB Schema 收集
│   ├── phase2a_tree_expand.py # Phase 2a 调用树管理
│   ├── phase3_path_prune.py  # Phase 3 路径剪枝
│   ├── phase4_rmb_bridge.py  # Phase 4 RMB 桥接
│   └── phase6_doc_gen.py     # Phase 6 文档生成
├── phases/                   # 各阶段执行规格
├── templates/                # 输出文档模板
└── SKILL.md                  # Skill 入口定义
```

---

## 1. 入口扫描规则（Phase 1）

### 配置文件：`rules/entry-rules.json`

#### 1.1 噪声排除

| 字段 | 说明 | 示例 |
|------|------|------|
| `noise.globExclusions` | 排除的文件名模式 | `**/*DTO.java`, `**/*Test.java` |
| `noise.packageExclusions` | 排除的包前缀 | `org.springframework.*`, `com.alibaba.*` |
| `noise.classNameNoise` | 类名中含这些词则排除 | `Advice`, `Interceptor` |

适配新项目时，检查是否需要增减排除模式。例如项目使用了新的工具库（如 `org.mapstruct.*`），需要加入排除列表。

#### 1.2 入口类型配置

`entryTypes` 数组中每个对象定义一种入口类型：

| 字段 | 说明 |
|------|------|
| `type` | 入口类型标识：`controller` / `rmb` / `job` |
| `discovery` | 文件发现配置（grep 模式或 glob 模式，支持数组和单对象） |
| `annotationMarkers` | 文件内容过滤用的注解标记 |
| `mappingAnnotations` | HTTP Mapping 注解列表（controller 类型） |
| `topicAnnotation` | Topic 注解名（rmb 类型） |
| `methodPatterns` | Job 入口方法名优先列表（job 类型） |
| `inheritanceMethodMap` | 基类 → 入口方法的映射（job 类型） |

#### 1.3 Job 继承方法映射

当 Job 类继承框架基类时，`methodPatterns` 中的方法名可能是基类方法。`inheritanceMethodMap` 配置基类到子类实际覆写方法的映射：

```json
{
  "type": "job",
  "methodPatterns": ["doJob", "execute", "run"],
  "inheritanceMethodMap": {
    "AbstractQuartzJob": "executeInternal",
    "CcpConcurrentTaskExecutor": "executeTaskInner",
    "ConcurrentTaskExecutor": "executeTask"
  }
}
```

解析优先级：
1. 解析 `extends BaseClass`，在 map 中查找基类对应的方法
2. 验证子类中是否存在该方法的覆写
3. 无匹配时回退到 `methodPatterns` 逐个尝试

适配新项目时，如果使用了不同的 Job 框架基类，需要添加对应的映射条目。

#### 1.4 框架注解适配

如果项目使用的注解不同于默认配置（例如自定义的 `@ServiceController` 而非 `@RestController`），需要修改 `discovery.grepPattern` 和 `annotationMarkers`。

人类可读文档 `rules/entry-rules.md` 需同步更新，保持文档与 JSON 配置一致。

---

## 2. 噪声过滤规则（Phase 3）

### 配置文件：`rules/filter-rules.md` 和 `prompts/phase2a-discover.md`

两个文件中的噪声规则必须保持一致。

#### 2.1 噪声包前缀（规则 #2）

当前排除的框架/库包前缀：

```
org.springframework.*
com.alibaba.*
lombok.*
org.apache.*
com.google.*
com.fasterxml.*
cn.hutool.*
com.github.*
io.netty.*
org.slf4j.*
```

适配新项目时，如果项目依赖了新的第三方库（如 `org.mapstruct.*`, `com.mysema.querydsl.*`），需要将其加入排除列表。否则 Phase 2a 会展开这些库的方法调用，浪费 BFS 资源。

#### 2.2 数据容器和工具类排除（规则 #3, #4）

当前排除的类名模式：

- 数据容器：`*DTO`, `*Vo`, `*VO`, `*Entity`, `*Request`, `*Response`
- 工具/配置类：`*Util`, `*Helper`, `*Constants`, `*Config`, `*Properties`, `*Enum`

如果项目使用不同的命名约定（如 `*Model`, `*Form`, `*Param`），需要添加到对应规则中。

#### 2.3 排除规则

以下调用即使匹配噪声规则也不能丢弃，必须保留到终点判定步骤：

- 同类内部方法调用（如 `this.uploadFile()`）
- 项目内任何有源码的类的方法调用
- 类名含 `Client`/`Proxy`/`Template` 的调用（可能是外调终点）

#### 2.4 BFS 控制参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `maxDepth` | 20 | 最大 BFS 深度 |
| `maxNodes` | 500 | 单入口最大节点数 |
| `MAX_FANOUT` | 10 | 单节点最大子节点展开数 |

对于调用层级特别深或扇出特别广的项目，可以在脚本调用参数中调整这些值。

---

## 3. 终点类型规则（Phase 3）

### 配置文件：`rules/endpoint-rules.md`

默认终点类型：

| 类型 | 匹配规则 |
|------|---------|
| DATABASE | 类名含 `Mapper`/`Dao`/`Repository` |
| RMB_EXTERNAL | 类名含 `Client`/`Proxy`，有 `@RmbClient` 注解 |
| HTTP_EXTERNAL | 调用 `RestTemplate`/`FeignClient`/`HttpClient` |
| FILE_WRITE | 调用 `FileOutputStream`/`Files.write` |
| MQ_PUBLISH | 调用 `KafkaTemplate`/`JmsTemplate` |

适配新项目时：
- 如果使用了不同的数据库访问框架（如 `*Mapper` → `*Repository`），需要调整匹配规则
- 如果有新的终点类型（如 Redis 缓存写入 `RedisTemplate`），需要在 `endpoint-rules.md` 和 `prompts/phase2a-discover.md` 中同步添加

---

## 4. RMB 桥接规则（Phase 5）

### 配置文件：`rules/bridge-rules.md`

#### 4.1 发送端/接收端识别

当前基于 Chameleon 框架的 RMB 规则：
- 发送端：`@RmbClient` 注解、`rmbClientProxy.invoke()` 调用
- 接收端：`@RmbController` 注解、Chameleon Flow 接口

#### 4.2 Topic 提取策略

4 级优先级：注解参数 → 方法参数字符串 → 常量引用 → 配置文件占位符

#### 4.3 Topic 变换规则

如果 Topic 名称在发送端和接收端之间存在环境前缀差异（如 `dev-topic-name` vs `topic-name`），需要在 `topicTransform` 中配置正则变换。

#### 4.4 适配要点

- 如果项目使用不同的 RPC 框架（如 Dubbo、gRPC），需要添加对应的桥接规则
- 如果 Topic 命名有特殊变换规则，需要配置 `topicTransform`
- 人类可读文档 `bridge-rules.md` 底部有自定义规则模板

---

## 5. DB Schema 收集（Phase 2）

### 脚本：`scripts/phase1b_db_schema.py`

Phase 2 扫描 Mapper/Dao/Repository 文件，解析 SQL 注解和 XML mapper 文件。

适配新项目时注意：
- 如果项目使用 MyBatis-Plus 但没有 XML mapper，表名可能从 `@TableName` 注解获取
- 如果使用 JPA，表名从 `@Table` 注解获取
- `db-schema-lookup.json` 供 Phase 2a 消费，`db-schema-tables.json` 供人工审查

---

## 6. 业务语义提示词（Phase 6）

### 配置文件：`prompts/phase5-describe.md`

Phase 5 的 LLM 提示词控制如何从源码中提取业务语义。如果项目的业务术语有特殊约定（如特定领域词汇表），可以在提示词中添加上下文说明。

---

## 7. 输出模板（Phase 7）

### 配置文件：`templates/flow-template.md`

控制最终生成的流程文档格式。可以根据团队文档规范调整模板结构。

---

## 适配检查清单

将 flow-trace-java 应用到新 Java 工程时，按以下顺序检查和调整：

1. **入口注解** — 检查项目使用的 Controller/RMB/Job 框架注解是否与 `entry-rules.json` 匹配
2. **Job 基类** — 检查 Job 类继承的框架基类，添加到 `inheritanceMethodMap`
3. **第三方库** — 检查项目依赖列表，将不需要展开的库加入噪声过滤规则
4. **数据访问层** — 确认 Mapper/Dao/Repository 的命名模式和访问方式
5. **RPC 框架** — 确认 RMB/HTTP 外调的实现方式，调整桥接规则
6. **命名约定** — 检查 DTO/VO/Entity 等类的命名后缀，调整噪声排除规则
7. **终点类型** — 检查是否有默认规则未覆盖的终点（如缓存、文件写入等）
8. **Topic 变换** — 检查 RMB Topic 在不同环境间是否有前缀差异
