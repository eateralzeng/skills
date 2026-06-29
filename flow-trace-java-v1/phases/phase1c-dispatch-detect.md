# Phase 1c: 分发点识别

## 概述

纯脚本阶段。扫描项目源码，识别多态分发点（接口/抽象类有多个实现类，且在代码中被调用）。
可选的 LLM 精筛步骤用于移除粗筛误报。

## 输入

- 项目源码目录
- `rules/dispatch-rules.md`（噪声接口黑名单、排除规则）

## 输出

- `phase1c/pattern-index.json` — 分发点索引

## 前置条件

- 无（可与 Phase 1a、Phase 1b 并行）

## 执行步骤

### 粗筛

运行脚本：

```bash
python3 <skill_dir>/scripts/phase1c_dispatch_detect.py --project-dir <project_dir> --cache-dir <cache_dir>
```

脚本内部流程：implements 扫描 → extends 补扫 → 去重合并 → pattern-index.json

### 精筛（LLM 验证）

粗筛完成后，可选执行 LLM 精筛移除误报。

1. 准备验证上下文：
   ```bash
   python3 <skill_dir>/scripts/phase1c_dispatch_detect.py --mode verify-prepare --project-dir <project_dir> --cache-dir <cache_dir>
   ```
   输出：`phase1c/tmp/_verify-context.json`

2. 分批派发 LLM 子代理：
   - 读取 `prompts/phase1c-verify.md`
   - 对每个 batch，替换 `{{patterns}}` 和 `{{project_dir}}`
   - 派发子代理（多个 batch 可并行）
   - 保存子代理输出到 `phase1c/tmp/_verify-result-{batchIndex}.json`

3. 合并验证结果：
   - 合并所有 batch 的 results 数组为单一 JSON
   - 保存到 `phase1c/tmp/_verify-results.json`

4. 应用验证结果：
   ```bash
   python3 <skill_dir>/scripts/phase1c_dispatch_detect.py --mode verify-apply --cache-dir <cache_dir> --results <results_path>
   ```
   - 从 pattern-index.json 中移除 verified=false 的 pattern
   - 输出 `phase1c/tmp/_verify-report.json`（记录移除的 pattern）

## 算法

1. grep 扫描 `implements` 子句，解析完整接口列表（多接口），构建接口→实现类映射
2. 对每个实现类判断是否为抽象类（grep 源文件 `abstract class`）
3. 如果是抽象类，递归 grep `extends` 找到子类，直到所有叶子节点都是具体类
4. 过滤噪声接口（dispatch-rules.md 中的黑名单）和具体实现类少于 2 个的
5. grep 扫描接口类型的字段声明（DI 注入），定位 Context 类
6. 判断分发方式（stream/map/switch/unknown）
7. 输出 implements 分发点到临时列表
8. grep 扫描 `extends` 关系，构建 parent → children 映射
9. 对每个有 2+ 直接子类的 parent：
   a. 如果 parent 已在 implements 结果中 → 跳过（已被覆盖）
   b. 确认 parent 是 abstract class（非 interface、非噪声类）
   c. 递归解析具体后代，数量 >= 阈值 → 纳入候选
10. extends 补扫结果与 implements 结果合并，统一输出到 pattern-index.json

## 错误处理

- grep 超时：跳过该目录，输出警告
- 找不到 Context 类：标记 type=UNKNOWN，仍然加入 pattern-index（Phase 2a 仍会跳过展开）
- 找不到接口源文件：跳过该接口（无法确认是 interface 还是 class）
- LLM 精筛失败：保留粗筛结果，不影响后续阶段
