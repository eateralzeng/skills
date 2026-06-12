# Phase 7: 校验

> 五维度校验，以外部 table-list 为核心基准

## 概述

Phase 7 对全流程产出进行五维度质量校验，生成校验报告（JSON + MD）。

## 校验维度

### D1: 入口完备性（不变）

源码注解扫描 vs entries，检查入口是否遗漏或多余。

- 扫描源码中的 `@RequestMapping`/`@RmbTopic`/`@Scheduled` 注解
- 与 entries.json 交叉比对
- WARNING: 源码中有但 entries 中无
- ERROR: entries 中有但源码文件不存在

### D2: 链路合理性（降级为轻量检查）

**不使用 graph.db 做终点全覆盖对比**。graph.db 本身有置信度问题，用它校验自己产出的 chain 不可靠。

检查项：
- chain 不能为空（入口至少应触达一些终点）
- chain 中的终点节点都有 domainInteraction 数据
- chain 状态不是 TRUNCATED/PARTIAL（如果是则标记 INFO）

### D3: 数据库表覆盖（核心校验，升级）

以用户提供的 `table-list` 为基准（来自真实数据库，不受 graph.db 置信度影响）：
- table-list 中的表 vs 流程中 domainInteraction 实际触达的表
- 未覆盖的表 → WARNING
- 流程引用但不在 table-list 中的表 → INFO
- 输出覆盖率统计

> 如果未提供 table-list，回退到 db-schema.json 作为基准。

### D4: RMB 桥接准确性（不变）

校验桥接匹配和 merged flow 完整性：
- MATCHED 桥接的 merged flow 文件是否存在
- merged flow 中是否有 BRIDGE 节点
- UNMATCHED 桥接的 topic 是否在源码中可找到接收端

### D5: 业务描述质量（新增）

检查描述填充质量：
- **空描述**：description 为空字符串（WARNING）
- **机械翻译**：描述是方法名的直接翻译，如"获取AbsolutePath"（WARNING）
- **终点描述缺上下文**：终点节点的描述未引用表名或 Topic（INFO）

## 执行

运行 `scripts/phase7_validate.py`：

```bash
python3 scripts/phase7_validate.py <project_dir> <cache_dir> <output_dir> <entries_path> [--table-list <path>]
```

## 输出

- `validate-report.json`：结构化校验报告
- `validate-report.md`：人类可读校验报告

## 报告格式

```json
{
  "version": "2.0",
  "overallStatus": "PASS | PASS_WITH_WARNINGS | FAIL",
  "dimensions": {
    "D1_entryCompleteness": {...},
    "D2_chainSanity": {...},
    "D3_databaseCoverage": {"stats": {"coverageRate": "85.0%"}},
    "D4_rmbBridgeAccuracy": {...},
    "D5_descriptionQuality": {...}
  }
}
```
