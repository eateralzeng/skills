# Phase 2a: 调用树展开（核心阶段）

## 概述

对每个入口，通过 BFS 逐层展开调用树。LLM 子代理读取源码识别方法调用，脚本管理树结构和进度。这是整个 skill 最关键的阶段。

## 输入

- `phase1a/entries.json` — 入口列表
- `phase1b/db-schema-lookup.json` — DB Schema lookup
- `rules/filter-rules.md` — 噪声过滤规则
- `rules/endpoint-rules.md` — 终点类型规则
- `prompts/phase2a-discover.md` — 子代理提示词模板

## 输出

- `phase2a/{entryId}-tree.json` — 每个入口的调用树
- `phase2a/{entryId}-progress.json` — 每个入口的展开进度

## 前置条件

- Phase 1 已完成
- Phase 2 已完成（可并行等待）

## 编排模型

三层模型：编排者 + 脚本 + 子代理

| 角色 | 实体 | 职责 |
|------|------|------|
| BFS 循环控制 | 编排者 (prompt.md) | 决定何时停止、派发批次、检查进度 |
| 树数据管理 | `phase2a_tree_expand.py` | 合并结果、去重、跟踪进度 |
| 源码读取 | 子代理 (prompts/phase2a-discover.md) | 读源码、识别调用、分类节点 |

## 执行流程

```
对每个入口 entry：
  1. 脚本初始化：
     python3 phase2a_tree_expand.py --mode init --cache-dir <cache> --entry-id <id> --entry <entries_path>

  2. BFS 循环：
     while True:
       a. 获取下一批：
          python3 phase2a_tree_expand.py --mode next-batch --cache-dir <cache> --entry-id <id> --batch-size 15

       b. 如果 batch 为空 → 跳出循环

       c. 准备子代理提示词：
          - 替换 {{nodes}} 为 batch 节点列表
          - 读取 filter-rules.md 内联到 {{noise_rules}}
          - 读取 endpoint-rules.md 内联到 {{endpoint_rules}}
          - 读取 db-schema-lookup.json 的 lookup 字段内联到 {{db_schema_lookup}}
          - 读取 phase1c/pattern-index.json 的 patterns 字段内联到 {{pattern_index}}
          - 设置 {{project_dir}} 和 {{output_path}}

       d. 派发子代理，传入提示词

       e. 合并结果：
          python3 phase2a_tree_expand.py --mode merge --cache-dir <cache> --entry-id <id> --results <output_path>

       f. 检查停止条件：
          - pendingNodes 为空
          - totalNodes >= maxNodes (500)
          - 任意节点 layer >= maxDepth (20)

  3. LLM domainInteraction 补全（所有入口 BFS 完成后）：
     a. 收集缺失节点：
        python3 phase2a_tree_expand.py --mode llm-backfill-prepare --cache-dir <cache> --project-dir <project>
     b. 如果缺失节点数 > 0：
        - 读取 prompts/phase2a-di-backfill.md，替换模板变量
        - 派发 LLM 子代理推测 domainInteraction
        - 回写结果：
          python3 phase2a_tree_expand.py --mode llm-backfill-apply --cache-dir <cache> --results <output>
     c. 如果缺失节点数 = 0：跳过

  4. 后校验补全（reconcile，所有入口 BFS 完成后、DI 补全前）：
     a. 扫描不一致：
        python3 phase2a_tree_expand.py --mode reconcile-prepare --cache-dir <cache>
        → 输出 phase2a/_reconcile-report.json
     b. 如果不一致节点数 > 0：
        - 对每个 needReAnalysis=true 的节点，构造单节点 batch
        - 派发子代理重新分析（复用 prompts/phase2a-discover.md 模板）
        - 保存子代理输出到 phase2a/_reconcile-result-{N}.json
        - 回写修正结果：
          python3 phase2a_tree_expand.py --mode reconcile-apply --cache-dir <cache>
     c. 如果不一致节点数 = 0：跳过
     d. 如果 reconcile 引入了新的 pending 节点，对受影响的入口继续 BFS 循环直到 pending 为空

  5. 更新全局进度 progress.json
```

## 节点结构

```json
{
  "nodeId": "模块名:包名.类名:方法名",
  "class": "...",
  "method": "...",
  "package": "",
  "filePath": "...",
  "layer": 0,
  "layerType": "ENTRY | INTERNAL | TERMINAL",
  "parentId": null,
  "callType": "DIRECT | POLYMORPHIC",
  "terminal": false,  // 子代理输出的 isEndpoint 映射而来；filePath 为空时脚本强制 true
  "description": "",
  "domainInteraction": null,
  "patternRef": null,  // 仅 DISPATCH 终点：指向 pattern-index 中的 interface 全限定名
  "children": []
}
```

## 节点分类与过滤（三层判断）

子代理对每个方法调用执行：

| 顺序 | 分类 | 处理 | 进入树 |
|------|------|------|--------|
| 1 | 噪声节点 | 匹配 filter-rules.md | 不进入，丢弃 |
| 2 | 终点节点 | 匹配 endpoint-rules.md + db-schema lookup | 进入，terminal=true |
| 3 | 分发点节点 | 匹配 pattern-index（DISPATCH） | 进入，terminal=true + patternRef，不展开 |
| 4 | 可穿透节点 | 默认 | 进入，加入待展开队列 |

## BFS 控制参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| maxDepth | 20 | 最大 BFS 深度 |
| maxNodes | 500 | 每入口最大节点数 |
| batchSize | 15 | 每批子代理处理数 |
| maxFanout | 10 | 每节点最大子节点数 |

## 去重

- nodeId = `模块名:包名.类名:方法名`
- 同入口树中相同 nodeId 只展开一次
- 跨入口不去重

## 断点续传

- 每批合并后脚本自动更新 `phase2a/{entryId}-progress.json`
- 恢复时编排者读取进度确定哪些入口已完成

## 错误处理

- 子代理返回异常：记录错误，跳过该批次节点，继续下一批
- 脚本 merge 失败：记录错误，不更新树，重新派发该批次
- 源文件不存在：子代理应标记该节点 calls 为空
