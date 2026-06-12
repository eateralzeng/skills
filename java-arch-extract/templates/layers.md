# {{project_name}} - 分层架构

> 模块：{{module_name}}
> 层次数：{{layer_count}}
> 总类数：{{total_classes}}

---

## 1. 分层架构图

```
{{layer_diagram}}
```

---

## 2. 各层详情

{{layer_details}}

### {{layer_name}}层

| 类名 | 包路径 | 注解 | 文件 |
|------|--------|------|------|
{{class_rows}}

子包结构：
{{sub_package_tree}}

---

## 3. 层间依赖

| from | to | 状态 |
|------|----|------|
{{layer_dependency_rows}}

---

## 待确认项

{{gap_items}}