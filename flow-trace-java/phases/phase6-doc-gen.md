# Phase 6: 文档生成

## 概述

纯脚本阶段。从语义填充后的调用树生成 Markdown 流程文档。

## 输入

- `phase5/{entryId}-semantics.json` — 语义填充后的数据（优先）
- `phase4/` — 桥接合并后的数据（降级）
- `phase3/{entryId}-pruned.json` — 剪枝后的数据（降级）
- `phase1a/entries.json` — 入口列表
- `templates/flow-template.md` — 输出模板

## 输出

- `flows/**/*.md` — 每个入口一个 Markdown 文档
- `flow-detail.json` — 详细流程信息
- `flow-summary.json` — 流程汇总
- `flow-data-lineage.json` — 数据血缘

## 前置条件

- Phase 5 已完成（或 Phase 5/4 已完成）

## 执行步骤

运行脚本：

```bash
python3 <skill_dir>/scripts/phase6_doc_gen.py \
  --cache-dir <cache_dir> \
  --output-dir <output_dir> \
  --entries <entries_path> \
  --template <template_path>
```

## 模板变量映射

| 模板变量 | 来源 |
|---------|------|
| flow_name | 入口 className.methodName |
| entry_type | 入口类型（controller/rmb/job） |
| business_overview | 入口描述 + 业务步骤列表 |
| call_chain_tree | 树的缩进格式调用链 |
| data_operations_table | domainInteraction.type=DATABASE 的节点 |
| external_calls_table | domainInteraction.type=EXTERNAL/MQ 的节点 |
| has_rmb_bridges | 是否存在桥接元数据 |
| rmb_bridge_section | 桥接信息（如适用） |
| dispatch_tables | DISPATCH 节点的分发路由表（从 dispatch-summary 文件生成） |

## DISPATCH 节点渲染

DISPATCH 节点渲染为两部分：

1. **树结构中**：显示 `[多态分发 - N个实现类]` 标记，子节点带实现类标记 `(ImplName)`
2. **分发路由表**：通过 patternRef 从 dispatch-summary 文件读取，渲染为表格：

```
### 分发路由：InterfaceName

| 路由条件 | 实现类 | 涉及的数据库操作 |
|---------|--------|-----------------|
| OrgType=PERSON | PersonAcctQueryStrategy | person_acc.SELECT |
| OrgType=CORP | CorpAcctQueryStrategy | corp_acc.SELECT |
```

## 输出文件结构

```
<output_dir>/
├── flows/
│   ├── controller/
│   │   └── ClassName/
│   │       └── methodName.md
│   ├── rmb/
│   │   └── ClassName/
│   │       └── methodName.md
│   └── job/
│       └── ClassName/
│           └── methodName.md
├── flow-summary.json
├── flow-detail.json
└── flow-data-lineage.json
```

## 错误处理

- 入口无流程数据：跳过
- chain 为空：跳过
- flowStatus=NO_ENDPOINT：跳过，不生成文档
