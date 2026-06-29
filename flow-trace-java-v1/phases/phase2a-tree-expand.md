# Phase 2a：调用树展开（Tree Expand）

> 编排者 Read 本文件获取 phase2a 详细执行逻辑。调度命令见 `prompt.md` 的「Phase 2a」段。

## 定位

从每个入口出发，BFS 逐层展开调用树，构建 **nodes + edges 分离的 DAG**（node 单实例 + edges 多 parent，彻底修复旧版 dict[nodeId] 的调用链丢失问题）。LLM 子代理读源码发现调用，脚本管理 tree.json + progress.json。

## 数据产物

| 产物 | 路径 | 说明 |
|---|---|---|
| 调用树 | `phase2a/{entryId}-tree.json` | version 2.0：`nodes`(dict) + `edges`(list)，DAG |
| 展开进度 | `phase2a/{entryId}-progress.json` | pendingNodes / expandedNodes / totalNodes / totalEdges + 控制参数 |
| 子代理临时文件 | `phase2a/tmp/` | `_prompt-*` / `_subagent-output-*` / `_reconcile-*` / `_llm-backfill-*` |

## 7 个 mode

脚本 `scripts/phase2a_tree_expand.py`（import `_tree_graph.py`）。编排者按序调用：

### 1. init — 初始化调用树
```
python3 phase2a_tree_expand.py --mode init --cache-dir <c> --entry-id <id> --entry <entries_path>
```
从 entries.json 取 entry，建 root node（nodeId=entry.nodeId，layer 隐式 0）+ 空 edges + progress。**幂等**：tree/progress 已存在则报错。

### 2. next-batch — 取待展开批次
```
python3 phase2a_tree_expand.py --mode next-batch --cache-dir <c> --entry-id <id> --batch-size 15
```
取 pendingNodes 前 N；**layer 从入边取 min**（root 无入边→0）。输出 `{batch, hasMore, remainingCount}` 到 stdout（编排者拿去渲染 prompt）。

### 3. merge — 合并子代理结果（核心）
```
python3 phase2a_tree_expand.py --mode merge --cache-dir <c> --entry-id <id> --results <subagent_output> --project-dir <project>
```
- **保序**：calls 按 sourceLine 排序 + sourceSnippet 校验（决策 13）
- **node 单实例去重，但 edge 照建**：同一 nodeId 在 nodes 一份，多 parent 调用通过 edges 表达（**修复调用链丢失的核心**）
- **DI lookup 兜底**：terminal 且 DI 为空的节点用 db-schema-lookup 补 DATABASE
- `--project-dir`：读 parent 源码做 snippet 校验（拍板点①）

### 4. reconcile-prepare — 跨 entry 后校验（扫描）
```
python3 phase2a_tree_expand.py --mode reconcile-prepare --cache-dir <c>
```
扫描所有 tree，检测：① 跨 entry 不一致（TERMINAL/CHILDREN_COUNT/CHILDREN_SET MISMATCH，bestEntry=出边最多者）；② 零调用可疑（非终点有 filePath 但 0 出边，suspicion=MEDIUM）。输出 `_reconcile-report.json`。

### 5. reconcile-apply — 应用修正
```
python3 phase2a_tree_expand.py --mode reconcile-apply --cache-dir <c>
```
读 `_reconcile-result-*.json`（LLM 重分析）+ bestEntry 兜底。**引用计数删子树**（DAG 下删出边后 to 节点引用计数=0 才删 node）+ 按权威 calls 重建。

### 6. llm-backfill-prepare — 收集 DI 缺失节点
```
python3 phase2a_tree_expand.py --mode llm-backfill-prepare --cache-dir <c> --project-dir <project>
```
收集 terminal 且 domainInteraction 为空的节点（按 nodeId 去重，跨 entry），filePath 为空时用 class 全限定名反推。输出 `_llm-backfill-context.json`。

### 7. llm-backfill-apply — 回写 DI（幂等）
```
python3 phase2a_tree_expand.py --mode llm-backfill-apply --cache-dir <c> --results <di_output>
```
按 nodeId 精确匹配回写 domainInteraction（**幂等**：只覆盖 null）。

## BFS 主循环（每个入口）

```
init
  → [next-batch → 渲染 discover prompt → 派发子代理 → merge] 循环（pending 空止）
  → 所有入口 BFS 完成
  → reconcile-prepare → 重分析子代理×N → reconcile-apply（若引入新 pending，回 BFS）
  → llm-backfill-prepare → di-backfill 子代理 → llm-backfill-apply
  → 更新 progress.json（phase2a = COMPLETED）
```

## discover 子代理（调用发现）

读 `prompts/phase2a-discover.md`，替换 7 个模板变量：

| 变量 | 来源 |
|---|---|
| `{{nodes}}` | next-batch 输出（nodeId/class/method/filePath/layer） |
| `{{project_dir}}` / `{{output_path}}` | 路径 |
| `{{noise_rules}}` | `rules/filter-rules.md` 全文内联 |
| `{{endpoint_rules}}` | `rules/endpoint-rules.md` 全文内联 |
| `{{db_schema_lookup}}` | `phase1b/db-schema-lookup.json` 的 lookup 字段内联 |
| `{{pattern_index}}` | `phase1c/pattern-index.json` 的 patterns 字段内联 |

子代理三层判断（噪声丢弃 / 终点标记 / 可穿透），**按源码顺序**输出 calls（每条含 `sourceLine`+`sourceSnippet`+`condition`，决策 13）。reconcile 重分析复用同 prompt（单节点 batch）。

## 关键约束（决策 12）

- **node 单实例 + edge 照建**：`parent_2 → A` 的 edge 必须创建（node 去重不丢 edge）
- **layer 在 edge 上**：node 无 layer 字段，从入边取 min（最短路径深度）
- **保序**：calls 按 sourceLine 排序，不信任 LLM 原始顺序
- **控制参数**：maxDepth=20 / maxNodes=500 / maxFanout=10（超限标 truncated）
- **terminal vs truncated**：node.terminal（方法是否终点，静态）/ edge.truncated（调用是否工程截断，动态）
