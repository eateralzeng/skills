# Architecture Extraction Rules - 分层架构抽取规则

本文件定义了从 Java 项目中抽取分层架构信息的扫描规则。目标项目为使用 **Spring + RMB 框架**的银行系统，强调 **Grep-only** 策略，阶段1不 Read 源码全文。

---

## 1. 分层识别规则

### 1.1 注解驱动识别（主策略）

优先通过类级别注解判定所属层次：

| 层次 | 识别注解 | type 枚举值 |
|------|---------|-----------|
| Controller 层 | `@RestController`, `@Controller` | `controller` |
| Service 层 | `@Service`, `@Component`（在 service/biz/core 包下） | `service` |
| DAO/Repository 层 | `@Repository`, `@Mapper`, `*Mapper` 接口（MyBatis） | `dao` |
| Entity 层 | `@Entity`, `@Table`, `@TableName` | `entity` |
| DTO/VO 层 | 无特定注解（按包名识别） | `dto` |
| Config 层 | `@Configuration`, `@EnableXxx` | `config` |
| Util 层 | 无特定注解（按包名识别） | `util` |
| RMB Client 层 | `@RmbClient` | `rmb-client` |
| RMB Controller 层 | `@RmbController` | `rmb-controller` |

```
# Controller 层
Grep pattern="@(RestController|Controller)" glob="**/*.java"
→ 提取：类名、文件路径

# Service 层
Grep pattern="@(Service|Component)" glob="**/*.java"
→ 辅助验证：类所在包是否含 service/biz/core

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

# RMB 层
Grep pattern="@RmbClient" glob="**/*.java"
Grep pattern="@RmbController" glob="**/*.java"
→ 提取：类名、文件路径、注解属性
```

### 1.2 包名驱动识别（辅助策略）

当类缺少注解时，通过包名路径判定归属层次：

| 包名模式 | 归属层次 | type 枚举值 |
|---------|---------|-----------|
| `*.controller`, `*.web`, `*.rest` | Controller 层 | `controller` |
| `*.service`, `*.biz`, `*.core`, `*.manager` | Service 层 | `service` |
| `*.dao`, `*.repository`, `*.mapper`, `*.persistence` | DAO 层 | `dao` |
| `*.entity`, `*.model`, `*.domain`, `*.po`, `*.pojo` | Entity 层 | `entity` |
| `*.dto`, `*.vo`, `*.request`, `*.response`, `*.param` | DTO/VO 层 | `dto` |
| `*.config`, `*.configuration` | Config 层 | `config` |
| `*.util`, `*.helper`, `*.common`, `*.utils`, `*.tools` | 工具层 | `util` |
| `*.rmb`（含 `@RmbClient`） | RMB Client 层 | `rmb-client` |
| `*.rmb`（含 `@RmbController`） | RMB Controller 层 | `rmb-controller` |

```
# 按包名扫描各层（无注解兜底）
Grep pattern="package\s+" glob="**/controller/**/*.java"
Grep pattern="package\s+" glob="**/service/**/*.java"
Grep pattern="package\s+" glob="**/dao/**/*.java"
Grep pattern="package\s+" glob="**/entity/**/*.java"
Grep pattern="package\s+" glob="**/dto/**/*.java"
Grep pattern="package\s+" glob="**/config/**/*.java"
Grep pattern="package\s+" glob="**/util/**/*.java"
Grep pattern="package\s+" glob="**/rmb/**/*.java"
→ 提取：类名、包路径、文件路径
```

### 1.3 识别优先级

1. **注解优先** — 类级别注解是第一判定依据
2. **包名辅助** — 缺少注解时按包名路径归类
3. **文件名参考** — 类名后缀提供佐证（如 `*Controller`、`*Service`、`*Dao`、`*Mapper`）
4. **未确认标记** — 以上规则均无法判定时，标记 `[待确认]`，留待人工审核

---

## 2. 构建系统识别规则

识别项目使用的构建工具及多模块结构。

### Maven

```
# 检测 Maven 项目
Grep pattern="<modules>" glob="**/pom.xml"
Grep pattern="<parent>" glob="**/pom.xml"
Grep pattern="<groupId>|<artifactId>|<version>" glob="**/pom.xml"
→ 提取：模块列表、父工程坐标、各模块坐标

# 多模块结构
Glob pattern="**/pom.xml"
→ 根据目录层级推断模块关系
```

### Gradle

```
# 检测 Gradle 项目
Grep pattern="include\s*['\"]" glob="**/settings.gradle"
Grep pattern="include\s*['\"]" glob="**/settings.gradle.kts"
→ 提取：子模块列表

Glob pattern="**/build.gradle"
Glob pattern="**/build.gradle.kts"
→ 根据文件位置推断模块关系
```

---

## 3. 框架特征识别规则

检测项目中使用的技术框架，每种框架给出对应的 Grep 扫描命令。

| # | 框架 | Grep 命令 |
|---|------|----------|
| 1 | Spring Boot | `Grep pattern="@SpringBootApplication" glob="**/*.java"` |
| 2 | Spring MVC | `Grep pattern="@(RestController|Controller|RequestMapping|GetMapping|PostMapping)" glob="**/*.java"` |
| 3 | Spring IOC | `Grep pattern="@(Service|Component|Repository|Autowired|Resource)" glob="**/*.java"` |
| 4 | RMB 框架 | `Grep pattern="@(RmbClient|RmbController)" glob="**/*.java"` |
| 5 | MyBatis | `Grep pattern="@(Mapper|Select|Insert|Update|Delete)" glob="**/*.java"` + `Glob pattern="**/*Mapper.xml"` |
| 6 | MyBatis-Plus | `Grep pattern="@(TableName|TableField|BaseMapper)" glob="**/*.java"` |
| 7 | JPA | `Grep pattern="@(Entity|Table|Column|Id|GeneratedValue)" glob="**/*.java"` |
| 8 | Redis | `Grep pattern="(RedisTemplate|StringRedisTemplate|@Cacheable|@CacheEvict|@CachePut)" glob="**/*.java"` |
| 9 | MQ | `Grep pattern="@(RabbitListener|KafkaListener|JmsListener|RocketMQMessageListener)" glob="**/*.java"` |
| 10 | Scheduled | `Grep pattern="@(Scheduled|EnableScheduling|Schedules)" glob="**/*.java"` |

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
Grep pattern="@(Scheduled|EnableScheduling|Schedules|FixedRate|Cron)" glob="**/*.java"
→ 提取：定时任务、调度配置
```

---

## 4. 层间依赖合规规则

### 4.1 合法的层间依赖方向

```
合法依赖方向（自上而下）：

Controller ──→ Service ──→ DAO ──→ Entity
     │              │
     │              └──→ DTO (入参/出参)
     │
     └──→ DTO (入参/出参)

RMB Controller ──→ Service ──→ DAO ──→ Entity
       │               │
       │               └──→ DTO (入参/出参)
       │
       └──→ DTO (入参/出参)

Service ──→ RMB Client ──→ DTO (远程调用)

Config ──→ 被所有层引用（豁免）
Util  ──→ 被所有层引用（豁免）
Entity ──→ 无外部依赖
DTO   ──→ 无外部依赖（可引用 Entity）
```

### 4.2 违规检测规则

| # | 违规类型 | 检测方式 | 示例 |
|---|---------|---------|------|
| 1 | 跳层访问 | Controller 直接引用 DAO 或 Entity | Controller 中注入 `*Mapper` 或 `*Dao` |
| 2 | 反向依赖 - Service→Controller | Service 层 import Controller 类 | `*ServiceImpl` 中 import `*Controller` |
| 3 | 反向依赖 - DAO→Service | DAO 层 import Service 类 | `*Mapper` 或 `*Dao` 中 import `*Service` |
| 4 | 职责混淆 - Service 直接操作 HTTP | Service 层使用 `HttpServletRequest`/`HttpServletResponse` | `*Service` 中 import `javax.servlet.*` |
| 5 | 职责混淆 - DAO 包含业务逻辑 | DAO 层使用 `@Transactional` 或调用其他 DAO | `*Mapper` 中标注事务注解 |

```
# 跳层检测：Controller 直接引用 DAO
Grep pattern="(Mapper|Dao|Repository)" glob="**/controller/**/*.java"
Grep pattern="import.*\.(dao|mapper|repository)\." glob="**/controller/**/*.java"

# 反向依赖：Service 引用 Controller
Grep pattern="import.*\.controller\." glob="**/service/**/*.java"
Grep pattern="import.*\.web\." glob="**/service/**/*.java"

# 反向依赖：DAO 引用 Service
Grep pattern="import.*\.service\." glob="**/dao/**/*.java"
Grep pattern="import.*\.biz\." glob="**/dao/**/*.java"

# 职责混淆：Service 操作 HTTP 对象
Grep pattern="(HttpServletRequest|HttpServletResponse|javax\.servlet)" glob="**/service/**/*.java"

# 职责混淆：DAO 包含业务逻辑
Grep pattern="@Transactional" glob="**/dao/**/*.java"
Grep pattern="import.*\.service\." glob="**/dao/**/*.java"
```

### 4.3 豁免规则

以下情况不视为违规：

- **Config 层**：`@Configuration` 类可被任何层引用，不参与依赖方向检查
- **测试代码**：`src/test/` 下的类不参与依赖方向检查
- **工具类**：`util`/`helper`/`common` 包下的静态工具类可被任何层引用，不参与依赖方向检查

---

## 5. 噪音排除规则（Predicate Filter）

在深入解析前，先应用以下排除规则减少无效 IO：

### 5.1 排除列表

| # | 排除类型 | 排除规则 | Grep/Glob 命令 |
|---|---------|---------|---------------|
| 1 | 测试代码 | `src/test/` 目录下所有 Java 文件 | `Glob pattern="src/test/**/*.java"` → 排除 |
| 2 | 第三方库 | import 非 `com.webank.*` / `com.company.*` 的外部类 | 不扫描 `import` 为第三方包的类 |
| 3 | 自动生成代码 | 含 `@Generated` 或 `@AutoValue` 注解的类 | `Grep pattern="@Generated|@AutoValue" glob="**/*.java"` → 排除 |
| 4 | 配置引导类 | 仅含 `@SpringBootApplication` 的启动类 | 排除只有一个注解的启动类 |
| 5 | DTO 纯数据类 | 只有 getter/setter 的 DTO/VO | 不进入 OO 关系深度分析，仅计入分层统计 |
| 6 | Enum 类 | 枚举类型 | `Grep pattern="^public\s+enum\s+" glob="**/*.java"` → 排除 |

### 5.2 应用策略

- **分层统计**：排除的类仍计入分层统计（各层类数量）
- **OO 关系分析**：排除的类不参与继承、组合、依赖关系的深度分析
- **设计模式识别**：排除的类不作为设计模式的参与者
- **合规检查**：排除的类不参与层间依赖合规检查

---

## 6. 使用说明

### L1 阶段 - 全局扫描

1. Skill 读取本文件，提取全部扫描规则
2. **全部使用 Grep/Glob**，不使用 Read 读取源码全文
3. 执行 Section 1（分层识别）和 Section 2（构建系统识别），建立模块与层次的映射关系
4. 执行 Section 3（框架特征识别），记录项目使用的技术栈
5. 输出：模块清单、各模块层次分布、框架清单

### L2 阶段 - 层间依赖分析

1. 子代理按需 Read 单个 Java 源文件，分析 import 语句和注入关系
2. 执行 Section 4（层间依赖合规规则），检测违规项
3. 子代理按需 Grep 追踪跨模块调用链
4. 输出：依赖关系图、违规清单（含豁免标记）

### L3 阶段 - 详细建模

1. 子代理 Read 完整类文件，提取方法签名、字段、注解属性
2. 结合 Section 1 的识别规则，补全 `[待确认]` 项的层次归属
3. 输出：完整的类级架构模型

### L4 阶段 - 结果汇总

1. 汇总 L1-L3 的输出，生成最终架构描述
2. 按 Section 1.1 的 type 枚举值归类所有类
3. 按 Section 4.1 的依赖方向整理调用关系
4. 标注 Section 4.2 的违规项和 Section 4.3 的豁免项
5. 输出：项目架构文档（含分层图、依赖矩阵、违规报告）

### L5 阶段 - 自检校验

1. 读取 Section 7 中定义的自检校验规则
2. 使用子代理分维度执行校验（5个维度可并行）
3. 汇总校验结果，生成 `validation-report.json`
4. ERROR 级问题需用户确认后方可标记分析完成
5. 输出：校验报告（含通过/警告/错误统计、详细问题列表）

---

## 7. 自检校验规则

### 7.1 数据完整性校验规则

| # | 校验项 | 期望 | 失败级别 |
|---|--------|------|---------|
| 1 | JSON 文件可解析 | 全部5个JSON文件合法 | ERROR |
| 2 | 必填字段非空 | layers[].type, patterns[].confidence 等必填字段有值 | ERROR |
| 3 | type 枚举值合法 | controller/service/dao/entity/dto/config/util/rmb-client/rmb-controller | WARNING |
| 4 | confidence 枚举值合法 | high/medium/low | WARNING |
| 5 | 关系类型枚举值合法 | extends/implements/composition/dependency/association | WARNING |
| 6 | 数值合理性 | classCount >= 0, percentage >= 0 且 <= 100 | WARNING |

### 7.2 交叉一致性校验规则

| # | 校验项 | 期望 | 失败级别 |
|---|--------|------|---------|
| 1 | 各层类数之和 ≤ 总类数 | SUM(layers[].classCount) ≤ totalClasses | WARNING |
| 2 | 模块数一致 | checkpoint.modules.length = layers.json 中模块数 | WARNING |
| 3 | OO引用类存在 | oo-relations 中引用的类名在 layers 的类列表中 | WARNING |
| 4 | 模式参与者存在 | patterns 参与者在 layers 类列表中 | WARNING |
| 5 | checkpoint 统计 = JSON 实际数据 | 各字段逐一比对 | WARNING |

### 7.3 输出质量校验规则

| # | 校验项 | 期望 | 失败级别 |
|---|--------|------|---------|
| 1 | 10个文件全部生成 | 全部存在 | ERROR |
| 2 | Markdown 无残留占位符 | 无 {{xxx}} 残留 | WARNING |
| 3 | JSON 符合模板结构 | 字段与模板一致 | ERROR |
| 4 | 文件非空且非异常小 | size > 100 bytes | WARNING |

### 7.4 分析逻辑校验规则

| # | 校验项 | 期望 | 失败级别 |
|---|--------|------|---------|
| 1 | 置信度匹配特征数 | high≥3, medium≥2, low≥1 | WARNING |
| 2 | 模板方法参与者完整 | 有抽象模板 + 至少1个具体实现 | WARNING |
| 3 | 策略模式参与者完整 | 有策略接口 + 至少2个实现 | WARNING |
| 4 | 违规项有改进建议 | risks[] 中每项 suggestion 非空 | WARNING |
| 5 | gapItems 状态 | 记录所有待确认项（允许非空） | INFO |

### 7.5 数学一致性校验规则

| # | 校验项 | 期望 | 失败级别 |
|---|--------|------|---------|
| 1 | 层分布百分比求和 | SUM(percentage) ≈ 100%（误差±5%） | WARNING |
| 2 | arch-summary 统计一致 | 与各分项 JSON 统计字段一致 | ERROR |
| 3 | MD 与 JSON 一致 | 同一数据在 Markdown 和 JSON 中一致 | WARNING |
