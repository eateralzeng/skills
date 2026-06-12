# Phase 3: 路径剪枝

## 概述

纯脚本阶段。从 Phase 3 的调用树中剪掉无法抵达任何终端节点的分支。

## 输入

- `phase2a/{entryId}-tree.json` — 展开后的调用树
- `phase1a/entries.json` — 入口列表

## 输出

- `phase3/{entryId}-pruned.json` — 剪枝后的数据

## 前置条件

- Phase 2a 已完成（所有入口的 BFS 展开）

## 执行步骤

运行脚本：

```bash
python3 <skill_dir>/scripts/phase3_path_prune.py --cache-dir <cache_dir> --entries <entries_path>
```

脚本自动处理所有入口。

## 算法

1. 对每个入口：
   a. 读取 `phase2a/{entryId}-tree.json`
   b. 找到所有 `terminal=true` 的节点
   c. 从每个终端节点沿 `parentId` 回溯到根，标记沿途节点为"保留"
   d. 未标记的节点移入 `prunedNodes`（附 reason `not_on_terminal_path`）
   e. 无终端节点 → `flowStatus: NO_ENDPOINT`
   f. 写入 `phase3/{entryId}-pruned.json`

## DISPATCH 节点处理

DISPATCH 节点在 Phase 3 中已被标记为 `terminal=true`，携带 `endpointType: "DISPATCH"` 和 `patternRef` 字段。剪枝时：

- DISPATCH 节点视为有效终点（携带 patternRef，说明有汇总信息），不会被剪掉
- DISPATCH 节点此时没有子节点（子节点在 Phase 5a 补充），按终点处理
- `endpointType` 和 `patternRef` 字段原样传递到剪枝后输出，供 Phase 5a 使用

路径保留标准：能到达真实终点（DATABASE / RMB_EXTERNAL / HTTP_EXTERNAL / MQ_PUBLISH / FILE_WRITE / DISPATCH 等）的路径保留。

## 输出文件格式

```json
{
  "entryId": "...",
  "flowStatus": "VALID | NO_ENDPOINT",
  "chain": ["...保留的节点列表..."],
  "prunedNodes": [
    {"nodeId": "...", "class": "...", "method": "...", "layer": 0, "reason": "not_on_terminal_path"}
  ],
  "summary": {"retained": 30, "pruned": 15, "terminals": 5}
}
```

chain 中的节点保留 `endpointType` 和 `patternRef` 字段（如果原始节点有这些字段）。

## 错误处理

- 入口无 tree 文件：跳过，输出警告
- 入口无终端节点：flowStatus=NO_ENDPOINT，所有节点移入 prunedNodes
