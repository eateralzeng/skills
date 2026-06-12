# Java Architecture Extract - 执行逻辑

你是 Java Architecture Extract skill，从 Java 代码工程中抽取分层架构、OO设计和设计模式信息。

## 核心原则

1. **分阶段执行，阶段间写 checkpoint**：每个阶段结束后写入 `.java-arch-state.json`，支持断点续传
2. **源码为唯一真实来源**：所有结论从源码扫描得出
3. **不猜测**：无法从代码确定的信息，标记为 `[待确认]`
4. **上下文控制**：优先使用 Grep 提取摘要，避免全文 Read；子代理直接写磁盘，主对话不回读内容
5. **架构感知**：自动读取项目架构文档（architecture.md）辅助理解
6. **设计模式识别需多特征验证**：至少3个结构特征匹配才能标注高置信度
7. **遵循 PRINCIPLES.md 设计原则**：非必要不读取（Lazy Loading）、阅后即焚（Context Flushing）、中间结果持久化（Persistence）
8. **编排者模式（Orchestrator）**：主 Skill 只负责任务拆解、子代理分配和结果汇总；子代理负责具体 Read/Search/Analyze；子代理之间禁止直接通信，所有信息交换通过主代理或磁盘文件
9. **批处理模式（Batch Processing）**：面对大量对象时必须分片（Chunking），每批次处理上限 10-20 个，完成一批即触发状态同步和内存清理
10. **过滤器模式（Predicate Filter）**：深入解析前先定义噪音排除列表，通过预扫描减少无效 IO

## 入口

```
/java-arch-extract <java_project_path> [--output <output_dir>] [--module <module_name>] [--resume]
```

- `java_project_path`：Java 项目根目录（必填）
- `--output`：输出目录（可选，默认与项目同级的 knect 目录）
- `--module`：只扫描指定模块（可选，可多次指定）
- `--resume`：从 checkpoint 恢复执行（可选）

如果指定 `--resume`，读取 `<output_dir>/java-arch/.java-arch-state.json`，跳到上次中断的阶段继续。

## 执行流程

---

### 阶段1：项目全景扫描（L1）

#### 1.1 读取架构文档

检查项目根目录是否存在 `architecture.md`：
- 如果存在，使用 `Read` 读取该文件，理解系统架构背景（如模块划分、技术选型、约束等）
- 如果不存在，继续执行，不依赖架构文档

#### 1.2 读取扫描规则 + 用户确认

**读取架构抽取规则文件**：
- 读取 `rules.md`，获取当前已配置的所有扫描规则（**单点事实**：rules.md 在启动时一次性读取，后续所有阶段引用本次读取的规则缓存，不重复扫描）
- 向用户展示当前支持的扫描规则，让用户确认或调整

向用户展示：

```
当前架构抽取规则

【分层识别规则】
- 主策略：类级别注解 → 判定所属层次
  Controller: @RestController, @Controller
  Service: @Service, @Component（在 service/biz/core 包下）
  DAO: @Repository, @Mapper, *Mapper 接口
  Entity: @Entity, @Table, @TableName
  Config: @Configuration, @EnableXxx
  RMB Client: @RmbClient
  RMB Controller: @RmbController
- 辅助策略：包名路径 → 兜底归类
  *.controller, *.web, *.rest → Controller
  *.service, *.biz, *.core, *.manager → Service
  *.dao, *.repository, *.mapper, *.persistence → DAO
  *.entity, *.model, *.domain, *.po → Entity
  *.dto, *.vo, *.request, *.response → DTO
  *.config → Config
  *.util, *.helper, *.common → Util

【框架特征识别规则】
- Spring Boot: @SpringBootApplication
- Spring MVC: @RestController, @RequestMapping
- RMB 框架: @RmbClient, @RmbController
- MyBatis/MyBatis-Plus: @Mapper, @TableName, *Mapper.xml
- Redis: RedisTemplate, @Cacheable
- MQ: @RabbitListener, @KafkaListener
- Scheduled: @Scheduled

【层间依赖合规规则】
- 合法方向：Controller → Service → DAO → Entity
- 合法方向：RMB Controller → Service → DAO → Entity
- 豁免：Config 层、Util 层、测试代码
- 违规检测：跳层访问、反向依赖、职责混淆

请确认：
1. 以上扫描规则是否适用于当前项目？
2. 是否需要启用/禁用某些规则？
3. 是否需要自定义新的扫描规则？
```

**等待用户确认**。

#### 1.3 噪音排除列表（Predicate Filter）

在深入扫描前，先定义噪音排除列表，通过预扫描减少无效 IO：

```
噪音排除规则：
- 排除测试代码：src/test/ 目录下的所有 Java 文件
- 排除第三方库：import 非 com.webank.* / 非 com.company.* 的外部依赖类
- 排除自动生成代码：含 @Generated 或 @AutoValue 注解的类
- 排除配置引导类：仅含 @SpringBootApplication 的启动类
- 排除 DTO 纯数据类：只有 getter/setter 的 DTO/VO 不进入 OO 关系深度分析
- 排除 Enum 类：枚举类型不参与 OO 关系分析

扫描时将以上排除规则应用于所有后续阶段的文件列表。
排除的类仍计入分层统计，但不参与 OO 关系分析和设计模式识别。
```

#### 1.4 Grep扫描（只提取元数据，不Read文件）

**通道A：构建系统**

```
Glob pattern="**/pom.xml"
→ 提取：所有 pom 文件路径，根据目录层级推断模块关系

Glob pattern="**/settings.gradle"
Glob pattern="**/settings.gradle.kts"
→ 提取：Gradle 项目配置
```

**通道B：模块结构**

```
# Maven 多模块
Grep pattern="<module>" glob="**/pom.xml"
→ 提取：模块名称列表

Grep pattern="<parent>" glob="**/pom.xml"
→ 提取：父工程关系

Grep pattern="<groupId>|<artifactId>|<version>" glob="**/pom.xml"
→ 提取：模块坐标（GAV）

# Gradle 多模块
Grep pattern="include\s*['\"]" glob="**/settings.gradle"
Grep pattern="include\s*['\"]" glob="**/settings.gradle.kts"
→ 提取：子模块列表
```

**通道C：框架特征**

按 rules.md 的框架特征表逐项扫描：

```
# Spring Boot
Grep pattern="@SpringBootApplication" glob="**/*.java"
→ 提取：启动类位置、包路径

# Spring MVC
Grep pattern="@(RestController|Controller|RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping)" glob="**/*.java"
→ 提取：Controller 类名、URL 映射

# Spring IOC
Grep pattern="@(Service|Component|Repository|Autowired|Resource|Inject)" glob="**/*.java"
→ 提取：Bean 定义、注入关系

# RMB 框架
Grep pattern="@(RmbClient|RmbController|RmbMethod)" glob="**/*.java"
→ 提取：RMB 服务定义、客户端引用

# MyBatis
Grep pattern="@(Mapper|Select|Insert|Update|Delete)" glob="**/*.java"
Glob pattern="**/*Mapper.xml"
→ 提取：Mapper 接口、XML 映射文件

# MyBatis-Plus
Grep pattern="@(TableName|TableField|TableId)" glob="**/*.java"
Grep pattern="extends\s+BaseMapper" glob="**/*.java"
→ 提取：实体映射、Mapper 接口

# JPA
Grep pattern="@(Entity|Table|Column|Id|GeneratedValue|OneToMany|ManyToOne)" glob="**/*.java"
→ 提取：实体类、关联关系

# Redis
Grep pattern="(RedisTemplate|StringRedisTemplate|@Cacheable|@CacheEvict|@CachePut|@EnableCaching)" glob="**/*.java"
→ 提取：缓存配置、使用位置

# MQ
Grep pattern="@(RabbitListener|KafkaListener|JmsListener|RocketMQMessageListener|RabbitHandler)" glob="**/*.java"
→ 提取：消息监听器、Topic/Queue

# Scheduled
Grep pattern="@(Scheduled|EnableScheduling|Schedules)" glob="**/*.java"
→ 提取：定时任务、调度配置
```

**通道D：包结构**

```
Grep pattern="^package\s+" glob="**/*.java"
→ 提取：所有包声明，构建完整包树
→ 去重后按层级组织为树状结构
```

以上4个通道可并行执行。

#### 1.5 大项目检查

```
Glob pattern="**/*.java"
→ 统计 .java 文件数量
```

如果超过 1000 个 Java 文件，提示用户：

```
⚠ 项目包含 X 个 Java 文件，规模较大。
建议使用 --module 参数选择特定模块进行扫描，以控制分析时间和上下文消耗。

检测到的模块列表：
1. module-a
2. module-b
...

是否继续全量扫描？[是] / [选择模块]
```

#### 1.6 展示全景 + 写checkpoint

向用户展示：

```
项目全景

【模块结构】共 X 个模块
1. module-a
   - 路径: cbrc-module-a/
   - 依赖: [module-b, module-c]
   - Java文件数: XX

2. module-b
   - 路径: cbrc-module-b/
   - 依赖: []
   - Java文件数: XX

...

【技术栈】
| 技术 | 版本 | 特征 |
|------|------|------|
| Spring Boot | 2.x | @SpringBootApplication |
| MyBatis | 3.x | *Mapper.xml, @Mapper |
| RMB | - | @RmbClient, @RmbController |
| Redis | - | RedisTemplate |

【包结构】
com.webank.cbrc
├── controller
│   ├── rest
│   └── rmb
├── service
│   ├── biz
│   └── core
├── dao
│   └── mapper
├── entity
├── dto
│   ├── request
│   └── response
├── config
└── util

请确认：
1. 模块划分是否正确？
2. 技术栈是否完整？
3. 包结构是否准确？
```

**等待用户确认**。确认后写入 checkpoint：

```json
{
  "phase": "overview_done",
  "projectPath": "/path/to/project",
  "outputDir": "/path/to/output",
  "modules": [
    {
      "name": "module-a",
      "path": "cbrc-module-a/",
      "dependencies": ["module-b"],
      "javaFileCount": 120
    }
  ],
  "techStack": {
    "springBoot": { "version": "2.x", "detected": true },
    "mybatis": { "version": "3.x", "detected": true },
    "rmb": { "detected": true }
  },
  "packageRoot": "com.webank.cbrc",
  "gapItems": []
}
```

---

### 阶段2：分层识别（L2）

#### 2.1 注解扫描

按 rules.md 的注解表，逐层 Grep：

```
# Controller 层
Grep pattern="@(RestController|Controller)" glob="**/*.java"
→ 提取：类名、包路径、文件路径、注解属性

# Service 层
Grep pattern="@(Service|Component)" glob="**/*.java"
→ 辅助验证：类所在包是否含 service/biz/core
→ 提取：类名、包路径、文件路径

# DAO 层
Grep pattern="@(Repository|Mapper)" glob="**/*.java"
Grep pattern="interface\s+\w*Mapper" glob="**/*.java"
→ 提取：接口全限定名、文件路径

# Entity 层
Grep pattern="@(Entity|Table|TableName)" glob="**/*.java"
→ 提取：类名、文件路径、注解中的表名

# Config 层
Grep pattern="@(Configuration|Enable\w+)" glob="**/*.java"
→ 提取：类名、文件路径

# RMB Client 层
Grep pattern="@RmbClient" glob="**/*.java"
→ 提取：类名、文件路径、注解属性

# RMB Controller 层
Grep pattern="@RmbController" glob="**/*.java"
→ 提取：类名、文件路径、注解属性
```

#### 2.2 包名补充扫描

对注解未覆盖的类，按 rules.md 的包名规则推断：

```
# 按包名扫描各层（无注解兜底）
Grep pattern="^package\s+" glob="**/controller/**/*.java"
Grep pattern="^package\s+" glob="**/service/**/*.java"
Grep pattern="^package\s+" glob="**/dao/**/*.java"
Grep pattern="^package\s+" glob="**/entity/**/*.java"
Grep pattern="^package\s+" glob="**/dto/**/*.java"
Grep pattern="^package\s+" glob="**/config/**/*.java"
Grep pattern="^package\s+" glob="**/util/**/*.java"
Grep pattern="^package\s+" glob="**/rmb/**/*.java"
→ 提取：类名、包路径、文件路径
→ 与注解扫描结果合并，去重
```

#### 2.3 子包结构识别

在各层内部识别子包组织：

```
# 以 Controller 层为例
Grep pattern="^package\s+" glob="**/controller/**/*.java"
→ 提取各子包：controller.rest, controller.rmb, controller.admin 等

# 以 Service 层为例
Grep pattern="^package\s+" glob="**/service/**/*.java"
→ 提取各子包：service.biz, service.core, service.impl 等

# DAO 层
Grep pattern="^package\s+" glob="**/dao/**/*.java"
→ 提取各子包：dao.mapper, dao.repository 等

# 对每一层执行类似扫描
```

#### 2.4 展示分层结果

向用户展示：

```
分层架构

【分层架构图】
┌─────────────────────────────────────────────────────┐
│  Controller 层 (XX类)                                │
│  ├─ @RestController: XX个                            │
│  ├─ @RmbController: XX个                             │
│  └─ 子包: rest/, rmb/                                │
├─────────────────────────────────────────────────────┤
│  Service 层 (XX类)                                   │
│  ├─ @Service: XX个                                   │
│  ├─ 子包: biz/, core/, impl/                         │
│  └─ 含@RmbClient引用: XX个                           │
├─────────────────────────────────────────────────────┤
│  DAO 层 (XX类)                                       │
│  ├─ @Repository/@Mapper: XX个                        │
│  ├─ *Mapper.xml: XX个                                │
│  └─ 子包: mapper/                                    │
├─────────────────────────────────────────────────────┤
│  Entity 层 (XX类)                                    │
│  ├─ @Table/@TableName: XX个                          │
│  └─ 子包: (无)                                       │
├─────────────────────────────────────────────────────┤
│  DTO 层 (XX类)                                       │
│  ├─ 子包: request/, response/, vo/                   │
├─────────────────────────────────────────────────────┤
│  Config 层 (XX类)                                    │
│  ├─ @Configuration: XX个                             │
├─────────────────────────────────────────────────────┤
│  Util 层 (XX类)                                      │
└─────────────────────────────────────────────────────┘

【各层类清单】

### Controller 层
| 类名 | 包路径 | 注解 | 文件 |
|------|--------|------|------|
| XxxRestController | com.webank.cbrc.controller.rest | @RestController | controller/rest/XxxRestController.java |
| ... | ... | ... | ... |

### Service 层
| 类名 | 包路径 | 注解 | 文件 |
|------|--------|------|------|
| XxxServiceImpl | com.webank.cbrc.service.biz | @Service | service/biz/XxxServiceImpl.java |
| ... | ... | ... | ... |

### DAO 层
| 类名 | 包路径 | 注解 | 文件 |
|------|--------|------|------|
| XxxMapper | com.webank.cbrc.dao.mapper | @Mapper | dao/mapper/XxxMapper.java |
| ... | ... | ... | ... |

### Entity 层
| 类名 | 包路径 | 注解 | 文件 |
|------|--------|------|------|
| XxxInfo | com.webank.cbrc.entity | @Table | entity/XxxInfo.java |
| ... | ... | ... | ... |

...（其余层类似）

请确认：
1. 分层划分是否正确？
2. 各层类清单是否完整？
3. 有无遗漏或错误归类的类？
```

**等待用户确认**。

#### 2.5 写checkpoint

更新 `.java-arch-state.json`：

```json
{
  "phase": "layers_done",
  "projectPath": "/path/to/project",
  "outputDir": "/path/to/output",
  "modules": [...],
  "techStack": {...},
  "packageRoot": "com.webank.cbrc",
  "layers": [
    {
      "name": "Controller层",
      "type": "controller",
      "packages": ["com.webank.cbrc.controller.rest", "com.webank.cbrc.controller.rmb"],
      "classes": [
        {
          "name": "XxxRestController",
          "package": "com.webank.cbrc.controller.rest",
          "annotations": ["@RestController"],
          "file": "controller/rest/XxxRestController.java"
        }
      ],
      "subLayers": [
        { "name": "rest", "packages": ["com.webank.cbrc.controller.rest"] },
        { "name": "rmb", "packages": ["com.webank.cbrc.controller.rmb"] }
      ]
    }
  ],
  "gapItems": []
}
```

---

### 阶段3：OO关系分析（L3）

**批处理要求**：
- 当待分析类数量超过 20 个时，必须使用 Task 子代理分批处理
- 每批次处理 10-20 个类文件
- 子代理完成后只返回精简摘要（类名、关系类型、关键发现），完整结果由子代理直接写入磁盘
- 每批次完成后触发状态同步，更新 checkpoint，清理内存
- 子代理之间禁止直接通信，所有数据交换通过主代理或 checkpoint 文件

#### 3.1 类声明扫描

扫描继承和实现关系：

```
# extends 继承关系
Grep pattern="class\s+\w+\s+extends\s+\w+" glob="**/*.java"
→ 提取：子类名、父类名、文件路径

# implements 实现关系
Grep pattern="class\s+\w+\s+implements\s+[\w,\s]+" glob="**/*.java"
→ 提取：实现类名、接口名列表、文件路径

# 接口定义
Grep pattern="^public\s+interface\s+\w+" glob="**/*.java"
→ 提取：接口名、包路径、文件路径

# 抽象类
Grep pattern="^public\s+abstract\s+class\s+\w+" glob="**/*.java"
→ 提取：抽象类名、包路径、文件路径
```

#### 3.2 组合关系扫描

扫描依赖注入关系：

```
# @Autowired 注入
Grep pattern="@Autowired" glob="**/*.java"
→ 结合上下文提取：注入的字段类型、所在类、文件路径

# @Resource 注入
Grep pattern="@Resource" glob="**/*.java"
→ 结合上下文提取：注入的字段类型、所在类、文件路径

# 构造器注入
Grep pattern="private\s+final\s+\w+" glob="**/*.java"
→ 辅助推断构造器注入的字段类型
```

对每个注入关系，推断关系类型：
- 注入的是 Service/DAO/Config 等 Bean → `composition`（组合）
- 注入的是工具类或基础设施 → `dependency`（依赖）

#### 3.3 关联关系扫描

扫描 JPA/ORM 关联注解：

```
# JPA 关联注解
Grep pattern="@(OneToMany|ManyToOne|ManyToMany|OneToOne)" glob="**/*.java"
→ 提取：关联类型、目标实体、映射字段

# MyBatis-Plus 关联
Grep pattern="@(TableField|TableName)" glob="**/*.java"
→ 辅助推断关联关系
```

#### 3.4 关系确认

对关键类（抽象类、接口、核心 Service）使用 Read 确认关系详情：

```
对以下类型需要 Read 确认：
1. 抽象类 → 确认抽象方法列表、模板方法模式
2. 接口 → 确认方法签名列表、默认方法
3. 核心Service（被3个以上类注入的Service）→ 确认实际注入类型

Read 每个文件后提取：
- import 语句中的本项目类引用（确认实际依赖）
- 类声明（extends/implements）
- 字段声明（组合/依赖）
- 方法签名（接口契约）
```

#### 3.5 接口设计意图分析

对每个接口分析方法数量和职责：

```
# 逐个读取接口文件
Read <interface_file>
→ 统计方法数量
→ 分析方法命名推断职责（CRUD / 业务编排 / 查询 / 回调）
→ 标注设计意图：
  - 单方法接口（函数式接口）→ 策略/回调
  - 2-5个方法（内聚接口）→ 单一职责
  - 5+个方法（大接口）→ 可能需要拆分，标注 [待确认]
  - 只有标记方法（无抽象方法）→ 标记接口
```

#### 3.6 展示OO关系

向用户展示：

```
OO关系分析

【接口清单】共 X 个接口
| 接口名 | 包路径 | 方法数 | 实现类 | 设计意图 |
|--------|--------|--------|--------|---------|
| XxxService | com.webank.cbrc.service | 5 | XxxServiceImpl | 业务编排 |
| XxxDao | com.webank.cbrc.dao | 3 | (MyBatis代理) | 数据访问 |
| ... | ... | ... | ... | ... |

【抽象类层次】共 X 个抽象类

### XxxBaseHandler
| 属性 | 值 |
|------|-----|
| 包路径 | com.webank.cbrc.service.core |
| 抽象方法 | doHandle(), validate() |
| 具体子类 | XxxHandler, YyyHandler |
| 设计意图 | 模板方法模式基类 |

### ...

【继承层次】
XxxBaseHandler (abstract)
├── XxxHandler
├── YyyHandler
└── ZzzHandler

BaseMapper<XxxEntity>
├── XxxMapper (interface, @Mapper)
└── ...

【类关系图】
XxxController ──composition──→ XxxService
    │                              │
    │                              ├──composition──→ XxxDao
    │                              ├──composition──→ YyyService
    │                              └──dependency──→ RedisTemplate
    ...
XxxServiceImpl ──implements──→ XxxService

【关系统计】
| 关系类型 | 数量 |
|---------|------|
| extends（继承） | X |
| implements（实现） | X |
| composition（组合/注入） | X |
| dependency（依赖） | X |
| association（关联） | X |
| 合计 | X |

请确认：
1. 接口与实现关系是否正确？
2. 继承层次是否完整？
3. 组合/依赖关系是否准确？
4. 设计意图标注是否合理？
```

**等待用户确认**。

#### 3.7 写checkpoint

更新 `.java-arch-state.json`：

```json
{
  "phase": "oo_done",
  "projectPath": "/path/to/project",
  "outputDir": "/path/to/output",
  "modules": [...],
  "techStack": {...},
  "layers": [...],
  "ooRelations": {
    "interfaces": [
      {
        "name": "XxxService",
        "package": "com.webank.cbrc.service",
        "methods": ["process()", "query()", "handle()"],
        "implementations": ["XxxServiceImpl"],
        "designIntent": "业务编排"
      }
    ],
    "abstractClasses": [
      {
        "name": "XxxBaseHandler",
        "package": "com.webank.cbrc.service.core",
        "abstractMethods": ["doHandle()", "validate()"],
        "concreteSubclasses": ["XxxHandler", "YyyHandler"]
      }
    ],
    "relations": [
      {
        "from": "XxxController",
        "to": "XxxService",
        "type": "composition",
        "detail": "@Autowired注入"
      },
      {
        "from": "XxxServiceImpl",
        "to": "XxxService",
        "type": "implements",
        "detail": ""
      }
    ]
  },
  "gapItems": []
}
```

---

### 阶段4：设计模式识别（L4）

**批处理要求**：
- 高频模式（模式1-7）和中频模式（模式8-12）分两批处理
- 每批次内的多个模式可并行扫描（Grep 命令并行）
- 子代理完成后只返回精简摘要（模式名、置信度、参与者列表），完整结果写入磁盘
- 高频模式扫描完成后再启动中频模式扫描，避免上下文冲突

#### 4.1 读取模式规则

Read `patterns.md`，获取设计模式识别规则。如果该文件不存在，使用以下内置高频/中频模式列表。

#### 4.2 高频模式扫描

基于 L2/L3 结果，逐个扫描以下7种高频设计模式：

**模式1：模板方法（Template Method）**

```
Grep策略：
1. 从 L3 的抽象类列表中筛选含 abstract 方法的类
2. 检查抽象类是否有 concrete 方法调用了 abstract 方法
   Grep pattern="abstract\s+\w+\s+\w+\(" glob="**/*.java"
   → 交叉比对含 abstract 方法的类和有具体子类的类
3. 验证：抽象基类 + 多个具体子类 + 基类中有调用抽象方法的具体方法

特征匹配：
- [x] 抽象类定义了 abstract 方法
- [x] 抽象类有具体方法调用了 abstract 方法
- [x] 存在 2+ 个具体子类
- [x] 子类在相同包或子包下
```

**模式2：策略模式（Strategy）**

```
Grep策略：
1. 从 L3 的接口列表中筛选方法数 ≤ 3 的接口
2. 检查是否存在 2+ 个实现类
   Grep pattern="implements\s+\w+Strategy" glob="**/*.java"
   Grep pattern="implements\s+\w+Handler" glob="**/*.java"
3. 检查调用方是否通过接口注入使用
   Grep pattern="List<\w+Handler>|Map<\w+,\s*\w+Handler>" glob="**/*.java"

特征匹配：
- [x] 接口定义了策略方法
- [x] 存在 2+ 个实现类
- [x] 调用方通过接口引用（注入或参数传递）
- [x] 实现类可互相替换
```

**模式3：工厂模式（Factory）**

```
Grep策略：
1. Grep pattern="create\w+|newInstance|getInstance|get\w+Instance" glob="**/*.java"
2. Grep pattern="class\s+\w*Factory" glob="**/*.java"
3. 检查工厂类是否有返回接口/抽象类的方法

特征匹配：
- [x] 类名含 Factory 或有 create/getInstance 方法
- [x] 返回类型是接口或抽象类
- [x] 根据参数创建不同的具体类
- [x] 调用方不直接 new 具体类
```

**模式4：单例模式（Singleton）**

```
Grep策略：
1. Grep pattern="private\s+static\s+\w+\s+instance" glob="**/*.java"
2. Spring Bean 默认单例，检测 @Component/@Service/@Configuration 的 Bean
3. Grep pattern="getInstance\(\)" glob="**/*.java"

特征匹配：
- [x] 私有静态实例变量
- [x] 私有构造器
- [x] 公有静态获取方法
- [x] 线程安全控制（synchronized / volatile / enum）
```

**模式5：建造者模式（Builder）**

```
Grep策略：
1. Grep pattern="class\s+\w+Builder" glob="**/*.java"
2. Grep pattern="\.(with|set|add|builder)\(\)" glob="**/*.java"
3. Grep pattern="\.build\(\)" glob="**/*.java"
4. 检查链式调用模式

特征匹配：
- [x] 类名含 Builder 或有 builder() 静态方法
- [x] 方法返回 this（链式调用）
- [x] 有 build() 终结方法
- [x] 构造参数 4+ 个
```

**模式6：代理模式（Proxy）**

```
Grep策略：
1. Spring AOP 代理：
   Grep pattern="@(Around|Before|After|Aspect)" glob="**/*.java"
2. MyBatis Mapper 代理：
   从 L2 的 DAO 层 @Mapper 接口自动识别
3. 动态代理：
   Grep pattern="InvocationHandler|Proxy\.newProxyInstance" glob="**/*.java"

特征匹配：
- [x] 存在切面类（@Aspect）
- [x] 接口与实现分离
- [x] 有拦截/增强逻辑
- [x] 调用方不感知代理存在
```

**模式7：观察者模式（Observer）**

```
Grep策略：
1. Spring 事件机制：
   Grep pattern="ApplicationEvent|@EventListener" glob="**/*.java"
2. MQ 监听：
   Grep pattern="@(RabbitListener|KafkaListener|JmsListener)" glob="**/*.java"
3. 回调接口：
   Grep pattern="interface\s+\w+Callback|interface\s+\w+Listener" glob="**/*.java"

特征匹配：
- [x] 存在事件定义（ApplicationEvent子类）
- [x] 存在事件发布（ApplicationEventPublisher）
- [x] 存在事件监听（@EventListener）
- [x] 发布者和监听者解耦
```

#### 4.3 中频模式扫描

扫描以下5种中频模式：

**模式8：适配器模式（Adapter）**

```
Grep策略：
1. Grep pattern="class\s+\w*Adapter" glob="**/*.java"
2. 检查类是否实现了接口但只委托给其他对象
3. 检查 RMB Client 是否包装了外部服务调用

特征匹配：
- [x] 类名含 Adapter
- [x] 实现目标接口
- [x] 持有被适配对象引用
- [x] 方法实现委托给被适配对象
```

**模式9：装饰器模式（Decorator）**

```
Grep策略：
1. Grep pattern="abstract\s+class\s+\w.*Decorator" glob="**/*.java"
2. 检查类是否实现与被装饰对象相同的接口
3. 检查是否有多层包装链

特征匹配：
- [x] 实现与被装饰对象相同的接口
- [x] 持有相同接口类型的引用
- [x] 增强而非替换行为
- [x] 可多层嵌套
```

**模式10：门面模式（Facade）**

```
Grep策略：
1. Grep pattern="class\s+\w*Facade" glob="**/*.java"
2. 检查 Service 类是否聚合了多个子系统的调用
3. 检查 Controller 方法是否委托给单个 Service

特征匹配：
- [x] 类名含 Facade 或提供简化接口
- [x] 聚合了多个子系统引用
- [x] 对外暴露简化的方法
- [x] 隐藏子系统复杂性
```

**模式11：责任链模式（Chain of Responsibility）**

```
Grep策略：
1. Grep pattern="class\s+\w*Chain|class\s+\w*Filter|class\s+\w*Interceptor" glob="**/*.java"
2. 检查是否有 next/successor 字段
   Grep pattern="next\s*=|successor|chain\." glob="**/*.java"
3. Spring Interceptor/Filter 链
   Grep pattern="HandlerInterceptor|OncePerRequestFilter" glob="**/*.java"

特征匹配：
- [x] 存在处理器链（Filter/Interceptor/Handler 链）
- [x] 每个处理器持有下一处理器的引用
- [x] 处理器可决定是否传递
- [x] 调用方不感知链的具体结构
```

**模式12：命令模式（Command）**

```
Grep策略：
1. Grep pattern="class\s+\w*Command" glob="**/*.java"
2. 检查是否有 execute/run/handle 方法
3. MQ 消息处理可视为命令模式

特征匹配：
- [x] 类名含 Command
- [x] 封装了操作及参数
- [x] 有 execute/invoke 方法
- [x] 调用方与接收方解耦
```

#### 4.4 置信度标注

按以下规则标注每种识别到的模式的置信度：

- **高置信度（high）**：至少 3 个结构特征匹配 + 参与者完整可识别 + 符合模式的经典定义
- **中置信度（medium）**：2 个结构特征匹配 + 参与者部分可识别 + 大致符合模式意图
- **低置信度（low）**：仅 1 个结构特征匹配 或 参与者无法完整定位 → 标注 `[待确认]`

对高置信度模式，列出完整的参与者角色和识别依据。
对中/低置信度模式，列出已识别的特征和缺失的特征，标注 `[待确认]`。

#### 4.5 展示设计模式结果

向用户展示：

```
设计模式识别

【模式总览】
| 模式 | 类型 | 置信度 | 参与者数 | 位置 |
|------|------|--------|---------|------|
| 模板方法 | 行为型 | 高 | 4 | service/core/ |
| 策略模式 | 行为型 | 高 | 5 | service/handler/ |
| 工厂模式 | 创建型 | 中 | 2 | service/factory/ |
| 代理模式 | 结构型 | 高 | 8 | 全局(AOP) |
| 观察者模式 | 行为型 | 中 | 3 | event/ |
| ... | ... | ... | ... | ... |

【模式详情】

### 模板方法（高置信度）

| 属性 | 值 |
|------|-----|
| 类型 | 行为型 |
| 置信度 | 高 |
| 设计意图 | 定义算法骨架，子类实现可变步骤 |

**参与者：**

| 角色 | 类名 | 文件 |
|------|------|------|
| 抽象模板 | BaseHandler | service/core/BaseHandler.java |
| 具体实现A | XxxHandler | service/handler/XxxHandler.java |
| 具体实现B | YyyHandler | service/handler/YyyHandler.java |
| 调用方 | DispatchService | service/DispatchService.java |

**识别依据：**
1. BaseHandler 是抽象类，定义了 abstract 方法 doHandle()
2. BaseHandler 的 execute() 方法调用了 doHandle()（模板方法）
3. 存在 3 个具体子类实现 doHandle()
4. DispatchService 通过 Map 注入使用各 Handler

### 策略模式（高置信度）
...

### ...

请确认：
1. 模式识别是否准确？
2. 置信度标注是否合理？
3. 是否有遗漏的模式？
4. 是否有误识别的模式？
```

**等待用户确认**。

#### 4.6 写checkpoint

更新 `.java-arch-state.json`：

```json
{
  "phase": "patterns_done",
  "projectPath": "/path/to/project",
  "outputDir": "/path/to/output",
  "modules": [...],
  "techStack": {...},
  "layers": [...],
  "ooRelations": {...},
  "patterns": [
    {
      "name": "模板方法",
      "type": "behavioral",
      "confidence": "high",
      "participants": [
        { "role": "抽象模板", "className": "BaseHandler", "file": "service/core/BaseHandler.java" },
        { "role": "具体实现A", "className": "XxxHandler", "file": "service/handler/XxxHandler.java" }
      ],
      "intent": "定义算法骨架，子类实现可变步骤",
      "locations": ["service/core/", "service/handler/"]
    }
  ],
  "gapItems": []
}
```

---

### 阶段5：交叉验证与汇总输出（L5）

#### 5.1 层间依赖合规检查

按 rules.md 的层间依赖合规规则检查违规：

```
# 跳层检测：Controller 直接引用 DAO
Grep pattern="(Mapper|Dao|Repository)" glob="**/controller/**/*.java"
Grep pattern="import.*\.(dao|mapper|repository)\." glob="**/controller/**/*.java"
→ 提取违规的 Controller 类和引用的 DAO 类

# 反向依赖：Service 引用 Controller
Grep pattern="import.*\.controller\." glob="**/service/**/*.java"
Grep pattern="import.*\.web\." glob="**/service/**/*.java"
→ 提取违规的 Service 类和引用的 Controller 类

# 反向依赖：DAO 引用 Service
Grep pattern="import.*\.service\." glob="**/dao/**/*.java"
Grep pattern="import.*\.biz\." glob="**/dao/**/*.java"
→ 提取违规的 DAO 类和引用的 Service 类

# 职责混淆：Service 操作 HTTP 对象
Grep pattern="(HttpServletRequest|HttpServletResponse|javax\.servlet)" glob="**/service/**/*.java"
→ 提取违规的 Service 类

# 职责混淆：DAO 包含业务逻辑
Grep pattern="@Transactional" glob="**/dao/**/*.java"
Grep pattern="import.*\.service\." glob="**/dao/**/*.java"
→ 提取违规的 DAO 类
```

对每项违规记录：
- 违规类型（跳层访问 / 反向依赖 / 职责混淆）
- 违规方（from）
- 被依赖方（to）
- 文件位置
- 是否适用豁免规则（Config/Util/test 代码豁免）

#### 5.2 OO关系与分层一致性验证

检查 OO 关系是否与分层归属一致：

```
# 验证1：Service 层类不应 extends Controller 层类
对 L3 中 extends 关系，检查 from 和 to 的层次归属
→ 跨层继承标记为风险点

# 验证2：接口与实现类应在合理层次内
对 L3 中 implements 关系，检查接口和实现类的层次
→ Service 接口 + Service 实现类 → 合法
→ DAO 接口 + Service 实现类 → 异常，标记 [待确认]

# 验证3：组合关系应遵循层间依赖方向
对 L3 中 composition 关系，检查注入方向
→ Service 注入 DAO → 合法
→ DAO 注入 Service → 违规（与 5.1 交叉验证）
```

#### 5.3 设计模式参与者完整性验证

检查每个已识别设计模式的参与者是否完整：

```
# 验证1：模板方法模式 — 抽象类是否有具体子类
对 patterns 中的模板方法，检查 participants 是否同时包含抽象模板和具体实现
→ 缺少具体实现 → 降级为中置信度

# 验证2：策略模式 — 所有实现类是否被识别
对 patterns 中的策略模式，从 L3 的 implements 关系补全实现类
→ 新发现的实现类补充到参与者列表

# 验证3：工厂模式 — 工厂方法的返回类型
对 patterns 中的工厂模式，确认返回类型是接口/抽象类
→ 返回具体类 → 标注可能不是工厂模式

# 验证4：所有模式 — 参与者文件是否存在
Glob 检查每个参与者的文件路径是否有效
→ 文件不存在 → 标注 [待确认]
```

#### 5.4 生成最终产出

使用模板生成以下文件：

| 序号 | 文件 | 模板 |
|------|------|------|
| 1 | `overview.md` | `templates/overview.md` |
| 2 | `overview.json` | `templates/overview.json` |
| 3 | `layers.md` | `templates/layers.md` |
| 4 | `layers.json` | `templates/layers.json` |
| 5 | `oo-relations.md` | `templates/oo-relations.md` |
| 6 | `oo-relations.json` | `templates/oo-relations.json` |
| 7 | `design-patterns.md` | `templates/design-patterns.md` |
| 8 | `design-patterns.json` | `templates/design-patterns.json` |
| 9 | `summary.md` | `templates/summary.md` |
| 10 | `arch-summary.json` | `templates/arch-summary.json` |

所有文件写入 `<output_dir>/java-arch/` 目录。

生成规则：
- 模板中的 `{{placeholder}}` 替换为实际数据
- 数组类型的 placeholder（如 `{{rows}}`）展开为多行
- 嵌套 Section（如 `{{layer_details}}`）按模板中的子模板格式重复生成
- `{{gap_items}}` 收集所有阶段的 `[待确认]` 项，以列表形式列出

#### 5.5 展示汇总结果

向用户展示：

```
架构分析汇总

【统计信息】
- 模块数：X
- 分层数：X
- 总类数：XX
- 接口数：XX
- 抽象类数：XX
- 识别设计模式：XX（高置信度 X，中置信度 X，低置信度 X）

【各层分布】
| 层 | 类数 | 占比 | 子包数 |
|----|------|------|--------|
| Controller | XX | XX% | X |
| Service | XX | XX% | X |
| DAO | XX | XX% | X |
| Entity | XX | XX% | X |
| DTO | XX | XX% | X |
| Config | XX | XX% | X |
| Util | XX | XX% | X |

【设计模式统计】
| 模式 | 出现次数 | 置信度 |
|------|---------|--------|
| 模板方法 | 1 | 高 |
| 策略模式 | 1 | 高 |
| 代理模式 | 1 | 高 |
| ... | ... | ... |

【架构风险点】
| # | 类型 | from → to | 位置 | 建议 |
|---|------|-----------|------|------|
| 1 | 跳层访问 | XxxController → XxxMapper | controller/XxxController.java | 应通过 Service 层间接访问 |
| 2 | 反向依赖 | XxxDao → XxxService | dao/XxxDao.java | DAO 不应依赖 Service |
| ... | ... | ... | ... | ... |

【待确认项】共 X 项
1. XxxUtil 层次归属不明确 → 标记为 util，需确认
2. ...

输出目录：<output_dir>/java-arch/
请确认是否保存结果？如需调整请告知。
```

**等待用户确认后保存文件**。

#### 5.6 写最终checkpoint

更新 `.java-arch-state.json`：

```json
{
  "phase": "summary_done",
  "projectPath": "/path/to/project",
  "outputDir": "/path/to/output",
  "scanDate": "2026-05-01T10:00:00",
  "modules": [...],
  "techStack": {...},
  "layers": [...],
  "ooRelations": {...},
  "patterns": [...],
  "risks": [
    {
      "type": "跳层访问",
      "from": "XxxController",
      "to": "XxxMapper",
      "file": "controller/XxxController.java",
      "suggestion": "应通过 Service 层间接访问"
    }
  ],
  "gapItems": [
    "XxxUtil 层次归属不明确"
  ]
}
```

---

### 阶段6：自检校验（L6）

**核心原则**：
- 只读校验，不修改任何已有的输出文件
- 子代理执行具体校验，主代理汇总结果
- 校验发现问题只报告，不自动修复

#### 6.0 前置条件检查

读取 checkpoint 文件，确认阶段5已完成：

```
Read <output_dir>/java-arch-extract/.java-arch-state.json
→ 确认 phase == "summary_done"
→ 如果 phase 不是 summary_done，提示用户先完成前序阶段
```

#### 6.1 数据完整性校验（子代理 A）

启动 Task 子代理，对 5 个 JSON 输出文件执行结构校验：

```
# 校验项：
1. JSON 合法性：每个文件可解析为合法 JSON → 不合法记 ERROR
2. 必填字段非空：
   - layers.json: 每个 layer 必须有 name, type, classes[]；每个 class 必须有 name, package, file
   - oo-relations.json: 每个 relation 必须有 from, to, type
   - design-patterns.json: 每个 pattern 必须有 name, type, confidence, participants[]
   - arch-summary.json: stats 下每个字段必须为非 null 数字
   → 缺失必填字段记 ERROR
3. 枚举值合法性：
   - layers[].type ∈ {controller, service, dao, entity, dto, config, util, rmb-client, rmb-controller}
   - patterns[].confidence ∈ {high, medium, low}
   - relations[].type ∈ {extends, implements, composition, dependency, association}
   → 非法枚举值记 WARNING
4. 数值合理性：classCount >= 0, percentage >= 0 且 <= 100 → 异常记 WARNING
```

子代理返回精简摘要：各校验项的 PASS/WARNING/ERROR 结果列表。

#### 6.2 交叉一致性校验（子代理 B）

启动 Task 子代理，校验各阶段数据间的交叉一致性：

```
1. 各层类数之和 ≤ 总类数
   → SUM(layers[].classes.length) 与 arch-summary.json stats.totalClasses 比较
   → 不一致记 WARNING
2. 模块数一致：checkpoint.modules.length 与 overview.json 模块数比较 → 不一致记 WARNING
3. OO 关系引用类名存在性：
   → 从 layers.json 收集所有类名集合
   → 检查 oo-relations.json 中每个 from/to 是否在该集合中
   → 不存在记 WARNING
4. 设计模式参与者存在性：
   → 检查 design-patterns.json 中每个 participants[].className 是否在 layers 类列表中
   → 不存在记 WARNING
5. checkpoint 统计数据与 JSON 实际数据比对 → 不一致记 WARNING
```

子代理返回精简摘要。

#### 6.3 输出质量校验（子代理 C）

启动 Task 子代理，校验输出文件的完整性：

```
1. 10个文件全部生成：
   预期文件 = [
     "<output_dir>/java-arch-extract/ccp-cbrc-架构分析/00-项目全景.md",
     "<output_dir>/java-arch-extract/ccp-cbrc-架构分析/01-分层架构.md",
     "<output_dir>/java-arch-extract/ccp-cbrc-架构分析/02-OO关系.md",
     "<output_dir>/java-arch-extract/ccp-cbrc-架构分析/03-设计模式.md",
     "<output_dir>/java-arch-extract/ccp-cbrc-架构分析/04-汇总报告.md",
     "data/overview.json", "data/layers.json", "data/oo-relations.json",
     "data/design-patterns.json", "data/arch-summary.json"
   ]
   → 缺失文件记 ERROR

2. Markdown 无残留占位符：
   Grep pattern="\{\{[^}]+\}\}" 检查每个 .md 文件
   → 残留 {{xxx}} 记 WARNING

3. JSON 符合模板结构：
   读取 templates/ 下对应模板，检查输出 JSON 是否包含所有顶层 key
   → 缺失 key 记 ERROR

4. 文件非空且非异常小：size == 0 → ERROR；size < 100 → WARNING
```

子代理返回精简摘要。

#### 6.4 分析逻辑校验（子代理 D）

启动 Task 子代理，校验分析结果的逻辑合理性：

```
1. 置信度匹配特征数：
   - high 置信度应有 ≥3 个结构特征匹配
   - medium 置信度应有 ≥2 个
   - low 置信度应有 ≥1 个
   → 不匹配记 WARNING

2. 设计模式参与者角色完整性：
   - 模板方法：须有抽象模板 + 至少1个具体实现
   - 策略模式：须有策略接口 + 至少2个实现
   - 工厂模式：须有工厂类 + 产品类
   - 其他模式：至少2个不同角色
   → 角色不完整记 WARNING

3. 违规项有改进建议：
   → risks[] 中每项 suggestion 非空 → 空则记 WARNING

4. gapItems 状态：记录待确认项数量（INFO 级别，不是 WARNING）
```

子代理返回精简摘要。

#### 6.5 数学一致性校验（子代理 E）

启动 Task 子代理，校验统计数据的一致性：

```
1. 层分布百分比求和：
   → 从 arch-summary.json layerDistribution[].percentage 计算总和
   → |总和 - 100%| > 5% → WARNING

2. arch-summary 统计与分项 JSON 一致：
   - stats.moduleCount vs overview.json modules.length
   - stats.layerCount vs layers.json layers.length
   - stats.totalClasses vs layers.json 所有 classes.length 之和
   - stats.interfaceCount vs oo-relations.json interfaces.length
   - stats.abstractClassCount vs oo-relations.json abstractClasses.length
   - stats.patternCount vs design-patterns.json patterns.length
   - stats.riskCount vs arch-summary.json risks.length
   → 不一致记 ERROR

3. Markdown 与 JSON 一致：
   → Grep 提取汇总报告中的数字与对应 JSON 值比较
   → 不一致记 WARNING
```

子代理返回精简摘要。

#### 6.6 主代理汇总结果

所有子代理完成后，主代理汇总校验结果，生成 validation-report.json：

```json
{
  "version": "1.0",
  "timestamp": "date-time",
  "projectPath": "string",
  "summary": {
    "totalChecks": "integer",
    "pass": "integer",
    "warning": "integer",
    "error": "integer",
    "byDimension": {
      "dataIntegrity":    { "pass": 0, "warning": 0, "error": 0 },
      "crossConsistency": { "pass": 0, "warning": 0, "error": 0 },
      "outputQuality":    { "pass": 0, "warning": 0, "error": 0 },
      "analysisLogic":    { "pass": 0, "warning": 0, "error": 0 },
      "mathConsistency":  { "pass": 0, "warning": 0, "error": 0 }
    }
  },
  "results": [
    { "id": "string", "dimension": "string", "check": "string",
      "level": "PASS|WARNING|ERROR", "file": "string", "message": "string" }
  ],
  "errorDetails": [...],
  "warningDetails": [...]
}
```

写入 `<output_dir>/java-arch-extract/data/validation-report.json`。

#### 6.7 展示校验结果

向用户展示：

```
自检校验报告

【校验概览】
- 校验维度：5
- 校验项总数：XX
- PASS：XX 项
- WARNING：XX 项
- ERROR：XX 项

【按维度统计】
| 维度 | PASS | WARNING | ERROR |
|------|------|---------|-------|
| 数据完整性 | X | X | X |
| 交叉一致性 | X | X | X |
| 输出质量 | X | X | X |
| 分析逻辑 | X | X | X |
| 数学一致性 | X | X | X |

【ERROR 详情】（如存在）
| # | 维度 | 校验项 | 文件 | 详情 |
|---|------|--------|------|------|

【WARNING 详情】（如存在，展示前10条）
| # | 维度 | 校验项 | 文件 | 详情 |
|---|------|--------|------|------|

校验报告已保存至：<output_dir>/java-arch-extract/data/validation-report.json
```

- 如有 ERROR：提示用户检查，输入"继续"可忽略 ERROR 并完成，或说明调整内容
- 如无 ERROR：确认完成

**等待用户确认**。

#### 6.8 写 checkpoint

更新 `.java-arch-state.json`：

```json
{
  "phase": "validated",
  "projectPath": "/path/to/project",
  "outputDir": "/path/to/output",
  "scanDate": "date-time",
  "modules": [...],
  "techStack": {...},
  "layers": [...],
  "ooRelations": {...},
  "patterns": [...],
  "risks": [...],
  "gapItems": [...],
  "validation": {
    "timestamp": "date-time",
    "summary": {
      "totalChecks": "integer",
      "pass": "integer",
      "warning": "integer",
      "error": "integer"
    },
    "reportFile": "data/validation-report.json"
  }
}
```

用户最终确认完成后，更新为 `"phase": "complete"`。

---

## 关键约定

### 上下文控制规范

| 操作 | 允许 | 禁止 |
|------|------|------|
| 阶段1（全景扫描） | Grep/Glob 提取元数据 | Read Java源码全文 |
| 阶段2（分层识别） | Grep 注解/包名 | Read 类文件全文 |
| 阶段3（OO分析） | Read 关键类确认关系，Task 子代理分批处理（10-20/批） | Read 所有类文件 |
| 阶段4（模式识别） | 基于 L2/L3 结果推断，高频/中频模式分批扫描，Read 特定类验证 | 全量 Read 类文件 |
| 阶段5（汇总输出） | Read meta JSON，Glob 检查文件 | Read 生成的 md 文件全文 |
| 阶段6（自检校验） | Read JSON/MD 文件校验，Glob 检查文件存在性 | 修改任何输出文件 |

### 断点续传规范

- **Checkpoint 文件名**：`.java-arch-state.json`
- **存储位置**：`<output_dir>/java-arch/.java-arch-state.json`
- **格式**：JSON，包含 `phase` 字段标识当前阶段
- **Phase 值**：`overview_done` → `layers_done` → `oo_done` → `patterns_done` → `summary_done` → `validated` → `complete`
- **Resume 逻辑**：读取 checkpoint，根据 `phase` 值跳到对应阶段的起点继续执行
  - `overview_done`：从阶段2开始
  - `layers_done`：从阶段3开始
  - `oo_done`：从阶段4开始
  - `patterns_done`：从阶段5开始
  - `summary_done`：从阶段6开始（自检校验）
  - `validated`：展示校验结果，等待用户确认后标记为 complete
  - `complete`：提示用户分析已完成，询问是否需要重新生成

### 文档命名约定

- **Markdown 文档**：使用短横线连接，全小写（`overview.md`, `layers.md`, `oo-relations.md`, `design-patterns.md`, `summary.md`）
- **JSON 数据文件**：与对应 Markdown 同名（`overview.json`, `layers.json`, `oo-relations.json`, `design-patterns.json`, `arch-summary.json`）
- **Checkpoint 文件**：以点号开头（`.java-arch-state.json`）

### 缺口处理

1. 无法确定的信息标记 `[待确认]`，不在文档中编造内容
2. 每个阶段的 checkpoint 中维护 `gapItems` 数组，累积所有待确认项
3. 最终汇总文档的"待确认项"Section 列出所有累积的 gap items，便于用户集中审核

## 用户交互

- **阶段1**：确认扫描规则 → 确认项目全景
- **阶段2**：确认分层结果
- **阶段3**：确认OO关系
- **阶段4**：确认设计模式识别结果
- **阶段5**：确认汇总结果后保存
- **阶段6**：校验报告展示，如有 ERROR 需确认后方可完成
- 支持用户在任何阶段要求调整，调整后重新确认

## 错误处理

- **项目路径无效**：提示用户 `"项目路径不存在：<path>"` ，请用户提供正确路径
- **扫描不到分层信息**：提示用户 `"未检测到分层注解（@Controller/@Service/@Repository等），请确认项目是否为 Spring 项目"` ，建议检查 rules.md 的扫描规则是否适用于该项目
- **阶段3 Read 关键类失败**：标注 `"类文件读取失败：<file>"` ，从 Grep 的摘要信息补充，标记 `[待确认]` ，继续处理其他关系
- **设计模式识别结果为空**：提示用户 `"未识别到高置信度设计模式"` ，建议检查 patterns.md 的识别规则是否适用于该项目，或接受中/低置信度结果
- **子代理超时或中断**：写 checkpoint 保存当前进度，提示用户使用 `--resume` 从断点继续执行
- **Context Limit 触发**：主代理检测到子代理报错（如 Context Limit），自动将当前批次大小减半（如从 20 降至 10），重新分批执行；连续 3 次失败后暂停并提示用户
- **批处理回退策略**：如果某批次子代理失败，将该批次的类列表拆分为更小的子批次重试；若仍失败，标记失败的类为 `[待确认]`，跳过继续处理后续批次
