---
name: flow-trace-java-v1
description: 从 Java 代码工程中通过纯源码分析提取流程文档（无 graph.db 依赖）
user_invocable: true
---

# Flow Trace Java v1

> 纯源码分析，无需 graph.db，从 Java 代码工程中提取流程文档

## 使用方法

```
/flow-trace-java-v1 <project_path>
```

## 前置依赖

- Java 项目源码目录（存在 `pom.xml` 或 `build.gradle`）
- 无需 graph.db

## 核心特性

- **纯源码分析**：LLM 直接读取 Java 源码追踪调用链，不依赖 Atlas graph.db
- **DB Schema 预收集**：从 Mapper XML + Java 注解构建数据库操作 lookup
- **终点可配置**：通过 endpoint-rules.md 定义流程终点类型
- **全路径追踪**：覆盖直接调用、RMB 跨进程调用
- **规则预筛**：每层展开前用噪声规则过滤无关分支
- **逐节点业务描述**：每个节点生成独立的业务语义描述
- **RMB 桥接**：自动匹配同一代码库中的 RMB 发送端和接收端
- **断点续传**：阶段粒度自动恢复

## 与 flow-trace-db 的关系

flow-trace-java 是 flow-trace-db 的纯源码替代方案。当项目没有预构建的 graph.db 时使用。两者输出格式一致。

## 目录结构

```
├── SKILL.md              ← 本文件
├── prompt.md             ← 入口提示词（编排者）
├── phases/               ← 各阶段规格说明
├── prompts/              ← 子代理提示词模板
├── rules/                ← 配置规则
├── scripts/              ← Python 执行脚本
└── templates/            ← 输出模板
```

**执行方式**：优先直接运行 `scripts/*.py`，LLM 源码分析工作由子代理完成（阶段 3、阶段 6）。