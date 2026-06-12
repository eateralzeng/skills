---
name: flow-trace-db
description: 以 graph.db 终点导向提取为主路，LLM 源码校验为补充，从 Java 代码工程中提取流程文档
user_invocable: true
---

# Flow Trace DB

> 以 graph.db 静态分析数据为主路，终点导向提取 Java 项目流程文档

## 使用方法

```
/flow-trace-db <project_path> [db_path]
```

## 前置依赖

- Java 项目源码目录
- Atlas graph.db 文件（默认 `<project>/.atlas/graph.db`）

## 核心特性

- **终点导向**：以数据库操作和 RMB 外调为终点，只保留能到达终点的路径
- **DB-first**：调用链和表操作从 graph.db 确定
- **LLM 源码校验**：Phase 5 定点读源码，校验 + 补充遗漏 + 生成业务描述
- **五维度校验**：D1 入口完备、D2 链路合理性、D3 数据库覆盖、D4 RMB 桥接、D5 描述质量
- **业务概述**：MD 文档包含流程业务概述，直接支持 DDD 领域分析
- **三个配置规则**：entry-rules / bridge-rules / filter-rules
- **断点续传**：阶段粒度自动恢复

## 目录结构

```
├── SKILL.md              ← 本文件
├── prompt.md             ← 入口提示词（编排者）
├── phases/               ← 各阶段规格说明（.md）
│   ├── phase1-entry-scan.md
│   ├── phase2-db-schema.md
│   ├── phase3-chain-extract.md
│   ├── phase4-rmb-bridge.md
│   ├── phase5-source-verify.md
│   ├── phase6-doc-gen.md
│   └── phase7-validate.md
├── rules/                ← 配置规则
│   ├── filter-rules.md
│   ├── bridge-rules.md
│   └── entry-rules.md
├── scripts/              ← Python 执行脚本（已验证）
│   ├── phase2_db_schema.py
│   ├── phase3_chain_extract.py
│   ├── phase4_rmb_bridge.py
│   ├── phase5_source_verify.py
│   ├── phase6_doc_gen.py
│   └── phase7_validate.py
└── templates/
    └── flow-template.md
```

**执行方式**：优先直接运行 `scripts/*.py`，避免每次重新生成脚本时因 LLM 理解差异引入问题。Phase 5 的 LLM 读源码工作由 `prompt.md` 编排子代理完成。
