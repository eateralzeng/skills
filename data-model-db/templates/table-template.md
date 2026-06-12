# 表生命周期：{{table_name}}

> 模块：{{primary_module}}
> 生成器：data-model-db | 数据源：graph-db + 源码

---

## 1. 基本信息

| 项目 | 详情 |
|------|------|
| 表名 | {{table_name}} |
| 来源 | {{source}} |
| Mapper | {{mapper_list}} |
| 所属 Service | {{service_list}} |
| 关联流程 | {{flow_count}}个 |
| 覆盖状态 | {{coverage_status}} |

---

## 2. CRUD 操作

| 操作类型 | 方法数 | 主要方法 |
|---------|--------|---------|
| SELECT | {{select_count}} | {{select_methods}} |
| INSERT | {{insert_count}} | {{insert_methods}} |
| UPDATE | {{update_count}} | {{update_methods}} |
| DELETE | {{delete_count}} | {{delete_methods}} |

---

## 3. 状态流转

{{#each stateTransitions}}
字段：{{field}} ({{enumClass}})
匹配方式：{{matchType}}
置信度：{{confidence}}

```
{{transition_diagram}}
```

{{/each}}

{{^stateTransitions}}
无状态流转字段
{{/stateTransitions}}

---

## 4. 归属关系

### 模块归属
| 模块 | Service | 操作 |
|------|---------|------|
{{#each ownership.services}}
| {{module}} | {{className}} | 读/写 |
{{/each}}

### 关联流程
| 流程 | 类型 | 操作 |
|------|------|------|
{{#each flowCoverage.flows}}
| {{entryId}} | {{entryType}} | {{operations}} |
{{/each}}

---

## 5. 领域归属提示（需人工审核）

> 以下为自动化提示，不作为确定性结论，需结合业务知识人工判断。

- 状态流转丰富度：{{state_richness}}
- 共享 Service 数量：{{shared_service_count}}
- 跨模块操作：{{cross_module_services}}
- 潜在聚合根特征：{{aggregate_root_feature}}
