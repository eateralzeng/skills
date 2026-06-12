---
name: java-arch-extract
description: Use when you need to analyze a Java project's layered architecture, OO design, and design patterns. Scans Spring+RMB codebases to extract layer structure, class relationships, and pattern usage.
---

# Java Architecture Extract：Java架构分析

> 从 Java 代码工程中自动抽取分层架构、OO设计和设计模式，生成层次化架构分析文档

## 概述

本 skill 通过分析 Java 代码工程，自动识别分层架构、提取 OO 设计关系（继承、接口实现、组合、依赖）、识别设计模式使用，并进行层间依赖合规检查，生成完整的架构分析文档。

**核心特性：**
- 6 阶段渐进式扫描：从项目全景到交叉验证和自检校验，逐步深入
- 注解+包名双策略：通过类级别注解和包名路径双重识别，确保分层识别覆盖完整
- 12 种设计模式识别：覆盖创建型、结构型、行为型设计模式的自动识别
- 层间依赖合规检查：检测跳层访问、反向依赖、职责混淆等违规，含豁免规则
- Markdown+JSON 双输出：人类可读文档和机器可消费结构化数据同步生成
- 断点续传：每个阶段写入 checkpoint，支持 `--resume` 恢复执行

**适用场景：**
- 快速了解项目的分层架构全貌
- 分析模块间的依赖关系和技术栈组成
- 识别 OO 设计中的接口抽象和继承层次
- 发现项目中使用的设计模式及其参与者
- 检查层间依赖是否合规，发现架构风险点
- 代码审查时评估架构变更影响范围
- 为新团队成员提供架构入门文档

## 使用方法

```bash
# 基本用法
/java-arch-extract <java_project_path>

# 指定输出目录
/java-arch-extract <java_project_path> --output <output_dir>

# 只扫描指定模块
/java-arch-extract <java_project_path> --module cbrc-repo --module farms-bs

# 断点续传
/java-arch-extract <java_project_path> --resume
```

## 参数说明

| 参数 | 必填 | 说明 |
|------|------|------|
| `<java_project_path>` | 是 | Java 项目根目录 |
| `--output <dir>` | 否 | 输出目录（默认项目同级 knect 目录） |
| `--module <name>` | 否 | 只扫描指定模块（可多次指定） |
| `--resume` | 否 | 从 checkpoint 恢复执行 |

## 执行流程

### 阶段 1：项目全景扫描
1. 自动读取项目中的 `architecture.md`（如果存在），理解系统架构背景
2. 读取 `rules.md` 中的扫描规则
3. 识别构建系统（Maven/Gradle）及多模块结构
4. 检测技术栈框架（Spring Boot、RMB、MyBatis 等）
5. 扫描包结构，建立模块与层次的初步映射
6. 写入 checkpoint

### 阶段 2：分层识别
- 通过类级别注解（主策略）+ 包名路径（辅助策略）识别各层归属
- 覆盖 Controller、Service、DAO、Entity、DTO/VO、Config、Util、RMB Client、RMB Controller 共 9 个层次
- 识别优先级：注解 > 包名 > 文件名后缀 > 待确认标记
- 生成各模块的分层架构图和层间依赖矩阵
- 写入 checkpoint

### 阶段 3：OO 关系分析
- 扫描继承关系（extends）、接口实现（implements）
- 分析组合关系（字段持有）和依赖关系（方法参数/局部变量）
- 提取接口与实现类的映射、抽象类与具体子类的层次
- 生成类关系图（ASCII）和结构化关系列表
- 写入 checkpoint

### 阶段 4：设计模式识别
- 自动识别 12 种设计模式（Factory、Singleton、Strategy、Template Method、Builder、Adapter、Proxy、Decorator、Observer、Facade、Command、Chain of Responsibility）
- 按置信度分级（高/中/低），标注识别依据
- 记录模式参与者及其角色
- 写入 checkpoint

### 阶段 5：交叉验证与汇总
- 执行层间依赖合规检查（跳层访问、反向依赖、职责混淆）
- 交叉验证分层识别与 OO 关系的一致性
- 标注架构风险点和改进建议
- 生成汇总报告
- 写入 checkpoint

### 阶段 6：自检校验
- 对阶段1-5的输出进行全面数据质量自检
- 5个维度：数据完整性、交叉一致性、输出质量、分析逻辑、数学一致性
- 生成校验报告 `data/validation-report.json`
- ERROR 级问题需用户确认后方可标记分析完成

## 输出结构

```
<output_dir>/java-arch-extract/
├── .java-arch-state.json           # Checkpoint文件
├── <project>-架构分析/
│   ├── 00-项目全景.md
│   ├── 01-分层架构.md
│   ├── 02-OO关系.md
│   ├── 03-设计模式.md
│   ├── 04-汇总报告.md
│   └── data/
│       ├── overview.json
│       ├── layers.json
│       ├── oo-relations.json
│       ├── design-patterns.json
│       ├── arch-summary.json
│       └── validation-report.json
```

### 模板文件清单

| 文件 | 用途 |
|------|------|
| `templates/overview.md` | 项目全景 Markdown 模板（模块结构、技术栈、包结构） |
| `templates/overview.json` | 项目全景 JSON 结构模板（模块列表、技术栈、包树） |
| `templates/layers.md` | 分层架构 Markdown 模板（分层图、各层详情、层间依赖） |
| `templates/layers.json` | 分层架构 JSON 结构模板（层次、类列表、层间依赖） |
| `templates/oo-relations.md` | OO 关系 Markdown 模板（接口实现、抽象类层次、关系图） |
| `templates/oo-relations.json` | OO 关系 JSON 结构模板（接口、抽象类、关系列表） |
| `templates/design-patterns.md` | 设计模式 Markdown 模板（模式总览、详情、参与者） |
| `templates/design-patterns.json` | 设计模式 JSON 结构模板（模式列表、置信度、参与者） |
| `templates/summary.md` | 汇总报告 Markdown 模板（架构概览、风险点、建议） |
| `templates/arch-summary.json` | 汇总报告 JSON 结构模板（统计、分布、风险） |
| `templates/validation-report.json` | 自检校验报告 JSON 结构模板（校验结果、按维度统计） |
| `rules.md` | 扫描规则定义（分层识别、构建系统、框架特征、合规检查、自检校验） |

## 依赖工具

本 skill 依赖 Claude Code 的以下工具：
- `Glob`: 文件模式匹配（查找 pom.xml、Mapper XML、Java 源文件）
- `Grep`: 代码内容搜索（查找注解、import 语句、类声明）
- `Read`: 文件内容读取（精读 Java 源文件、配置文件）
- `Write`: 文档写入（Markdown 和 JSON 输出）
- `Task`: 子代理批量处理（阶段 2-4 的分批分析）

## 设计原则

本 skill 遵循 `~/.claude/skills/PRINCIPLES.md` 中的 AI Skill 架构设计原则：

- **非必要不读取（Lazy Loading）**：先用 Grep 定位关键行，确认逻辑相关后才 Read 源文件
- **阅后即焚（Context Flushing）**：子代理短命运行，完成后只返回精简摘要，结果写入磁盘
- **中间结果持久化（Persistence）**：每阶段结果存入 checkpoint，不依赖对话历史保存数据
- **编排者模式（Orchestrator）**：主 Skill 负责任务拆解和汇总，子代理负责具体分析
- **批处理模式（Batch Processing）**：大量对象分片处理，每批 10-20 个，完成一批同步一次
- **过滤器模式（Predicate Filter）**：深入解析前排除噪音（测试类、第三方库、DTO 纯数据类等）
- **单点事实（Single Source of Truth）**：配置信息启动时一次性读取，后续统一引用
- **防御性扫描（Defensive Scanning）**：子代理报错时自动缩小批次并重试

## 注意事项

1. **项目要求**：Java 项目需要有 Spring 注解或标准的包结构（至少存在其一）
2. **架构文档**：如果项目根目录存在 `architecture.md`，skill 会自动读取作为背景知识
3. **扫描规则**：规则定义在 `rules.md` 中，包含分层识别、构建系统检测、框架特征识别和层间依赖合规检查规则
4. **阶段1 Grep-only**：阶段1只使用 Grep/Glob 扫描元数据，不 Read 源码全文，确保大项目快速完成全景扫描
5. **分阶段确认**：阶段1和阶段5的结果需确认后才继续，避免大量无用工作
6. **并发处理**：阶段2-4使用 Task 子代理分批处理，单个模块/类失败不影响其他
7. **待确认标记**：无法通过注解和包名明确归层的类标记为 `[待确认]`，留待人工审核

## 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| 1.0 | 2026-05-01 | 初始版本 |
| 1.1 | 2026-05-01 | 对齐 PRINCIPLES.md 设计原则：添加噪音排除、批处理、编排者模式、防御性扫描 |
| 1.2 | 2026-05-12 | 新增阶段6：自检校验，5维度数据质量校验 |
