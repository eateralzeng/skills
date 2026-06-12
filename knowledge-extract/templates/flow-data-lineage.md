# {{project_name}} 数据血缘分析报告

> 扫描日期: {{scan_date}} | 版本: v{{version}} | 项目: {{project_name}}

---

## 一、概览统计

| 指标 | 数值 |
|------|------|
| 数据库表总数 | {{total_tables}} |
| 跨流程数据依赖 | {{total_dependencies}} |
| 高风险依赖 | {{high_risk_count}} |
| 中风险依赖 | {{medium_risk_count}} |
| 低风险依赖 | {{low_risk_count}} |
| 涉及流程数 | {{involved_flow_count}} (Flow {{flow_range}}) |

### 风险等级分布

```
HIGH   ████████████░░░░░░░░  {{high_risk_count}}  ({{high_risk_ids}})
MEDIUM ████████░░░░░░░░░░░░  {{medium_risk_count}}  ({{medium_risk_ids}})
LOW    ████████████████░░░░  {{low_risk_count}}  ({{low_risk_ids}})
```

---

## 二、表级血缘分析

{{#each table_groups}}
### {{group_index}}.{{sub_index}} {{group_title}}

{{#each tables}}
#### {{table_name}}（{{table_description}}）

{{#if risk_level}}
> **风险等级**: {{risk_level}} | **涉及流程**: {{involved_flow_count}}个 | **跨流程依赖**: {{cross_flow_dep_count}}条
{{/if}}

| 角色 | 流程 | 操作 |
|------|------|------|
{{#if producers}}
| **生产者 (Producer)** | {{producer_rows}} |
{{/if}}
{{#if consumers}}
| **消费者 (Consumer)** | {{consumer_rows}} |
{{/if}}
{{#if updaters}}
| **更新者 (Updater)** | {{updater_rows}} |
{{/if}}

{{#if key_data_flow}}
**关键数据流**: {{key_data_flow}}
{{/if}}

{{#if special_note}}
**{{special_note_title}}**: {{special_note}}
{{/if}}

---
{{/each}}

{{/each}}

## 三、依赖关系图

### 3.1 核心业务链路（ASCII图）

```
                    ┌─────────────────────────────────────────┐
                    │          {{external_system_desc}}         │
                    └────────────┬────────────────────────────┘
                                 │ {{transport_protocol}}
                    ┌────────────┴────────────┐
                    │                         │
              ┌─────┴─────┐            ┌──────┴──────┐
              │ {{flow_a}}  │            │  {{flow_b}}   │
              │ {{flow_a_desc}}│          │ {{flow_b_desc}} │
              └─────┬──────┘            └──────┬──────┘
                    │                         │
           {{op_type_1}}   │                         │  {{op_type_2}}
                    ▼                         ▼
        ┌───────────────────────────────────────────┐
        │     {{shared_table_1}}                  │
        │     {{shared_table_2}}                          │
        └───┬──────────┬──────────┬────────────────┘
            │          │          │
     {{op_type_3}} │   {{op_type_4}} │   {{op_type_5}}│
            ▼          ▼          ▼
      ┌──────────┐ ┌──────────┐ ┌──────────┐
      │ {{flow_c}} │ │ {{flow_d}} │ │ {{flow_e}} │
      │ {{flow_c_desc}}│ │ {{flow_d_desc}}│ │ {{flow_e_desc}}│
      └──────────┘ └──────────┘ └─────┬────┘
```

### 3.2 {{secondary_chain_title}}

```
                ┌──────────────────┐
                │  {{secondary_source}}  │
                └────────┬─────────┘
                         │ {{secondary_protocol}}
                  ┌──────┴──────┐
                  │  {{secondary_flow}}   │
                  └──────┬──────┘
                         │
          ┌──────────────┼──────────────────┐
          │              │                  │
          ▼              ▼                  ▼
  ┌───────────────┐ ┌─────────────┐ ┌──────────────────┐
  │ {{sec_table_1}} │ │{{sec_table_2}} │ │ {{sec_table_3}}  │
  └───────────────┘ └──────┬──────┘ └────────┬─────────┘
```

---

## 四、依赖详情表

| 编号 | 类型 | 风险 | 表名 | 操作对 | 源流程 | 目标流程 | 说明 |
|------|------|------|------|--------|--------|----------|------|
| {{dependency_rows}} |

---

## 五、风险分析总结

### 5.1 高风险项（需优先处理）

| 风险项 | 涉及表 | 问题 | 建议 |
|--------|--------|------|------|
| {{high_risk_rows}} |

### 5.2 中风险项（建议优化）

| 风险项 | 涉及表 | 问题 | 建议 |
|--------|--------|------|------|
| {{medium_risk_rows}} |

### 5.3 表操作模式分类

| 模式 | 表 | 说明 |
|------|----|------|
| **跨流程生产-消费** | {{producer_consumer_tables}} | 一组Flow写入，另一组Flow读取 |
| **双向共享** | {{bidirectional_tables}} | 两个Flow互相读写 |
| **并发写入** | {{concurrent_write_tables}} | 多个Flow同时UPDATE |
| **单流程独占** | {{exclusive_tables}} | 单一Job内部自引用 |
| **纯只读引用** | {{readonly_tables}} | 仅SELECT，无写入操作 |
| **无生产者** | {{no_producer_tables}} | 缺少INSERT来源，需确认数据来源 |

---

## 六、流程ID映射（v{{version}} 重编号对照）

| 新编号 | 名称 | 类型 | 说明 |
|--------|------|------|------|
| {{flow_id_mapping_rows}} |
