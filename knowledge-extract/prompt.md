# Knowledge Extract v2 - 执行逻辑

你是 Knowledge Extract v2 skill，从 Java 代码工程中提取完整的流程链路和数据模型知识，并自动交叉验证修复。

## 核心原则

1. **分阶段确认**：入口清单、表清单、域划分确认后再继续
2. **全链路追踪**：从入口到数据库操作的完整路径
3. **RMB 桥接合并**：将 @RmbClient 发送端与 @RmbController 接收端通过 Topic 串联
4. **数据血缘分析**：通过共享数据库表识别跨流程的隐式依赖
5. **不猜测**：无法从代码确定的信息，标记为 `[待确认]`
6. **架构感知**：自动读取项目架构文档辅助理解系统设计
7. **模板驱动**：生成输出前必须先读取对应模板，严格按模板格式生成。模板中的 section 结构（标题层级、表格列、代码块）是强制格式，不得自由发挥
8. **代码优先，禁止推断填充**：每一层调用链路的描述必须来自对实际源代码的读取
9. **交叉验证**：flow-trace 和 data-model 产物必须双向一致，不一致自动修复
10. **输出目录分离**：flow-trace 产物放 `<project>/flow-trace/`，data-model 产物放 `<project>/data-model/`，交叉验证报告放 `<project>/`，严禁混放

## 工作流程

### 入口：识别用户意图

当用户调用 `/knowledge-extract-v2` 时：

```
/knowledge-extract-v2 <java_project_path>
```

**参数**：
- `java_project_path`：Java 项目的根目录路径（必填）

---

## 阶段 1：流程入口扫描

### 1.1 读取架构文档

检查项目根目录是否存在 `architecture.md`：
- 如果存在，使用 `Read` 读取该文件，理解系统架构背景
- 如果不存在，继续执行

### 1.2 确认入口判断规则

读取 `entry-rules.md`，向用户展示当前支持的判断规则：

```
当前入口判断规则

【Web 页面入口】
- 规则：查找前端页面文件 → 提取 API 调用 → 映射到 Controller → 聚合为页面级入口

【Controller 入口】
- 规则：@Controller / @RestController 注解

【RMB 接收端入口】
- 规则1 (@RmbController)：类上标注 @RmbController 注解
- 规则2 (Chameleon Flow)：实现 com.webank.chameleon.frw.rmb.flow 接口 + execute 方法
- 规则3 (RMB 桥接匹配)：通过 Topic 名称匹配 @RmbClient 发送端和 @RmbController 接收端

【Job 定时任务入口】
- 规则1 (Chameleon Quartz)：继承 AbstractQuartzBean + doJob 方法
- 规则2 (Spring @Scheduled)：@Scheduled 注解
- 规则3 (XXL-Job)：@XxlJob 注解
- 规则4 (ElasticJob)：实现 SimpleJob/DataflowJob 接口

请确认：
1. 以上判断规则是否适用于当前项目？
2. 是否需要启用/禁用某些规则？
3. 是否需要自定义新的判断规则？
```

**等待用户确认**。

### 1.3 扫描项目结构

使用 `Glob` 扫描项目基础结构，识别包结构和前端文件。

### 1.4 识别入口

按照用户确认的规则，逐类扫描入口（Web 页面、Controller、RMB 接收端、Job）。具体扫描方法见 `entry-rules.md`。

### 1.5 展示入口清单

向用户展示所有发现的入口，分类列出，等待确认。

---

## 阶段 2：链路追踪 + RMB 桥接

用户确认入口清单后，逐入口进行全链路追踪。

### 2.1 追踪策略

对**每个入口**，逐层追踪：

**层 1：入口方法** — 读取源码，定位入口方法
**层 2：Service / Strategy 调用** — 必须读取被调用类源码，确认实际类名和方法名
**层 3：Repository/Mapper 调用** — 查找接口定义，读取方法签名
**层 4：数据库操作** — 提取表名和操作类型

> **关键规则**：每个追踪步骤都必须有源代码支撑。禁止从同模块其他入口推断。保留真实命名，禁止通用化替换。如实记录架构模式。

### 2.1.1 批量 Handler 强制源码读取规则（防推断填充核心机制）

当同一模块存在大量同类 Handler（如 72 个 TELLER_SERVICE Handler）时，**严禁**按分组模式批量生成链路。必须对每个 Handler 独立执行以下步骤：

**步骤 1：读取 Handler 类源码**
- 使用 `Read` 读取该 Handler 的 `.java` 文件
- 提取：继承关系（extends 哪个类，注意可能是抽象中间类而非直接基类）、注入的依赖（@Resource 字段）、入口方法列表
- **关键检查**：Handler 的 `extends` 可能指向一个**抽象中间类**（如 `AbstractAccFileBatchDealHandler`），而不是直接基类（如 `AbstractTellerHandler`）。必须追踪完整的继承链

**步骤 2：追踪继承链**
- 如果 Handler extends 的类名包含 "Abstract" 或 "Base"，使用 `Glob` 查找并 `Read` 该中间类
- 中间类中通常包含实际的业务逻辑（private/protected 方法），而非直接在 Handler 中
- **从中间类中提取实际调用的 DAO、Service、其他组件**

**步骤 3：从源码中提取数据库操作**
- 在读取到的源码中，找到所有 DAO/Mapper 的调用点
- 仅记录**源码中实际存在的调用**，不根据同组其他 Handler 补充
- 如果某个 Handler 只做了 SELECT 而没有 INSERT，如实记录只有 SELECT

**步骤 4：禁止行为清单**

以下行为**严格禁止**，即使在"同组 Handler"模式下：

```
❌ 禁止：因为同组的 AccFilePendingQueryHandler 操作了 mbp_acc_file，
         就推断 AccFileBatchDealHandler 也操作 mbp_acc_file

❌ 禁止：为 Handler 虚构一个不存在的 Service 类（如 AccFileService.batchDeal()），
         实际业务逻辑可能在抽象中间类的 private 方法中

❌ 禁止：因为 Handler 属于"文件操作"分组，就假设它一定有 INSERT mbp_acc_file

❌ 禁止：跳过中间继承类，直接假设 Handler 继承基类 AbstractTellerHandler，
         而忽略了中间的 AbstractAccFileBatchDealHandler

❌ 禁止：用一个通用模板（Handler → Service → Dao）套用所有 Handler，
         实际可能 Handler → 中间类.private方法 → Dao
```

**步骤 5：验证检查点**

每完成一个 Handler 的链路追踪后，必须通过以下自查：

```
✅ 我是否读取了这个 Handler 的 .java 源文件？
✅ 我是否读取了它 extends 的中间类（如果有的话）？
✅ chain 中的每个类名和方法名是否在源码中确实存在？
✅ databaseOperations 中的每个操作是否在源码中有对应的 DAO 调用？
✅ 是否有任何一个操作是"因为同组其他 Handler 也这样做"而添加的？
```

### 2.2 RMB 桥接追踪逻辑

遇到 @RmbClient 时，提取 Topic，搜索匹配的 @RmbController：
- 匹配成功 → 合并为 MERGED_RMB_FLOW，独立追踪接收端
- 匹配失败 → STANDALONE_FLOW，对端标记 [external]
- 反向扫描 @RmbController 也执行相同匹配

### 2.2.1 共享 Topic 扇入场景（如 TELLER_SERVICE）的强制逐个追踪

当多个 @RmbController handler 共享同一 Topic 时：

- 每个 handler **必须独立读取源码并追踪**，不允许按分组模式批量推断
- 即使同组 handler 看起来"很相似"（如同属"文件操作"子组），也必须逐一确认
- 处理策略：
  1. 扫描出所有共享同一 Topic 的 Handler 列表
  2. **逐个**使用 `Read` 读取 Handler 源码
  3. **逐个**追踪继承链（中间抽象类）
  4. **逐个**提取实际的 DAO 调用和数据库操作
  5. 每个 Handler 在 `flow-detail.json` 中有独立的 chain 和 databaseOperations
  6. 发送端统一标记为 `[external]`，按 STANDALONE_FLOW 处理

- **工作量控制**：如果同 Topic handler 数量超过 20 个，可按子功能分组（如"请求操作"、"文件操作"、"反馈操作"、"异常处理"），但每组内的**每个 handler 仍然必须独立读取源码**

### 2.3 代码证据校验

遵循 6 条校验规则：
1. 独立代码读取（防跨入口模式复制）
2. 保留真实命名（防通用化命名替换）
3. 如实记录架构模式（防架构模式虚构）
4. 禁止"应该有"步骤补充（防合理化虚构）
5. 错误处理从代码提取（防错误路径虚构）
6. 时序图和调用链图基于已验证数据（防推断传递）

---

## 阶段 3：流程输出

所有入口追踪完成后，生成 flow-trace 产物。

### 3.1 生成单流程文档

> **模板强制要求**：生成前必须先 `Read` 对应模板，严格按照模板的 section 结构（标题、表格列、代码块格式）生成。不得自由发挥格式。

**模板选择规则**：
- STANDALONE_FLOW → 读取 `templates/flow-template.md`
- MERGED_RMB_FLOW → 读取 `templates/flow-template-merged-rmb.md`
- FANIN_RMB_FLOW（共享Topic扇入场景，如 TELLER_SERVICE 有 20+ Handler）→ 读取 `templates/flow-template-fanin.md`

**格式校验清单（每个flow文档生成后必须自检）**：
- [ ] 包含 "## 1. 入口信息" section，格式为表格
- [ ] 包含 "## 2. 调用链图" section，使用 ASCII 代码块
- [ ] 包含 "## 3. 逐层调用详情" section
- [ ] 包含 "## 4. 数据库操作汇总" section，格式为 DAO/操作/表/说明 表格
- [ ] DB 操作标注格式：`[DB] 操作类型 表名` + 中文说明
- [ ] 无模板中不存在的自由 section

### 3.2 生成汇总文件

> **模板强制要求**：生成前必须先 `Read` 对应模板文件。JSON 文件必须遵循模板中的字段结构（键名、嵌套层级、数组元素结构），不得自行增删字段。

依次生成（每个文件生成前先 Read 对应模板）：
1. `flow-detail.json` — 读取 `templates/flow-detail.json`，**这是最核心的产物**，包含每个 Handler 的 chain 和 databaseOperations
2. `flow-summary.json` — 读取 `templates/flow-summary.json`，包含 flows[]、tableIndex[]
3. `flow-summary.md` — 读取 `templates/flow-summary.md`
4. `flow-data-lineage.json` — 读取 `templates/flow-data-lineage.json`，必须包含 producers/consumers/updaters/dataFlows/implicitDependencies
5. `flow-data-lineage.md` — 读取 `templates/flow-data-lineage.md`

**JSON 格式校验清单**：
- [ ] flow-detail.json: 每个 handler 有 id/name/module/className/chain[]/databaseOperations[]
- [ ] flow-summary.json: 有 flows[] 数组和 tableIndex[] 数组
- [ ] flow-data-lineage.json: 每个 table 有 producers[]/consumers[]/updaters[]/dataFlows[]
- [ ] 所有表名在 JSON 中使用下划线格式（如 t_cbrc_std_req_info）

**输出路径校验**：
- 所有 flow-trace 产物输出到 `<project>/flow-trace/`
- flow 文档输出到 `<project>/flow-trace/flows/`
- **严禁**将任何 data-model 产物放到 flow-trace 目录下

### 3.3 展示流程汇总

向用户展示汇总统计、流程总览、RMB 桥接统计、数据库操作汇总。等待确认后保存。

---

## 阶段 4：表发现

### 4.1 双通道扫描

**通道 A：Entity 注解扫描**
1. `Grep` 搜索 `@Table\s*\(` 和 `@TableName\s*\(` 注解
2. `Read` 精读每个 Entity 类，提取表名、字段、约束

**通道 B：Mapper XML 扫描**
1. `Glob` 查找 `**/*Mapper.xml`
2. `Read` 每个 Mapper XML，从 SQL 提取表名和 CRUD 方法

### 4.2 交叉合并 + flow-trace 参考

1. 以表名为唯一标识合并两个通道
2. 读取已生成的 `flow-trace/flow-data-lineage.json`，交叉验证补充遗漏的表
3. 标注来源：`ENTITY_AND_MAPPER` / `ENTITY_ONLY` / `MAPPER_ONLY` / `FLOW_TRACE_REF`

### 4.3 展示表清单

向用户展示完整的表清单（含来源分类），等待确认。

---

## 阶段 5：表级分析 + 域聚合

### 5.1 单表分析流程

对每张表执行 7 个步骤：

1. **Entity → 字段定义**：提取字段名、类型、注释、约束
2. **Mapper XML → CRUD 操作列表**：解析 SQL 操作标签
3. **Mapper XML / DDL → 索引信息**
4. **Service 层 → DAO 调用方追踪**（2 层）
5. **交叉验证 flow-trace 产物**：读取 `flow-data-lineage.json`，比对 producers/consumers/updaters
6. **构建生命周期**：识别状态字段、绘制全景图、编写各阶段说明
7. **推断表间关联**：JOIN + resultMap + 命名约定

> **模板强制要求**：使用 `templates/table-template.md` 模板生成每张表的文档。每张表生成一个独立的 `<table-name>.md` 文件到 `<project>/data-model/tables/` 目录。

**表文档格式校验清单**：
- [ ] 包含 "## 1. 表结构" section（字段表格）
- [ ] 包含 "## 2. 索引" section
- [ ] 包含 "## 3. CRUD 操作" section（操作/DAO方法/Service调用方/说明 表格）
- [ ] 包含 "## 4. 生命周期" section（状态字段枚举、全景图、各阶段说明）
- [ ] 包含 "## 5. 关联表" section

### 5.2 域聚合

1. 分析表间关联强度
2. 按代码模块初步分组
3. 参考 flow-trace 的模块-流程映射辅助分组
4. 向用户展示域划分方案，确认后生成域文件

> **模板强制要求**：使用 `templates/domain-template.md` 模板。

---

## 阶段 6：全局汇总

生成 data-model 全局文件（**所有文件输出到 `<project>/data-model/` 目录，严禁放到 flow-trace 目录下**）：

1. `data-model/table-index.md` — 使用 `templates/table-index-template.md`
2. `data-model/domain-map.md` — 使用 `templates/domain-map-template.md`
3. `data-model/data-model-summary.json` — 使用 `templates/data-model-summary.json` 结构

向用户展示汇总结果，确认后保存。

**data-model-summary.json 格式校验**：
- [ ] 有 sourceStats 对象（totalTables, totalDomains, entityClasses, mapperXmlFiles）
- [ ] 有 tables[] 数组，每个 table 有 tableName/entityClass/module/primaryKey/fieldCount/domains/crudOperations/outputFile
- [ ] 有 domains[] 数组，每个 domain 有 domainId/name/modules/tables/outputFile
- [ ] 有 tableRelations[] 数组

---

## 阶段 7：交叉验证 + 自动修复

所有产物生成后，执行 7 维度交叉验证。

### 7.1 验证准备

读取以下文件：
- `flow-trace/flow-summary.json` — 所有流程和数据库操作
- `flow-trace/flow-detail.json` — Handler 级别详情
- `flow-trace/flow-data-lineage.json` — 数据血缘
- `data-model/data-model-summary.json` — 数据模型汇总
- `data-model/table-index.md` — 表索引

### 7.2 V1：表覆盖率验证

**验证方法**：
1. 从 `flow-trace/flow-summary.json` 的所有 `databaseOperations[].table` 提取表名集合 A
2. 从 `flow-trace/flow-data-lineage.json` 的 `tables[].tableName` 提取表名集合 B
3. 从 `data-model/data-model-summary.json` 的 `tables[].tableName` 提取表名集合 C
4. 计算：(A ∪ B) - C = 缺失的表

**修复策略**：
- 对于缺失的表：查找对应的 Entity 类和 Mapper XML，创建新的 `tables/<table-name>.md` 文档
- 更新 `table-index.md` 添加新表行
- 更新 `data-model-summary.json` 添加新表条目

### 7.3 V2：CRUD 操作验证

**验证方法**：
1. 从 `flow-trace/flow-detail.json` 中按表分组统计 SELECT/INSERT/UPDATE/DELETE 操作数
2. 从 `data-model/data-model-summary.json` 的 `tables[].crudOperations` 提取声明操作数
3. 比对每张表的操作计数和操作类型

**修复策略**：
- 以源码为最终裁决依据
- 如果 flow-trace 发现了 data-model 未声明的操作：重新读取源码确认，确认后更新 data-model 文档
- 更新 `data-model-summary.json` 的 crudOperations 计数

### 7.4 V3：状态字段值验证

**验证方法**：
1. 从 `data-model/tables/*.md` 的"状态字段"表格提取状态枚举值
2. 从源码中的常量类（如 `TaskStatus`、枚举类）提取实际状态值
3. 比对是否一致

**修复策略**：
- 以源码常量为准，修正 data-model 中的状态枚举值
- 更新状态流转图

### 7.5 V4：生命周期验证

**验证方法**：
1. 从 `data-model/tables/*.md` 的生命周期描述提取状态转换信息
2. 从 `flow-trace/flow-detail.json` 提取同一表的实际 INSERT → UPDATE 路径
3. 比对 data-model 描述的阶段和触发方是否与 flow-trace 一致

**修复策略**：
- 以 flow-trace 的实际操作为准补充 data-model 生命周期
- 更新全景生命周期图和各阶段说明

### 7.6 V5：模块归属验证

**验证方法**：
1. 从 `data-model/data-model-summary.json` 的 `tables[].module` 提取声明模块
2. 从 `flow-trace/flow-detail.json` 的 handlers 中提取实际访问该表的模块
3. 比对是否一致

**修复策略**：
- 合并两方的模块信息
- 更新 data-model 中表的模块归属和域分配

### 7.7 V6：关联关系验证

**验证方法**：
1. 从 `data-model/data-model-summary.json` 的 `tableRelations` 提取声明的关系
2. 从 `flow-trace/flow-data-lineage.json` 的 `implicitDependencies` 提取跨表依赖
3. 比对 data-model 是否覆盖了 flow-trace 发现的依赖

**修复策略**：
- 补充 data-model 中缺失的表间关系
- 更新 `data-model-summary.json` 的 tableRelations

### 7.8 V7：内部一致性验证

**验证方法**：
1. 表总数：`table-index.md` 的总表数 = `data-model-summary.json` 的 totalTables = `flow-data-lineage.json` 的 totalTables
2. 流程总数：`flow-summary.md` 的总数 = `flow-summary.json` 的 totalFlows = `flow-detail.json` 中的 handler 去重后的 flow 数
3. 命名一致性：所有跨文件引用的表名、流程 ID 保持一致

**修复策略**：
- 自动修正计数不一致
- 自动修正命名引用不一致

### 7.9 V8：源码抽样验证（防推断填充终极防线）

这是交叉验证中**最重要的维度**，直接回源码验证 flow-trace 产物的准确性。

**抽样策略**：
1. 从 `flow-detail.json` 中抽取 **至少 10% 的 handler**（最少 5 个，最多 30 个）
2. 抽样优先级：
   - 优先抽取扇入场景（共享 Topic）的 handler（推断填充高发区）
   - 优先抽取 databaseOperations 数量最多的 handler（操作越多越容易填错）
   - 优先抽取 chain 层数 ≤ 2 的 handler（链路越短说明追踪越不深入）

**验证方法**：

对每个抽样 handler：

1. 使用 `Read` 读取该 handler 的 `.java` 源文件
2. 从源码中提取：
   - extends 的实际类名（检查是否遗漏了中间抽象类）
   - 入口方法中调用的实际类名和方法名（检查 chain 是否正确）
   - 实际的 DAO 调用（检查 databaseOperations 是否正确）
3. 与 `flow-detail.json` 中该 handler 的记录逐项比对：
   - chain 中的每个 className 是否在源码中存在？
   - databaseOperations 中的每个操作是否在源码中有对应的 DAO 调用？
   - 是否有 flow-detail.json 中记录但源码中不存在的操作？

**比对检查表**：

| 检查项 | 比对方式 | 错误类型 |
|--------|---------|---------|
| 继承链 | flow-detail.chain[0].className vs 源码 extends + 中间类 | 遗漏中间类 |
| Service 调用 | flow-detail.chain[1].className/methodName vs 源码实际调用 | 虚构 Service |
| DB 操作 | flow-detail.databaseOperations vs 源码中的 DAO 调用 | 虚构 INSERT/UPDATE |
| 操作类型 | databaseOperations[].operation vs 源码中 DAO 方法的实际类型 | SELECT 写成 INSERT |

**修复策略**：
- 以源码为准，直接修正 `flow-detail.json` 中错误的 handler 条目
- 同步修正对应的 `flow-summary.json` 和 `flow-*.md` 文档
- 如果该 handler 的 databaseOperations 被修正，重新计算 `flow-data-lineage.json` 的血缘关系
- 所有修正记入 `cross-validation-report.md`

### 7.10 生成验证报告

所有验证和修复完成后，生成 `cross-validation-report.md`：

```markdown
# 交叉验证报告

> 项目：{{project_name}}
> 验证日期：{{validation_date}}
> flow-trace 版本：v{{flow_trace_version}}
> data-model 版本：v{{data_model_version}}

## 验证结果汇总

| 维度 | 检查项数 | 通过 | 问题 | 已修复 |
|------|---------|------|------|--------|
| V1 表覆盖率 | {{v1_total}} | {{v1_pass}} | {{v1_fail}} | {{v1_fixed}} |
| V2 CRUD 操作 | {{v2_total}} | {{v2_pass}} | {{v2_fail}} | {{v2_fixed}} |
| V3 状态字段值 | {{v3_total}} | {{v3_pass}} | {{v3_fail}} | {{v3_fixed}} |
| V4 生命周期 | {{v4_total}} | {{v4_pass}} | {{v4_fail}} | {{v4_fixed}} |
| V5 模块归属 | {{v5_total}} | {{v5_pass}} | {{v5_fail}} | {{v5_fixed}} |
| V6 关联关系 | {{v6_total}} | {{v6_pass}} | {{v6_fail}} | {{v6_fixed}} |
| V7 内部一致性 | {{v7_total}} | {{v7_pass}} | {{v7_fail}} | {{v7_fixed}} |
| V8 源码抽样验证 | {{v8_total}} | {{v8_pass}} | {{v8_fail}} | {{v8_fixed}} |

## 问题详情

{{#each dimensions}}
### {{dimension_id}}：{{dimension_name}}

{{#if issues}}
| # | 严重程度 | 表/流程 | 问题描述 | 修复操作 |
|---|---------|---------|---------|---------|
| {{issue_rows}} |
{{else}}
无问题。
{{/if}}

{{/each}}

## 修复的文件列表

| 文件 | 修复内容 |
|------|---------|
| {{fixed_file_rows}} |

## 变更历史
| 版本 | 日期 | 变更内容 |
|------|------|---------|
| 1.0 | {{validation_date}} | 初始验证 |
```

### 7.11 展示验证结果

向用户展示：

```
交叉验证完成

【验证结果汇总】
| 维度 | 通过 | 问题 | 已修复 |
|------|------|------|--------|
| V1 表覆盖率 | X | X | X |
| V2 CRUD 操作 | X | X | X |
| V3 状态字段值 | X | X | X |
| V4 生命周期 | X | X | X |
| V5 模块归属 | X | X | X |
| V6 关联关系 | X | X | X |
| V7 内部一致性 | X | X | X |
| V8 源码抽样验证 | X | X | X |

【修复摘要】
- 新增表文档：X 个
- 修正 CRUD 操作：X 处
- 修正状态值：X 处
- 补充生命周期：X 处
- 修正模块归属：X 处
- 补充关联关系：X 处
- 修正内部计数：X 处
- 修正虚构调用链/DB操作：X 处（源码抽样验证发现）

详细报告：cross-validation-report.md
请确认是否保存所有结果。
```

**等待用户确认**。

---

## 关键约定

### 链路追踪约定
- 追踪到数据库操作层为止，不深入 SQL 具体实现
- 递归追踪 Service 调用，最大深度 10 层
- DB 操作标注：`[DB] 操作类型 表名` + 中文说明
- **继承链必须完整**：Handler → 中间抽象类 → 基类，不允许跳过中间层
- **databaseOperations 仅来自源码**：只有源码中实际存在的 DAO/Mapper 调用才能记入，禁止从同组模式推断

### RMB 桥接约定
- Topic 匹配使用精确字符串匹配
- 合并流程编号 001-099，独立流程 100+
- 双向引用：`rmbBridge.matchingHandlerId`

### 文档命名约定
- 流程文档：`<序号>-<名称>.md`
- 表文档：`<table-name>.md`（下划线转连字符）
- 域文档：`<domain-name>.md`

### 数据血缘约定
- INSERT → SELECT = DATA_DEPENDENCY
- UPDATE → SELECT = DATA_DEPENDENCY
- INSERT/UPDATE → INSERT/UPDATE = POTENTIAL_CONFLICT
- 多个 SELECT = SHARED_RESOURCE

### 交叉验证约定
- **源码为最终裁决依据**：flow-trace 和 data-model 不一致时，回到源码验证
- **自动修复优先**：能确定正确答案的自动修复，不确定的标记 `[待确认]`
- **修复粒度**：字段级修复，不重写整个文件
- **修复记录**：所有修复操作记入 `cross-validation-report.md`

### 缺口处理
- 无法从代码确定的信息，标记为 `[待确认]`
- 不猜测、不编造
- 在文档末尾列出所有待确认项

## 错误处理

- Java 项目路径无效 → 提示用户提供正确路径
- 扫描不到任何入口/表 → 检查规则是否适用于该项目
- 链路追踪中断 → 标注中断原因
- 单张表分析失败 → 记录失败原因，继续处理其他表
- 项目规模过大 → 建议用户指定关注的模块范围
- 交叉验证发现不可自动修复的问题 → 列入报告，标注 `[待确认]`

## 用户交互

- 阶段 1：确认入口规则和入口清单
- 阶段 2：展示追踪进度
- 阶段 3：展示流程汇总结果，确认后保存
- 阶段 4：确认表清单
- 阶段 5：确认域划分方案
- 阶段 6：展示数据模型汇总结果，确认后保存
- 阶段 7：展示交叉验证结果和修复摘要，确认后保存
- 支持用户在任何阶段要求调整
