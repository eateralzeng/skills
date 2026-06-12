# {{table_name}}

> 模块：{{module}}
> 实体类：{{entity_class}}
> 主键：{{primary_key}}
> 所属域：{{domains}}

---

## 1. 表结构

{{field_groups}}

---

## 2. 索引

{{index_section}}

---

## 3. CRUD 操作

| 操作 | DAO 方法 | Service 调用方 | 说明 |
|------|---------|--------------|------|
{{crud_rows}}

---

## 4. 生命周期

{{lifecycle_overview}}

### 状态字段

| 字段 | 含义 | 枚举值 |
|------|------|--------|
{{status_field_rows}}

### 全景生命周期图

```
{{lifecycle_diagram}}
```

### 各阶段说明

{{stage_details}}

### 状态流转图

```
{{state_transition_diagram}}
```

### 跨流程数据依赖 [ref: flow-trace flow-data-lineage]

| 依赖编号 | 依赖类型 | 操作对 | 源流程 | 目标流程 | 风险 |
|---------|---------|--------|--------|---------|------|
{{cross_flow_dependency_rows}}

{{cross_flow_dependency_notes}}

### 典型成功路径

```
{{typical_success_path}}
```

---

## 5. 关联表

| 关联表 | 关系 | 关联键 | 说明 |
|--------|------|--------|------|
{{related_table_rows}}

---

## 待确认项

{{gap_items}}

---

## 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| 1.0 | {{creation_date}} | 初始生成 |
