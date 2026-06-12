# 流程追踪：{{flow_name}}

> 模块：{{project_name}} / {{module_name}}
> 包：`{{entry_package}}`
> 类型：**RMB 接收端 (扇入)** | 分类：**FANIN_RMB_FLOW**
> 共享 Topic：`{{shared_topic}}` | Handler 数量：{{handler_count}}

---

## 1. 入口信息

| 项目 | 详情 |
|------|--------|
| Topic | `{{shared_topic}}` |
| 通信模式 | {{rmb_mode}} |
| 接收方模块 | {{module_name}} |
| Handler 数量 | {{handler_count}} |
| 发送方 | {{sender_description}} |
| 路由方式 | {{routing_method}} (如 transCode) |

### Handler 清单

| # | Handler 类 | TransCode / 路由Key | 方法 | 说明 |
|---|-----------|---------------------|------|------|
| {{handler_rows}} |

---

## 2. 调用链图

```
[外部发送方] {{sender_description}}
   │
   │ RMB {{rmb_mode}} (Topic: {{shared_topic}})
   │ 路由: {{routing_method}}
   │
   ▼
┌──────────────────────────────────────────────────────┐
│  {{module_name}} - {{handler_count}} 个 Handler       │
│  每个 Handler 按 {{routing_method}} 路由到对应处理方法    │
└──────────────────────────────────────────────────────┘
   │
   ├── [Handler 1] {{handler_class_1}}.{{handler_method_1}}()
   │     ├── {{handler_1_service_call}}()
   │     │     └── [DB] {{handler_1_db_operations}}
   │     └── ...
   │
   ├── [Handler 2] {{handler_class_2}}.{{handler_method_2}}()
   │     └── ...
   │
   └── [Handler N] {{handler_class_n}}.{{handler_method_n}}()
         └── ...
```

---

## 3. Handler 调用详情

{{#each handlers}}
### {{handler_index}}. {{handler_class}} — {{handler_description}}

| 属性 | 值 |
|------|-----|
| 类名 | `{{handler_class_fqn}}` |
| 路由条件 | {{routing_key}} |
| 入口方法 | `{{handler_method}}()` |
| 继承 | `{{handler_extends}}` |

#### 调用链

```
{{handler_class}}.{{handler_method}}()
   │
   ├── [1] {{service_class}}.{{service_method}}()
   │     ├── [1.1] {{dao_class}}.{{dao_method}}()
   │     │     └── [DB] {{db_operation}} {{db_table}}
   │     │         {{db_operation_description}}
   │     └── ...
   └── [2] {{external_call}} (如有)
```

#### DB 操作明细

| DAO | 操作 | 表 | 说明 |
|-----|-----------|-------|-------------|
| {{handler_db_rows}} |

{{/each}}

---

## 4. 数据库操作汇总

| DAO | 操作 | 表 | Handler | 说明 |
|-----|-----------|-------|---------|------|
| {{all_db_operations_rows}} |

### 外部系统交互

| 系统 | 方向 | 协议 | Handler | 说明 |
|--------|-----------|----------|---------|------|
| {{external_system_rows}} |

---

{{#if has_execution_flow}}
## 5. 执行流程

```
{{entry_method}}() 被触发
   |
   v
{{execution_steps}}
```

---
{{/if}}

{{#if has_design_pattern}}
## {{section_number}}. 设计模式

```
{{design_pattern_diagram}}
```

---
{{/if}}

## 待确认项

{{gap_items}}

---

*{{project_name}} flow-trace 文档自动生成 (FANIN_RMB_FLOW 模板)。*
