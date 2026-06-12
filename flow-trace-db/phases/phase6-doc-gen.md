# Phase 6: 文档生成

> 从修正后的 chain 数据生成 MD 文档，含流程业务概述

## 概述

Phase 6 是纯格式化输出阶段，不产生新信息。从修正后的 `{entryId}.json` 和 `bridges.json` 生成人类可读的 Markdown 文档。

## 输出结构

每个入口生成一个 MD 文件，包含 4 个章节：

### 第 1 章：流程业务概述（DDD 分析的主要输入）

聚合 chain 中所有节点的 description，生成：
- 业务步骤叙述（编号列表）
- 数据操作表（操作类型、表名、说明）
- 外部调用表（调用方法、Topic、说明）

### 第 2 章：完整调用链路

以 chain 为基础渲染完整调用树，不压缩。终点节点嵌入 `[读]`/`[写]`/`[删]`/`[RMB外调]` 标记。

### 第 3 章：RMB 桥接段落（如有）

独立展示接收端流程，包含接收端的业务概述和完整链路。

### 第 4 章：数据操作汇总表

按操作类型（读/写/删）分组，汇总所有终点的 domainInteraction。

## 终点标记

| 操作类型 | 标记 |
|---------|------|
| SELECT | `[读]` |
| INSERT | `[写]` |
| UPDATE | `[写]` |
| DELETE | `[删]` |
| RMB 外调 | `[RMB外调]` |

## 执行

运行 `scripts/phase6_doc_gen.py`：

```bash
python3 scripts/phase6_doc_gen.py <cache_dir> <output_dir> <entries_path>
```

## 输出

- `flows/**/*.md`：每个入口一个 MD 文件
- `flow-detail.json`：结构化流程数据
- `flow-summary.json`：流程摘要
- `flow-data-lineage.json`：数据操作血缘

## 设计原则

- JSON 保留完整 chain，不做任何压缩
- MD 文档中"流程业务概述"是 DDD 领域分析的主要输入
- 完整调用链路作为技术参考保留
