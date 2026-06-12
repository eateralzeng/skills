# 流程追踪：{{flow_name}}

> 模块：{{project_name}} / {{module_name}}
> 包：`{{entry_package}}`
> 类型：**{{entry_type}}** | 分类：**STANDALONE_FLOW**

---

## 1. 入口信息

| 项目 | 详情 |
|------|--------|
| {{entry_type_label}} | `{{entry_class}}` |
| 包路径 | `{{entry_package}}` |
| 层级 | {{layer_description}} |
| 基类 | `{{base_class}}` |
| 用途 | {{business_description}} |

{{#if has_multi_module}}
### 双模块实现

该 {{entry_type}} 存在于多个后端模块中，数据源不同但处理逻辑相似：

| 模块 | 包路径 | DAO | 说明 |
|--------|---------|-----|-------------|
| {{module_a}} | `{{module_a_package}}` | `{{module_a_dao}}` | {{module_a_description}} |
| {{module_b}} | `{{module_b_package}}` | `{{module_b_dao}}` | {{module_b_description}} |
{{/if}}

{{#if has_base_class_framework}}
### 继承的框架

```
{{base_class}}
  - {{framework_feature_1}}
  - {{framework_feature_2}}
  - 模板方法：{{template_method}} -- 子类实现
```
{{/if}}

---

## 2. 调用链图

```
{{entry_trigger}}
   |
   v
+----------------------------------------------------------+
| {{entry_class}}{{#if base_class}} extends {{base_class}}{{/if}}       |
|  - {{entry_method}}()                                      |
+----------------------------------------------------------+
   |
   v
+----------------------------------------------------------+
| {{service_class}}                             |
|  - {{service_method}}()                           |
+----------------------------------------------------------+
   |
   {{call_branches}}
```

### 调用链 DB 操作明细

```
[入口] {{entry_class}}.{{entry_method}}()
  │
  ├── [1] {{service_class}}.{{service_method}}()
  │     ├── [1.1] {{sub_call_1}}()              -- {{sub_call_1_desc}}
  │     ├── [1.2] {{dao_class}}.{{dao_method}}()
  │     │     └── [DB] {{db_op_1}} {{db_table_1}}
  │     │         {{db_op_1_description}}
  │     └── [1.3] {{dao_class_2}}.{{dao_method_2}}()
  │           └── [DB] {{db_op_2}} {{db_table_2}}
  │               {{db_op_2_description}}
  │
  └── [2] {{external_or_rmb_call}}
```

---

## 3. 逐层调用详情

### 第1层：{{entry_type_label}}（`{{entry_class}}`）

```
Class: {{entry_class}}{{#if base_class}} extends {{base_class}}{{/if}}
Packages:
{{package_list}}
Method: {{entry_method}}()
```

- {{entry_layer_description}}

### 第2层：{{service_layer_title}}（`{{service_class}}`）

```
Class: {{service_class}}
Methods:
  - {{service_method_list}}
```

{{#each service_methods}}
#### 方法 {{method_index}}：`{{method_name}}`（{{method_description}}）

- {{method_step_list}}

{{/each}}

### 第{{n}}层：{{sub_layer_title}}

{{sub_layer_details}}

### 第{{m}}层：外部系统

| 系统 | 客户端 | 说明 |
|--------|--------|-------------|
| {{external_system_name}} | {{external_client}} | {{external_description}} |

---

## 4. 数据库操作汇总

{{#each db_modules}}
### {{module_name}} 模块

| DAO | 操作 | 表 | 说明 |
|-----|-----------|-------|-------------|
| `{{dao_class}}` | {{operation}} | `{{table_name}}` | {{operation_description}} |

{{/each}}

### 外部系统交互

| 系统 | 方向 | 协议 | 说明 |
|--------|-----------|----------|-------------|
| {{external_system}} | {{direction}} | {{protocol}} | {{interaction_description}} |

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
## {{section_number}}. {{design_pattern_title}}

```
{{design_pattern_diagram}}
```

---
{{/if}}

{{#if has_sequence_diagram}}
## {{section_number}}. 时序图（文本）

```
{{participant_list}}
   |            |            |            |           |              |
   |--trigger-->|            |            |           |              |
   |            |--call----->|            |           |              |
   |            |            |------------|---------->|              |
   |            |            |<--result-------------|              |
   |            |            |            |           |              |
   |            |<--done-----|            |           |              |
```

---
{{/if}}

*为 {{project_name}} flow-trace 文档生成。*
