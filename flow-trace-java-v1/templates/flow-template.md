# 流程追踪：{{flow_name}}

> 模块：{{project_name}}
> 类型：**{{entry_type}}**
> 生成器：**flow-trace-java** | 数据源：纯源码分析

---

## 1. 流程业务概述

{{business_overview}}

### 数据操作

| 操作 | 表 | 说明 |
|------|-----|------|
{{data_operations_table}}

### 外部调用

| 调用 | Topic | 说明 |
|------|-------|------|
{{external_calls_table}}

---

## 2. 完整调用链路

标记说明：[读] 数据库读取 | [写] 数据库写入 | [删] 数据库删除 | [RMB外调] RMB 外部调用

```
{{call_chain_tree}}
```

---

{{#if has_dispatch_tables}}
## 3. 分发路由详情

{{dispatch_tables}}

---

{{/if}}
{{#if has_rmb_bridges}}
## 4. RMB 桥接链路

{{rmb_bridge_section}}

---

{{/if}}
## {{section_number}}. 数据操作汇总

| 操作 | 表 | 节点 | 说明 |
|------|-----|------|------|
{{data_operations_summary}}

---

*为 {{project_name}} flow-trace-db 文档生成。*
