---
name: data-model-db
description: 以 graph.db + 源码为数据源，生成数据库表生命周期视图（归属链、CRUD、状态流转、流程覆盖度）
user_invocable: true
---

# Data Model DB

> 以 graph.db 静态分析 + Java 源码验证，生成数据库表生命周期视图

## 使用方法

```
/data-model-db <project_path> [db_path] [--tables <table_list_file>]
```

## 前置依赖

- Java 项目源码目录
- Atlas graph.db 文件（默认 `<project>/.atlas/graph.db`）
- 用户提供的完整表清单文件（可选，txt 格式每行一个表名）

## 核心特性

- **表驱动**：以用户提供的表清单为起点，保证覆盖度
- **源码验证**：每个 Phase 生成数据后通过读取 Java 源码/MyBatis XML 反向验证
- **多数据访问模式兼容**：支持 MyBatis XML、注解、JdbcTemplate
- **断点续传**：阶段粒度自动恢复
- **独立 skill**：不依赖 flow-trace-db，共享 graph.db 和 Java 源码

## 目录结构

```
├── SKILL.md              ← 本文件
├── prompt.md             ← 入口提示词
├── phases/               ← 各阶段规格说明（.md）
├── scripts/              ← Python 执行脚本
│   ├── phase0_discovery.py      ← Phase 0: 表清单解析 + graph.db 对齐
│   ├── phase1_ownership.py      ← Phase 1: 归属链构建
│   ├── phase2_crud_analysis.py  ← Phase 2: CRUD 操作分析
│   ├── phase3_state_inference.py ← Phase 3: 状态流转推断
│   ├── phase4_flow_coverage.py  ← Phase 4: 流程关联 + 覆盖度
│   └── phase5_doc_gen.py        ← Phase 5: 文档生成
└── templates/            ← 文档模板
```

**执行方式**：各 Phase 的 Python 脚本独立运行，从 `data-model-db/.cache/` 读取上游数据。
