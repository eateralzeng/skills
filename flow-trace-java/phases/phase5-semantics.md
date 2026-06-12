# Phase 5: 业务语义填充

## 概述

LLM 子代理重新遍历剪枝/桥接后的调用树，为每个节点生成业务语义描述。这是 flow-trace-java 的核心增值部分。

## 输入

- `phase4/` 中的合并/未合并流程数据
- `phase3/` 中的剪枝后数据（phase4 不存在时的降级）
- `prompts/phase5-describe.md` — 子代理提示词模板

## 输出

- `phase5/{entryId}-semantics.json` — 包含业务描述的流程数据

## 前置条件

- Phase 4 已完成（或 Phase 3 已完成）

## 编排模型

与 Phase 2a 相同的三层模型：

| 角色 | 实体 | 职责 |
|------|------|------|
| 遍历控制 | 编排者 (prompt.md) | 遍历树、派发批次 |
| 描述管理 | 编排者直接更新 | 将描述合并到树中 |
| 源码读取 | 子代理 (prompts/phase5-describe.md) | 读源码、生成描述 |

## 执行流程

```
对每个入口 entry：
  1. 加载流程数据（优先 phase4，降级 phase3）
  2. 展开所有节点为平铺列表
  3. 应用跳过规则（减少 LLM 读取）
  4. 剩余节点分批（每批 15 个）
  5. 每批：
     a. 准备子代理提示词（替换 {{nodes}}、{{project_dir}}、{{output_path}}）
     b. 为每个节点附加 parentDescription
     c. 派发子代理
     d. 将描述结果合并到树中
  6. 写入 phase5/{entryId}-semantics.json
```

## 跳过规则

以下节点类型不需要读源码，直接从上下文推断：

1. **标准 Mapper 方法**：方法名匹配 `select*`/`insert*`/`update*`/`delete*`/`query*`/`find*`/`get*`/`save*`
   → 从方法名推断描述

2. **带 domainInteraction 的终端节点**：
   → 从 domainInteraction 推断（如 `{type: DATABASE, operation: SELECT, table: account}` → "查询 account 表"）

3. **getter/setter**：
   → 跳过，描述为空

4. **DISPATCH 分发节点**（endpointType == "DISPATCH"）：
   → 从 patternRef 读取 dispatch-summary 文件生成描述
   → 编排者负责：读取 dispatch-summary-{patternRef}.json，生成 "多态分发：根据 {conditions} 路由到 N 个实现类"
   → 子节点（DISPATCH_IMPL 类型）的描述从 summary 的 endpoints 字段直接推断

5. **DISPATCH 子节点**（callType == "DISPATCH_IMPL"）：
   → 从 dispatchImpl 和 domainInteraction 推断
   → 不需要读源码

## 子代理输出格式

```json
{
  "descriptions": [
    {
      "nodeId": "模块名:包名.类名:方法名",
      "description": "中文业务描述",
      "source": "source-code | inferred-method-name | inferred-domain",
      "businessContext": "流程定位（可选）"
    }
  ]
}
```

## 错误处理

- 子代理异常：跳过该批，使用方法名作为降级描述
- 源文件不存在：使用 inferred-method-name 作为描述来源
