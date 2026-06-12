# Entry Detection Rules - 入口判断规则

本文件定义了四种入口类型的代码判断逻辑。Skill 运行时会展示当前已配置的判断规则，用户可以：
- 选择当前已支持的判断逻辑
- 在本文件中增加新的判断规则
- 在对话中直接描述自定义判断逻辑

---

## Web 页面入口

### 判断规则

```
1. 查找前端页面文件：.vue, .html, .jsp, .tsx
2. 从前端代码中提取 API 调用路径（axios/fetch/request 等）
3. 将 API 路径映射到后端 Controller（@RequestMapping/@PostMapping/@GetMapping）
4. 将同一页面调用的多个 API 聚合为一个页面级入口
```

### Grep 搜索模式

```
# 前端页面文件
glob: **/*.{vue,html,jsp,tsx}

# 前端 API 调用
pattern: (axios|fetch|request|api)\.(get|post|put|delete)\s*\(

# 后端 Controller 映射
pattern: @(RequestMapping|PostMapping|GetMapping|PutMapping|DeleteMapping)
```

### 备注

Web 页面入口的关键是将多个 API 聚合为页面级别，而非将每个 Controller 独立视为入口。

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
5. 将 Controller 中每个 public 方法识别为一个独立流程入口
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

#### 验证方法

```
1. 使用 Grep 查找 "@RmbController" 注解
2. 使用 Read 读取标注该注解的类
3. 提取 @RmbTopic 注解的 topic（名称）和 topicMode（SYNC/ASYNC）
4. 提取类中处理 RMB 消息的方法
5. 追踪方法内部调用链
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

### 规则 3：RMB 桥接匹配

当追踪到 `@RmbClient` 调用（发送端）或 `@RmbController` 接收端时，通过 Topic 名称将两端桥接为一个完整的 MERGED_RMB_FLOW。

#### 匹配流程

```
1. 追踪到 @RmbClient 发送端时：
   a. 从 @RmbClient 注解或方法参数中提取 topic 名称
   b. 如果 topic 使用常量引用，使用 Grep 追踪常量定义获取实际值
   c. 使用 Grep 搜索 @RmbTopic.*topic\s*=\s*"<topic_name>" 找到匹配的接收端
   d. 找到匹配 → 将两端合并为一个 MERGED_RMB_FLOW，继续追踪接收端内部链路
   e. 未找到匹配 → 将发送端记录为独立流程，接收端标记为 [external]

2. 扫描 @RmbController 接收端时（反向查找）：
   a. 从 @RmbTopic 注解中提取 topic 名称
   b. 使用 Grep 搜索代码中所有 @RmbClient 或 API 定义类中对同一 topic 的引用
   c. 找到匹配 → 合并为 MERGED_RMB_FLOW
   d. 未找到匹配 → 接收端记录为独立流程，发送端标记为 [external]

3. TELLER_SERVICE 等共享 Topic 的特殊处理：
   - 当多个 @RmbController 共享同一 Topic（如 TELLER_SERVICE）时
   - 如果发送端不在本代码库中，将每个 handler 作为独立 RMB 接收流程处理
   - 发送端统一标记为 [external - <系统名称>]
```

#### Grep 搜索模式

```
# 从 @RmbClient 提取 topic
pattern: @RmbClient
glob: **/*.java

# 从方法参数或常量定义中提取 topic 名称
pattern: topic\s*=\s*"[^"]+"
glob: **/*.java

# 匹配接收端的 @RmbTopic
pattern: @RmbTopic.*topic\s*=\s*"<extracted_topic>"
glob: **/*.java

# 反向查找：从接收端 topic 搜索发送端
pattern: <topic_name>
glob: **/*.java
（然后在搜索结果中过滤 @RmbClient 或 API 定义类）
```

#### 验证方法

```
1. 使用 Grep 查找所有 @RmbClient 注解
2. 读取每个 @RmbClient 所在类，提取 topic 名称（可能是常量引用）
3. 如果是常量引用，使用 Grep 追踪常量定义，获取实际值
4. 使用 Grep 搜索 @RmbTopic 注解中包含相同 topic 值的类
5. 验证匹配结果：
   - 精确匹配（topic 字符串完全相同）→ 合并为 MERGED_RMB_FLOW
   - 无匹配 → 标记为 [external] 发送端
6. 反向扫描：对所有 @RmbController，执行相同搜索逻辑
```

#### 备注

- RMB 桥接匹配使用**精确字符串匹配**，不做模糊匹配
- Topic 常量可能定义在 API 模块的常量类中，需追踪常量定义获取实际值
- 合并后的流程编号范围：001-099
- 独立流程编号范围：100+
- TELLER_SERVICE 等 fan-in 场景：20+ 个 handler 共享同一 Topic，发送端为外部系统，按独立流程处理

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

1. Skill 读取本文件，提取所有入口类型的判断规则
2. 在阶段 1 开始时，向用户展示当前支持的判断规则：

```
📋 当前入口判断规则

【Web 页面入口】
- 规则：查找前端页面文件 → 提取 API 调用 → 映射到 Controller → 聚合为页面级入口

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

请确认：
1. 以上判断规则是否适用于当前项目？
2. 是否需要启用/禁用某些规则？
3. 是否需要自定义新的判断规则？
```

3. 用户确认后，使用确认的规则进行入口扫描

---

## 表发现规则（data-model 阶段）

以下规则用于阶段 4（表发现）和阶段 5（表级分析）。

### Entity 注解发现

```
1. 搜索 @Table / @TableName / @Entity 注解
2. 精读 Entity 类提取表名和字段
```

#### Grep 搜索模式

```
# Entity 类注解
pattern: @(Table|TableName)\s*\(
glob: **/*.java

# JPA Entity 注解
pattern: @Entity
glob: **/*.java
```

### Mapper XML 发现

```
1. 查找所有 *Mapper.xml 文件
2. 解析 SQL 提取表名和 CRUD 操作
```

#### Grep 搜索模式

```
# Mapper XML 文件
glob: **/*Mapper.xml

# SQL 表名提取
pattern: (INSERT INTO|UPDATE|FROM|JOIN)\s+\w+
glob: **/*Mapper.xml
```

### 表间关联推断

```
1. JOIN 语句 + Entity 关联注解 + resultMap 关联 → 推断外键关系
```

### DAO 接口发现

```
pattern: (Dao|Mapper)\s*\.
glob: **/*.java
```

### Service 调用方追踪

```
1. Grep DAO 类名 → Service 层（1 层）→ Handler/Job/Controller（2 层）
```
