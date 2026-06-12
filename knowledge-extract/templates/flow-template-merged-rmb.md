# 流程追踪：{{flow_name}}（{{flow_description}}）

> 模块：{{project_name}} / {{sender_module}}
> 包路径：`{{entry_package}}`
> 类型：**{{entry_type}}** | 分类：**MERGED_RMB_FLOW**

---

## 1. 入口信息

| 项目 | 详情 |
|------|--------|
| 控制器 | `{{entry_class}}` |
| 包路径 | `{{entry_package}}` |
| 所属层 | {{layer_description}} |
| 功能说明 | {{business_description}} |

### 入口方法

| # | HTTP方法 | URL | 方法名 | 说明 |
|---|-------------|-----|-------------|-------------|
| 1 | {{http_method_1}} | `{{api_path_1}}` | `{{method_name_1}}` | {{method_description_1}} |
| 2 | {{http_method_2}} | `{{api_path_2}}` | `{{method_name_2}}` | {{method_description_2}} |

### 统一调用

所有入口方法统一委托给：
```
{{service_class}}.{{service_method}}({{params}})
```

---

## 2. 调用链路图

```
{{entry_trigger}}
   |
   v
+----------------------------------------------+
| {{entry_class}}                 |
|  - {{method_list}}                        |
+----------------------------------------------+
   |  {{service_method}}({{params}})
   v
+----------------------------------------------+
| {{service_class}}                         |
|  {{service_method}}()                         |
+----------------------------------------------+
   |          |              |               |
   v          v              v               v
+--------+ +----------+ +---------+ +------------------+
| {{step_1}} | {{step_2}} | {{step_3}} | {{rmb_client}}  |
| {{step_1_desc}} | {{step_2_desc}} | {{step_3_desc}} | {{rmb_client_desc}} |
+--------+ +----------+ +---------+ +------------------+
   |          |              |               |
   v          v              v               v
{{step_1_detail}}   {{step_2_detail}}   {{step_3_detail}}   RMB {{rmb_mode}}调用
```

---

## 3. 逐层调用详情

### 第1层：控制器（`{{entry_class}}`）

```
Class: {{entry_class}}
Package: {{entry_package}}
```

- {{controller_description}}

| 方法 | applyType值 | 说明 |
|--------|----------------|-------------|
| `{{method_name}}` | {{apply_type_value}} | {{method_description}} |

### 第2层：服务层（`{{service_class}}`）

```
Class: {{service_class}}
Method: {{service_method}}({{method_params}})
```

{{#each service_steps}}
#### 步骤 {{step_index}}：{{step_title}}

```
{{step_class}}.{{step_method}}({{step_params}})
```

- {{step_description}}

{{/each}}

### 第3层：外部系统

| 系统 | 客户端 | 说明 |
|--------|--------|-------------|
| {{external_system_1}} | `{{client_1}}` | {{external_desc_1}} |
| {{external_system_2}} | `{{client_2}}` | {{external_desc_2}} |

---

## 4. 数据库操作汇总

### 发送方（{{sender_module}}）-- 前置层

| 数据库 | 操作 | 表 | 说明 |
|----------|-----------|-------|-------------|
| **无** | -- | -- | 前置层不直接操作数据库，所有数据持久化通过RMB委托给后端({{receiver_module}})处理 |

### 接收方（{{receiver_module}}）-- 后端业务层

| DAO | 操作 | 表 | 说明 |
|-----|-----------|-------|-------------|
| `{{receiver_dao_class}}` | {{receiver_db_op}} | `{{receiver_db_table}}` | {{receiver_db_op_description}} |

### 外部系统交互

| 系统 | 方向 | 协议 | 说明 |
|--------|-----------|----------|-------------|
| {{external_system}} | {{direction}} | {{protocol}} | {{interaction_description}} |

---

## 5. RMB 桥接

| 属性 | 值 |
|------|-----|
| Topic | `{{rmb_topic}}` |
| 通信模式 | {{rmb_mode}} |
| 发送方模块 | {{sender_module}} |
| 发送方入口 | `{{sender_entry_class}}.{{sender_methods}}` |
| 接收方模块 | {{receiver_module}} |
| 接收方入口 | `{{receiver_entry_class}}` |
| 对端是否外部 | {{is_external}} |

### 端到端链路图

```
[发送方入口] {{sender_entry_class}} ({{sender_module}})
  │
  ├── [1] {{sender_service_class}}.{{sender_service_method}}()
  │     ├── [1.1] {{sender_sub_call_1}}()        -- {{sender_sub_call_1_desc}}
  │     ├── [1.2] {{sender_sub_call_2}}()        -- {{sender_sub_call_2_desc}}
  │     └── [1.3] {{sender_sub_call_3}}()        -- {{sender_sub_call_3_desc}}
  │
  └── [2] [RMB Client] {{rmb_client_class}}.{{rmb_client_method}}()
        │  Topic: {{rmb_topic}} ({{rmb_mode}})
        │
        ▼
  ═══════════════ [RMB 桥接] ═════════════════════════════
        │
        ▼
  [接收方入口] {{receiver_entry_class}} ({{receiver_module}})
  │
  ├── [3] {{receiver_service_class}}.{{receiver_service_method}}()
  │     │
  │     ├── [3.1] {{receiver_dao_class}}.{{receiver_dao_method}}()
  │     │     └── [DB] {{receiver_db_op_1}} {{receiver_db_table_1}}
  │     │         {{receiver_db_op_1_description}}
  │     │
  │     ├── [3.2] {{receiver_dao_class_2}}.{{receiver_dao_method_2}}()
  │     │     └── [DB] {{receiver_db_op_2}} {{receiver_db_table_2}}
  │     │         {{receiver_db_op_2_description}}
  │     │
  │     └── [3.3] {{receiver_external_call}}
  │           └── {{receiver_external_call_desc}}
  │
  └── [4] {{receiver_update_dao}}.{{receiver_update_method}}()
        └── [DB] {{receiver_db_op_final}} {{receiver_db_table_final}}
            {{receiver_db_op_final_description}}
```

---

## 6. 错误处理

```
{{error_flow_root}}
   |
   +--> {{error_branch_1}} --> {{error_action_1}} --> {{error_result_1}}
   |
   +--> {{error_branch_2}} --> {{error_action_2}} --> {{error_result_2}}
   |
   +--> {{error_branch_3}} --> {{error_action_3}} --> {{error_result_3}}
   |
   +--> {{error_branch_success}} --> {{success_action}} --> {{success_result}}
```

---

## 7. 时序图（文本）

```
{{participant_1}}       {{participant_2}}          {{participant_3}}         {{participant_4}}     {{participant_5}}    {{participant_6}}       {{participant_7}}
  |               |               |               |             |              |              |
  |---POST------->|               |               |             |              |              |
  |               |--handleReq-->|               |             |              |              |
  |               |               |--step1------>|             |              |              |
  |               |               |<--result1---|             |              |              |
  |               |               |--step2------------------>|              |              |
  |               |               |<--result2-----------------|              |              |
  |               |               |--step3()---------------------------->|              |
  |               |               |<--result3--------------------------------------------|
  |               |<--result------|               |             |              |              |
  |<--响应--------|               |               |             |              |              |
```

---

*{{project_name}}流程追踪文档自动生成。*
