# {{project_name}} - 架构分析汇总报告

> 分析日期：{{scan_date}}
> 总模块数：{{module_count}}
> 总类数：{{total_class_count}}

---

## 1. 架构概览

| 指标 | 数量 |
|------|------|
| 模块数 | {{module_count}} |
| 分层数 | {{layer_count}} |
| 总类数 | {{total_class_count}} |
| 接口数 | {{interface_count}} |
| 抽象类数 | {{abstract_class_count}} |
| 识别设计模式 | {{pattern_count}} |

---

## 2. 各层分布

| 层 | 类数 | 占比 | 子包数 |
|----|------|------|--------|
{{layer_distribution_rows}}

---

## 3. 关键抽象与接口

{{key_abstractions}}

---

## 4. 设计模式统计

| 模式 | 出现次数 | 置信度 |
|------|---------|--------|
{{pattern_stats_rows}}

---

## 5. 架构风险点

| # | 类型 | from → to | 位置 | 建议 |
|---|------|-----------|------|------|
{{risk_rows}}

---

## 6. 改进建议

{{improvement_suggestions}}

---

## 待确认项

{{gap_items}}