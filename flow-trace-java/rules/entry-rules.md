# Entry Detection Rules - 入口判断规则

本文件定义了四种入口类型的代码判断逻辑。Skill 运行时会展示当前已配置的判断规则，用户可以：
- 选择当前已支持的判断逻辑
- 在本文件中增加新的判断规则
- 在对话中直接描述自定义判断逻辑

---

## 噪音排除规则（Predicate Filter）

在深入解析前，先排除以下无效文件，减少无效 IO：

### 排除的 Glob 模式

```
**/*DTO.java
**/*Vo.java
**/*VO.java
**/*Test.java
**/*Tests.java
**/test/**
**/thirdparty/**
**/generated/**
**/*Constant.java
**/*Enum.java
**/*Config.java
**/*Properties.java
```

### 排除的包前缀

```
org.springframework.*
com.alibaba.*
lombok.*
javax.*
```

### 排除的类修饰符

```
abstract 类（class 声明含 abstract 关键字）
```

> 以上排除规则在阶段 1 扫描和阶段 3 链路提取中统一生效。排除列表记录在 `progress.json` 的 `projectConfig.noiseExclusion` 中。

---

## Controller 入口

### 判断规则

```
判断条件：类上标注了 @Controller 或 @RestController 注解
```

### Grep 搜索模式

```
# 查找 @Controller / @RestController 注解
pattern: @(Controller|RestController)
glob: **/*.java

# 查找 RequestMapping 相关注解
pattern: @(RequestMapping|PostMapping|GetMapping|PutMapping|DeleteMapping)
glob: **/*.java
```

### 验证方法

```
1. 使用 Grep 查找 "@Controller" 或 "@RestController" 注解
2. 使用 Read 读取标注该注解的类
3. 提取类级别的 @RequestMapping 路径前缀
4. 提取每个方法的 HTTP 路径和请求方法
5. 将 Controller 中每个标注了 HTTP Mapping 注解的 public 方法识别为一个独立流程入口
```

### 备注

Controller 作为独立入口类型，用于没有前端页面文件、但需要分析 API 端点流程的场景。如果一个 Controller 已被 Web 页面入口聚合，则不再重复列出。

---

## RMB 接收端入口

### 规则 1：@RmbController 注解

```
判断条件：类上标注了 @RmbController 注解
```

#### Grep 搜索模式

```
# 查找 @RmbController 注解
pattern: @RmbController
glob: **/*.java

# 查找 @RmbTopic 注解（获取 topic 和 topicMode）
pattern: @RmbTopic
glob: **/*.java
```

#### @RmbTopic 与 @AppHeaderArg 定义规则

RMB 接收端通过以下注解定义消息通道：

| 注解 | 必须 | 属性 | 说明 |
|------|------|------|------|
| `@RmbTopic` | 是 | `topic` | Topic 名称，标识消息通道 |
| `@RmbTopic` | 是 | `topicMode` | 处理机制：`SYNC`（同步阻塞）/ `ASYNC`（异步非阻塞） |
| `@RmbTopic` | 否 | `transCode` | 交易类型编码 |
| `@AppHeaderArg` | 否 | `transCode` | 交易类型编码 |

> `@RmbTopic` 和 `@AppHeaderArg` 中的 `transCode` 作用相同，理论上只能定义一次。

#### 验证方法

```
1. 使用 Grep 查找 "@RmbController" 注解
2. 使用 Read 读取标注该注解的类
3. 提取 @RmbTopic 注解的 topic（名称）、topicMode（SYNC/ASYNC）和可选的 transCode
4. 提取 @AppHeaderArg 注解中可选的 transCode（如果存在）
5. 提取类中每个 @RmbTopic 标注的方法，一个类中的多个 @RmbTopic 方法各自作为独立入口
```

### 规则 2：Chameleon 框架 RMB Flow

```
判断条件：代码实现了 com.webank.chameleon.frw.rmb.flow 接口，并且实现了 execute 方法
```

#### Grep 搜索模式

```
# 查找 Flow 接口实现类
pattern: implements\s+\w*[Ff]low
glob: **/*.java

# 查找 execute 方法
pattern: public\s+\w+\s+execute\s*\(
glob: **/*.java

# 查找 Chameleon RMB Flow 特征
pattern: com\.webank\.chameleon\.frw\.rmb
glob: **/*.java
```

#### 验证方法

```
1. 使用 Grep 查找 "com.webank.chameleon.frw.rmb.flow" 的 import 语句
2. 找到实现该接口的类
3. 使用 Read 读取该类，确认包含 execute 方法
4. 从 execute 方法的参数和逻辑中提取业务语义
```

### 如何新增自定义 RMB 规则

如果项目使用不同的 RMB 框架，在下方添加新规则：

```
### 规则 3：[框架名称] RMB 接收端

判断条件：[描述判断条件]

Grep 搜索模式：
pattern: [搜索模式]
glob: **/*.java

验证方法：
1. [步骤1]
2. [步骤2]
```

---

## Job 定时任务入口

### 规则 1：Chameleon 框架 Quartz Job

```
判断条件：代码继承了 com.webank.chameleon.frw.core.job.AbstractQuartzBean 并实现了 doJob 方法
```

#### Grep 搜索模式

```
# 查找 AbstractQuartzBean 子类
pattern: extends\s+AbstractQuartzBean
glob: **/*.java

# 查找 doJob 方法
pattern: (protected|public)\s+\w+\s+doJob\s*\(
glob: **/*.java
```

#### 验证方法

```
1. 使用 Grep 查找 "com.webank.chameleon.frw.core.job.AbstractQuartzBean" 的 import 语句
2. 找到继承该类的子类
3. 使用 Read 读取该类，确认包含 doJob 方法
4. 查找类上的注解或配置中的 cron 表达式
```

### 规则 2：Spring @Scheduled

```
判断条件：方法上标注了 @Scheduled 注解
```

#### Grep 搜索模式

```
pattern: @Scheduled
glob: **/*.java
```

### 规则 3：XXL-Job

```
判断条件：方法上标注了 @XxlJob 注解
```

#### Grep 搜索模式

```
pattern: @XxlJob
glob: **/*.java
```

### 规则 4：ElasticJob

```
判断条件：类实现了 SimpleJob 或 DataflowJob 接口
```

#### Grep 搜索模式

```
pattern: implements\s+(SimpleJob|DataflowJob)
glob: **/*.java
```

### 规则 5：@CronQuartzJob 注解

```
判断条件：类上标注了 @CronQuartzJob 注解
```

#### Grep 搜索模式

```
# 查找 @CronQuartzJob 注解
pattern: @CronQuartzJob
glob: **/*.java
```

#### 验证方法

```
1. 使用 Grep 查找 "@CronQuartzJob" 注解
2. 使用 Read 读取标注该注解的类
3. 提取 @CronQuartzJob 注解中的 cron 表达式或调度配置（如存在）
4. 定位入口方法（如 execute、run、doJob 等）
```

### 规则 6：类名以 Job 结尾

```
判断条件：Java 类名以 "Job" 结尾（文件名匹配 *Job.java）
```

#### Grep 搜索模式

```
# 查找类名以 Job 结尾的 Java 文件
glob: **/*Job.java
pattern: class\s+\w*Job\s+

# 或通过文件名直接匹配
glob: **/*Job.java
```

#### 验证方法

```
1. 使用 Glob 查找所有 *Job.java 文件
2. 使用 Grep 过滤出包含 class 定义的文件
3. 使用 Read 读取类文件，排除已被其他 Job 规则（规则1-5）识别的类
4. 定位入口方法（如 execute、run、doJob 等）
5. 如果类同时满足其他 Job 规则（如也标注了 @CronQuartzJob），按优先匹配的规则归类，不重复计数
```

#### 备注

此规则作为兜底扫描规则，捕获未使用常见 Job 框架注解/基类、但按命名约定以 Job 结尾的定时任务类。需排除已被规则 1-5 识别的类，避免重复。

### 继承方法映射（inheritanceMethodMap）

当 Job 类继承外部框架基类时，`methodPatterns` 中的方法名可能是基类方法而非子类覆写方法。`inheritanceMethodMap` 配置基类 → 入口方法的映射，脚本优先检查继承关系：

```
inheritanceMethodMap:
  AbstractQuartzJob → executeInternal
  CcpConcurrentTaskExecutor → executeTaskInner
  ConcurrentTaskExecutor → executeTask
```

解析优先级：
1. 解析 `extends BaseClass`，在 `inheritanceMethodMap` 中查找基类对应的方法名
2. 验证子类中是否存在该方法的覆写（`protected/public` 修饰）
3. 若无匹配，回退到 `methodPatterns` 逐个尝试

### 如何新增自定义 Job 规则

如果项目使用不同的 Job 框架，在下方添加新规则：

```
### 规则 5：[框架名称] Job

判断条件：[描述判断条件]

Grep 搜索模式：
pattern: [搜索模式]
glob: **/*.java

验证方法：
1. [步骤1]
2. [步骤2]
```

---

## 使用说明

### Skill 运行时的流程

1. Skill 读取本文件，提取所有入口类型的判断规则和噪音排除规则
2. 在阶段 1 开始时，向用户展示当前支持的判断规则：

```
当前入口判断规则

【噪音排除】
- 排除模式：*DTO.java, *VO.java, *Test.java, *Tests.java, test/**, thirdparty/**, generated/**
- 排除包前缀：org.springframework.*, com.alibaba.*, lombok.*, javax.*
- 排除修饰符：abstract 类

【Controller 入口】
- 规则：@Controller / @RestController 注解

【RMB 接收端入口】
- 规则1 (@RmbController)：类上标注 @RmbController 注解
- 规则2 (Chameleon Flow)：实现 com.webank.chameleon.frw.rmb.flow 接口 + execute 方法

【Job 定时任务入口】
- 规则1 (Chameleon Quartz)：继承 AbstractQuartzBean + doJob 方法
- 规则2 (Spring)：@Scheduled 注解
- 规则3 (XXL-Job)：@XxlJob 注解
- 规则4 (ElasticJob)：实现 SimpleJob/DataflowJob 接口
- 规则5 (@CronQuartzJob)：@CronQuartzJob 注解
- 规则6 (类名匹配)：Java 类名以 Job 结尾（兜底规则）

请确认：
1. 以上判断规则是否适用于当前项目？
2. 是否需要启用/禁用某些规则？
3. 是否需要自定义新的判断规则？
```

3. 用户确认后，将配置写入 `progress.json`，使用确认的规则进行入口扫描

---

## 机器可读配置

`scripts/phase1a_entry_scan.py` 从 `rules/entry-rules.json` 读取规则配置，而非解析本文件。当规则发生变更时，需同步修改 `entry-rules.json`。

> 已移除。Phase 1 改为纯源码扫描，不再使用 graph.db 交叉验证，因此不再需要置信度标注。entries.json 中不包含 confidence 字段。
