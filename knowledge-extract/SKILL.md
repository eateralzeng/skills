# Knowledge Extract v2: Java 代码知识提取器（流程 + 数据模型 + 交叉验证）

> 从 Java 代码工程中提取完整知识：流程链路追踪、数据模型文档、自动交叉验证

## 概述

本 skill 将 `flow-trace`（流程梳理）和 `data-model`（数据模型文档）两个 skill 合并为一个统一的执行流程。先运行流程梳理，再运行数据模型扫描，最后将两部分产物进行交叉验证并自动修复不一致。

**核心特性：**
- 一键全量提取：流程链路 + 数据模型 + 交叉验证，一次执行完成
- 流程梳理：全量入口扫描、全链路追踪、RMB 桥接合并、数据血缘分析
- 数据模型：双通道表发现（Entity + Mapper XML）、完整表结构、CRUD 归属、生命周期构建、业务域聚合
- 交叉验证：自动比对两套产物的数据一致性，发现问题并自动修复
- 分阶段确认：入口清单确认 → 链路追踪 → 表清单确认 → 表级分析 → 域划分 → 交叉验证 → 最终确认
- 模板驱动：所有输出严格遵循模板格式

**适用场景：**
- 快速了解陌生项目的完整知识（流程 + 数据模型）
- 代码审查时定位流程和数据变更的影响范围
- 生成流程文档和数据模型文档用于知识传递
- 分析跨模块的数据依赖和隐式耦合
- 为新团队成员提供完整的项目入门文档

## 使用方法

```bash
# 基本用法
/knowledge-extract-v2 <java_project_path>

# 示例
/knowledge-extract-v2 /path/to/java/project
```

## 执行流程

skill 执行分为 **7 个阶段**，按序执行：

### 阶段 1：流程入口扫描（来自 flow-trace）

1. 读取 `architecture.md`（如存在），理解系统架构背景
2. 读取 `entry-rules.md` 中的入口判断规则，展示给用户确认
3. 扫描项目：Web 页面、Controller、RMB 接收端、Job 四类入口
4. 向用户展示入口清单，等待确认

### 阶段 2：链路追踪 + RMB 桥接（来自 flow-trace）

1. 逐入口追踪调用链路：入口 → Service → Repository/Mapper → DB Table
2. 记录每一层的类名、方法签名、调用关系
3. RMB 桥接匹配：@RmbClient ↔ @RmbController 通过 Topic 串联
4. 数据库操作类型识别（SELECT/INSERT/UPDATE/DELETE）

### 阶段 3：流程输出（来自 flow-trace）

1. 为每个入口生成独立的 Markdown 流程文档（区分合并/独立流程）
2. 生成 `flow-summary.json`、`flow-summary.md`
3. 生成 `flow-detail.json`（Handler 级别详情）
4. 生成 `flow-data-lineage.json`、`flow-data-lineage.md`（数据血缘）
5. 向用户展示汇总结果，等待确认

### 阶段 4：表发现（来自 data-model）

1. 双通道扫描：Entity 注解通道 + Mapper XML 通道
2. 交叉合并两个通道的结果
3. 参考 flow-trace 产物（阶段 3 已生成）交叉验证补充
4. 向用户展示表清单，等待确认

### 阶段 5：表级分析 + 域聚合（来自 data-model）

1. 对每张表独立分析：字段结构、CRUD 操作、生命周期、关联关系
2. 使用 flow-trace 产物辅助追踪 DAO 调用方和跨流程依赖
3. 域聚合：按代码模块和表间关系划分为业务域
4. 向用户展示域划分方案，等待确认
5. 生成 `tables/*.md`、`domains/*.md`

### 阶段 6：全局汇总（来自 data-model）

1. 生成 `table-index.md`（全量表索引）
2. 生成 `domain-map.md`（域关系总览）
3. 生成 `data-model-summary.json`（结构化汇总）
4. 向用户展示汇总结果，确认后保存

### 阶段 7：交叉验证 + 自动修复（新增）

1. 读取 flow-trace 和 data-model 的全部产物
2. 执行 7 维度交叉验证（详见下方）
3. 自动修复发现的问题
4. 向用户展示验证报告和修复结果

## 交叉验证维度

| # | 验证维度 | 说明 | 修复策略 |
|---|---------|------|---------|
| V1 | 表覆盖率 | flow-trace 引用的表是否都在 data-model 中有文档 | 缺失的表自动创建文档 |
| V2 | CRUD 操作 | flow-trace 中的 DB 操作是否与 data-model 声明一致 | 以源码为准修正 data-model |
| V3 | 状态字段值 | data-model 中的状态枚举是否与源码常量一致 | 以源码常量为准修正 |
| V4 | 生命周期 | data-model 标注的生命周期是否与 flow-trace 的实际操作匹配 | 以 flow-trace 实际操作为准补充 |
| V5 | 模块归属 | data-model 中表的模块归属是否与 flow-trace 一致 | 合并两方的模块信息 |
| V6 | 关联关系 | data-model 的表间关系是否覆盖了 flow-trace 发现的依赖 | 补充缺失的关系 |
| V7 | 内部一致性 | 两套产物内部的计数、命名、引用是否自洽 | 自动修正计数和命名 |
| **V8** | **源码抽样验证** | **回到源码验证 flow-trace 的调用链和 DB 操作是否真实存在** | **以源码为准修正虚构内容** |

## 输出结构

生成的文档保存在目标 Java 项目的根目录下。**flow-trace 和 data-model 必须分离到不同目录，严禁混放**：

```
<java_project>/
├── flow-trace/
│   ├── flows/
│   │   ├── 001-<发送端>-RMB-<接收端>.md   # 合并的 RMB 全链路流程 (MERGED_RMB_FLOW)
│   │   ├── 100-<入口名称>.md             # 独立流程 (STANDALONE_FLOW)
│   │   ├── 200-<扇入Topic>.md            # 共享Topic扇入流程 (FANIN_RMB_FLOW)
│   │   └── ...
│   ├── flow-summary.json                  # 流程汇总 JSON (含 flows[], tableIndex[])
│   ├── flow-summary.md                    # 流程汇总 Markdown
│   ├── flow-detail.json                   # Handler 级别详情 (含 handlers[].chain[], databaseOperations[])
│   ├── flow-data-lineage.json             # 数据血缘 JSON (含 producers/consumers/updaters/implicitDependencies)
│   └── flow-data-lineage.md               # 数据血缘 Markdown
├── data-model/                            # ★ 独立目录，严禁放到 flow-trace 下
│   ├── tables/
│   │   ├── t-std-req-info.md              # 每张表一个文件 (表名下划线转连字符)
│   │   └── ...
│   ├── domains/
│   │   ├── judicial-control.md            # 业务域文件
│   │   └── ...
│   ├── table-index.md                     # 全量表索引
│   ├── domain-map.md                      # 域关系总览
│   └── data-model-summary.json            # 数据模型汇总 JSON
└── cross-validation-report.md             # 交叉验证报告
```

### 模板文件清单

所有输出严格遵循 `templates/` 目录中的模板格式：

| 模板文件 | 用途 | 来源 |
|---------|------|------|
| `flow-template.md` | STANDALONE_FLOW 单流程文档 | flow-trace |
| `flow-template-merged-rmb.md` | MERGED_RMB_FLOW 单流程文档 | flow-trace |
| `flow-template-fanin.md` | FANIN_RMB_FLOW 共享Topic扇入文档 | flow-trace |
| `flow-summary.md` | 流程汇总 Markdown | flow-trace |
| `flow-summary.json` | 流程汇总 JSON 示例 | flow-trace |
| `flow-summary-schema.json` | 流程汇总 JSON Schema | flow-trace |
| `flow-detail.json` | Handler 详情 JSON 示例 | flow-trace |
| `flow-detail-schema.json` | Handler 详情 JSON Schema | flow-trace |
| `flow-data-lineage.md` | 数据血缘 Markdown | flow-trace |
| `flow-data-lineage.json` | 数据血缘 JSON 示例 | flow-trace |
| `flow-data-lineage-schema.json` | 数据血缘 JSON Schema | flow-trace |
| `table-template.md` | 单表文档模板 | data-model |
| `domain-template.md` | 业务域文档模板 | data-model |
| `table-index-template.md` | 表索引模板 | data-model |
| `domain-map-template.md` | 域关系总览模板 | data-model |
| `data-model-summary.json` | 数据模型汇总 JSON Schema | data-model |

### 格式合规要求

每个阶段输出后必须进行格式自检：

**flow-trace 产物**：
1. 每个 flow 文档必须包含 "入口信息"、"调用链图"、"逐层调用详情"、"数据库操作汇总" 四个核心 section
2. `flow-detail.json` 是最核心产物，每个 handler 必须有 `chain[]` 和 `databaseOperations[]`
3. `flow-summary.json` 必须有 `flows[]` 和 `tableIndex[]`
4. `flow-data-lineage.json` 必须有 `producers/consumers/updaters/implicitDependencies`

**data-model 产物**：
1. 每张表一个独立 md 文件，输出到 `data-model/tables/`
2. 每个表文档必须包含 "表结构"、"CRUD 操作"、"生命周期"、"关联表" section
3. `data-model-summary.json` 必须符合其 JSON Schema 定义

**严禁**：
- 将 data-model 产物放到 `flow-trace/tables/` 下
- 自由发挥文档格式（不使用模板）
- 省略 `flow-detail.json` 或 `data-model-summary.json`

## 依赖工具

本 skill 依赖 Claude Code 的以下工具：
- `Glob`: 文件模式匹配
- `Grep`: 代码内容搜索
- `Read`: 文件内容读取
- `Write`: 文档写入
- `Edit`: 文档编辑（交叉验证修复时使用）

## 注意事项

1. **项目要求**：Java 项目需要有清晰的分层结构
2. **执行顺序**：严格按阶段 1→2→3→4→5→6→7 执行，后续阶段依赖前序产物
3. **架构文档**：如果项目根目录存在 `architecture.md`，自动读取作为背景知识
4. **模板驱动**：所有输出必须严格遵循模板格式，生成前先读取对应模板
5. **分阶段确认**：入口清单、表清单、域划分需用户确认后继续
6. **代码证据校验**：每个追踪步骤都必须有源代码支撑，禁止推断填充
7. **交叉验证**：V1-V8 全部检查项均执行，以源码为最终裁决依据
8. **自动修复**：交叉验证发现的问题自动修复，修复后展示变更摘要
9. **防推断填充机制**：
   - 批量 Handler 场景下，必须逐个读取源码，禁止按分组模式批量推断
   - Handler 继承链必须完整追踪到中间抽象类，禁止跳过
   - 每个 Handler 的 databaseOperations 必须从源码中实际存在的 DAO 调用提取
   - V8 源码抽样验证作为终极防线，回源码校验产物准确性

## 与原 skill 的对比

| 维度 | flow-trace | data-model | knowledge-extract-v2 |
|------|-----------|------------|---------------------|
| 核心视角 | 代码流程 | 数据结构 | 流程 + 数据模型 |
| 交叉验证 | 无 | 参考 flow-trace | 自动双向验证 + 修复 |
| 执行方式 | 独立运行 | 独立运行 | 一体化顺序执行 |
| 产物一致性 | 可能不一致 | 可能不一致 | 强制一致 |

## 变更历史

| 版本 | 日期 | 变更内容 | 变更人 |
|------|------|----------|--------|
| 2.2 | 2026-04-28 | 强化格式合规：(1) 新增 FANIN_RMB_FLOW 模板处理共享Topic扇入场景；(2) 新增阶段3/5/6格式校验清单；(3) 强制 flow-trace/data-model 目录分离；(4) flow-detail.json 标记为核心产物；(5) 新增禁止行为清单 | Claude |
| 2.1 | 2026-04-27 | 强化防推断填充：阶段 2 新增强制源码读取规则（2.1.1 + 2.2.1）；新增 V8 源码抽样验证维度 | Claude |
| 2.0 | 2026-04-27 | 合并 flow-trace v1.3 + data-model v1.1，新增阶段 7 交叉验证 | Claude |
