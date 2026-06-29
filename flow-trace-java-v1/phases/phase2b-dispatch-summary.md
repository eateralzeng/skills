# Phase 2b: 分发点分析

## 概述

LLM 子代理读取分发点实现类的源码，提取路由条件和关键下游调用，生成汇总文件。
Phase 2b 只输出 dispatch-summary 文件，不修改其他阶段的文件。

## 输入

- `phase1c/pattern-index.json` — 分发点索引
- `phase1b/db-schema-lookup.json` — DB Schema lookup

## 输出

- `phase2b/dispatch-summary-{patternName}.json` — 每个分发点的汇总

`patternName` 取接口的短类名，如 `CustomerQueryStrategy`。

## 前置条件

- Phase 1c 已完成（pattern-index.json 已生成）

## 执行步骤

```
1. 读取 phase1c/pattern-index.json，获取所有分发点（仅排除 `_verified is False`；缺失 `_verified` 按 detect 处理，CR-02）
2. 对每个分发点：
   a. 准备子代理输入：
      - {{interface}}：接口全限定名
      - {{interface_methods}}：接口方法列表（JSON 数组字符串）
      - {{dispatch_type}}：分发类型
      - {{implementations}}：实现类列表（JSON 数组字符串，包含 class、filePath、module、parentAbstract）
      - {{db_schema_lookup}}：Phase 1b 的 lookup 字段
      - {{project_dir}} 和 {{output_path}}
   b. 派发 LLM 子代理（prompts/phase2b-dispatch-analyze.md）
   c. 子代理输出 dispatch-summary-{patternName}.json 到 phase2b/ 目录
3. 如果分发点的实现类数量 > 30，将实现类分批（每批 18 个）：
   - 每批派发一个子代理，输出到临时文件 `phase2b/tmp/_batch-result-{patternName}-{N}.json`
   - 合并多批结果：`python3 phase2b_dispatch_prepare.py --mode merge --cache-dir <cache> --pattern-name <name> --results <batch_dir>`（batch_dir 应为 `phase2b/tmp/`）
   - merge 模式自动按 `class` 字段去重，从 `phase2b/tmp/_prepare-context-{patternName}.json` 获取 interface/dispatchType
```

## 与 Phase 2a 的关系

- Phase 2a 和 Phase 2b 可并行执行
- Phase 2a 的 BFS 子代理在遇到分发点时标记 DISPATCH 终点（不展开）
- Phase 2b 独立分析分发点的实现类（不管 BFS 树结构）
- 两者通过 `patternRef` 关联：DISPATCH 节点的 patternRef 指向 dispatch-summary 文件
- DISPATCH 节点的子节点补充由 Phase 4a 完成（不在 Phase 2b 中处理）

## dispatch-summary 文件格式

```json
{
  "interface": "com.webank.cbrc.bs.strategy.cust.CustomerQueryStrategy",
  "dispatchType": "STREAM_DISPATCH",
  "results": [
    {
      "class": "com.webank.cbrc.bs.strategy.cust.impl.PersonAcctQueryStrategy",
      "shortName": "PersonAcctQueryStrategy",
      "condition": "OrgType=PERSON, OperateType=ACCT_QUERY",
      "endpoints": [
        {
          "class": "PersonAccMapper",
          "method": "selectByAcctNo",
          "filePath": "cbrc-bs/src/main/java/.../PersonAccMapper.java",
          "type": "DATABASE",
          "table": "person_acc",
          "operation": "SELECT"
        }
      ]
    }
  ]
}
```

## 错误处理

- 实现类源文件不存在：endpoints 为空数组，condition 为 "unknown"
- 子代理返回格式异常：记录错误，保留已成功的结果，跳过失败的实现类
- 大规模分发点（50+ 实现类）分批处理，合并时按 class 去重
