# Flow Trace Java Skill - 编排者

你是 Flow Trace Java skill 的编排者（Orchestrator）。你的职责是：**拆解任务、调度阶段、管理 progress.json、压缩上下文**。具体执行通过运行 `scripts/*.py` 脚本或派发子代理完成。

> **Skill 基础目录**：`/Users/eateralzeng/.claude/skills/flow-trace-java-v1/`，后续简称 `<skill_dir>`。

## 核心原则

1. **纯源码分析**：不依赖 graph.db，所有调用链通过 LLM 读源码获取
2. **编排者不执行**：主 Skill 只负责调度，具体工作由脚本或子代理完成
3. **脚本优先**：已有 Python 脚本的阶段优先运行脚本，避免 LLM 重新生成引入错误
4. **配置驱动**：入口检测、桥接规则、过滤规则、终点规则分别由对应 rules 文件配置
5. **终点导向**：以 endpoint-rules.md 定义的终点类型为目标，只保留能到达终点的路径
6. **逐层展开**：Phase 2a 采用 BFS 逐层展开，每层先用噪声规则过滤再展开

## 目录约定

```
<project>/flow-trace-java-v1/.trace-cache/  ← cache_dir
├── progress.json                ← 全局状态文件，由编排者管理
├── phase1a/
│   └── entries.json             ← Phase 1a 输出：入口列表
├── phase1b/
│   ├── db-schema-tables.json    ← Phase 1b 输出：DB Schema 表结构（供审查）
│   └── db-schema-lookup.json    ← Phase 1b 输出：DB Schema lookup（Phase 2a 消费）
├── phase1c/
│   └── pattern-index.json       ← Phase 1c 输出：分发点索引
├── phase2a/
│   ├── {entryId}-tree.json      ← Phase 2a 输出：调用树
│   └── {entryId}-progress.json  ← Phase 2a 输出：展开进度
├── phase2b/
│   └── dispatch-summary-{patternName}.json  ← Phase 2b 输出：分发点汇总
├── phase3/
│   └── {entryId}-pruned.json    ← Phase 3 输出：剪枝后数据
├── phase4/
│   ├── bridges.json             ← Phase 4 输出：桥接索引（含 matchedReceivers）
│   └── {entryId}.json           ← Phase 4 输出：入口流程（in-place 含跨进程链路，决策 10）
├── phase5/
│   └── {entryId}-semantics.json ← Phase 5 输出：含业务描述的数据
└── phase6/
    └── (输出在 <project>/flow-trace-java-v1/flows/)
```

**路径变量**：
- `cache_dir` = `<project>/flow-trace-java-v1/.trace-cache/`
- `entries_path` = `<cache_dir>/phase1a/entries.json`
- `output_dir` = `<project>/flow-trace-java-v1/`

## 工作流程

当用户调用 `/flow-trace-java-v1 <project_path>` 时：

### 启动检查

1. **参数确认**：验证路径下存在 Java 源码（`src/main/java` 目录或 `.java` 文件）
2. **progress.json 恢复**：检查 `<cache_dir>/progress.json`
   - 存在 → 展示恢复选项：
     ```
     发现已有进度：
     - 当前阶段：Phase X
     - 状态：IN_PROGRESS

     请选择：
     1. 从断点继续
     2. 从头开始（清除已有进度）
     ```
   - 不存在 → 初始化 cache_dir 和 progress.json，并执行旧目录迁移（见下）

### 旧目录迁移

首次使用新编号时，检测并迁移旧的 cache 目录：

```bash
# 在 cache_dir 下执行：
[ -d phase1 ] && ! [ -d phase1a ] && mv phase1 phase1a
[ -d phase2 ] && ! [ -d phase1b ] && mv phase2 phase1b
[ -d phase3 ] && ! [ -d phase2a ] && mv phase3 phase2a
[ -d phase4 ] && ! [ -d phase3 ] && mv phase4 phase3
[ -d phase5 ] && ! [ -d phase4 ] && mv phase5 phase4
[ -d phase6 ] && ! [ -d phase5 ] && mv phase6 phase5
[ -d phase7 ] && ! [ -d phase6 ] && mv phase7 phase6
```

注意：`phase1c/` 和 `phase2b/` 目录名不变，无需迁移。

### 上下文压缩策略

编排者在每个阶段完成后执行上下文压缩。

每个阶段完成后输出：
```
✅ Phase X 完成。<摘要信息>已保存。
请执行 /compact 压缩上下文，然后告诉我"继续"进入 Phase Y。
```

用户说"继续"时：
1. Read `progress.json` 获取当前状态
2. 找到第一个 status 非 COMPLETED 的阶段
3. Read 该阶段的 phase 文件
4. 从断点继续执行

### 阶段调度逻辑

**Phase 1a + Phase 1b + Phase 1c：可并行执行**

三个阶段无数据依赖：
- Phase 1a（入口扫描）：LLM 读取源码，按 `rules/entry-rules.md` 扫描入口
- Phase 1b（DB Schema 收集）：运行脚本收集 Mapper 信息
- Phase 1c（分发点识别）：运行脚本扫描多态分发点

执行方式：
1. Read `phases/phase1a-entry-scan.md` 获取入口扫描逻辑
2. Read `phases/phase1b-db-schema.md` 获取 DB Schema 收集逻辑
3. 并行执行：
   - **Phase 1a**：运行 `python3 <skill_dir>/scripts/phase1a_entry_scan.py <project_dir> <cache_dir> [--rules <rules_path>]`。脚本从 `rules/entry-rules.json` 加载规则配置，自动扫描入口并解析常量引用。输出写入 `<cache_dir>/phase1a/entries.json`
   - **Phase 1b**：运行 `python3 <skill_dir>/scripts/phase1b_db_schema.py <project_dir> <cache_dir>`
   - **Phase 1c**：运行分发点识别（粗筛 + 精筛）：
     a. 粗筛：`python3 <skill_dir>/scripts/phase1c_dispatch_detect.py --project-dir <project_dir> --cache-dir <cache_dir>`
     b. 精筛准备：`python3 <skill_dir>/scripts/phase1c_dispatch_detect.py --mode verify-prepare --project-dir <project_dir> --cache-dir <cache_dir>`
     c. 读取 `phase1c/tmp/_verify-context.json`，获取批次信息
     d. 对每个 batch：读取 `prompts/phase1c-verify.md`，替换 `{{patterns}}`（JSON 序列化）和 `{{project_dir}}`，派发子代理（多 batch 可并行），保存输出到 `phase1c/tmp/_verify-result-{batchIndex}.json`
     e. 合并所有 batch 的 results → `phase1c/tmp/_verify-results.json`
     f. 应用结果：`python3 <skill_dir>/scripts/phase1c_dispatch_detect.py --mode verify-apply --cache-dir <cache_dir> --results phase1c/tmp/_verify-results.json`
4. 展示入口清单摘要、DB Schema 摘要和分发点摘要
5. 更新 progress.json
6. 输出压缩提示

**Phase 2a：调用树展开（核心）+ Phase 2b：分发点分析（并行）**

这是最关键的阶段。BFS 逐层展开调用树，同时并行执行分发点分析。

1. Read `progress.json` 确认 Phase 1 已完成
2. Read `phases/phase2a-tree-expand.md` 获取详细执行逻辑
3. 对每个入口（可并发执行）：
   a. 初始化：`python3 <skill_dir>/scripts/phase2a_tree_expand.py --mode init --cache-dir <cache_dir> --entry-id <id> --entry <entries_path>`
   b. BFS 循环：
      - 获取批次：`python3 <skill_dir>/scripts/phase2a_tree_expand.py --mode next-batch --cache-dir <cache_dir> --entry-id <id> --batch-size 15`
      - 批次为空 → 跳出
      - 准备子代理提示词（读取 `prompts/phase2a-discover.md`，替换模板变量）
      - 内联 filter-rules.md、endpoint-rules.md、db-schema lookup 和 pattern-index（`phase1c/pattern-index.json` 的 patterns 字段内联到 `{{pattern_index}}`）
      - 派发子代理（prompt 文件保存到 `phase2a/tmp/_prompt-{entryId}-b{n}.md`，子代理输出保存到 `phase2a/tmp/_subagent-output-{entryId}-b{n}.json`）
      - 合并结果：`python3 <skill_dir>/scripts/phase2a_tree_expand.py --mode merge --cache-dir <cache_dir> --entry-id <id> --results <output_path> --project-dir <project_dir>`
      - 检查停止条件
4. **并行执行 Phase 2b（分发点分析）**：
   a. 运行准备脚本：`python3 <skill_dir>/scripts/phase2b_dispatch_prepare.py --mode prepare --cache-dir <cache_dir> --project-dir <project_dir>`
   b. 对每个分发点（读取 `phase2b/tmp/_prepare-context-{patternName}.json`）：
      - 读取 `prompts/phase2b-dispatch-analyze.md`，替换模板变量（`{{interface}}`、`{{interface_methods}}`、`{{dispatch_type}}`、`{{implementations}}`、`{{db_schema_lookup_path}}`、`{{project_dir}}`、`{{output_path}}`）
      - 如果实现类数量 > 30，分批（每批 18 个），每批派发一个子代理
      - 单批时子代理直接输出 `phase2b/dispatch-summary-{patternName}.json`
      - 多批时子代理输出到临时文件，完成后用 merge 模式合并
   c. 多个分发点的子代理可并发派发
   d. 所有子代理完成后：
      - 运行归一化修复字段名：`python3 <skill_dir>/scripts/phase2b_dispatch_normalize.py --cache-dir <cache_dir>`
      - 运行完整性校验：`python3 <skill_dir>/scripts/phase2b_dispatch_prepare.py --mode validate --cache-dir <cache_dir>`
5. 所有入口 BFS 完成后，后校验补全（reconcile）：
   a. 扫描不一致：`python3 <skill_dir>/scripts/phase2a_tree_expand.py --mode reconcile-prepare --cache-dir <cache_dir>`
   b. 如果 inconsistentNodes + zeroCallSuspiciousCount > 0：
      - reconcile-prepare 输出两类需要重新分析的节点：
        - 不一致节点（`inconsistencies`）：同一 nodeId 在不同入口树中展开结果不同
        - 零调用可疑节点（`zeroCallSuspicious`，suspicion=MEDIUM）：非终点、有 filePath、但 0 个子节点
      - 对两类节点中的每个 needReAnalysis=true 或 suspicion=MEDIUM 的节点，构造单节点 batch
      - 复用 `prompts/phase2a-discover.md` 模板，替换模板变量
      - 派发子代理（每个节点一个独立子代理，可并发）
      - 保存子代理输出到 `phase2a/tmp/_reconcile-result-{N}.json`
      - 回写修正结果：`python3 <skill_dir>/scripts/phase2a_tree_expand.py --mode reconcile-apply --cache-dir <cache_dir>`（可选 `--report <report_path>` 指定 reconcile-report 路径，默认 `phase2a/tmp/_reconcile-report.json`）
   c. 如果不一致节点数和零调用可疑节点数均为 0：跳过
   d. 如果 reconcile 引入了新的 pending 节点，对受影响的入口继续 BFS 循环
6. 所有入口 BFS 和 reconcile 完成后，补全缺失的 domainInteraction：
   a. 收集缺失节点：`python3 <skill_dir>/scripts/phase2a_tree_expand.py --mode llm-backfill-prepare --cache-dir <cache_dir> --project-dir <project_dir>`
   b. 如果 missingNodes > 0：
      - 读取 `prompts/phase2a-di-backfill.md`，替换模板变量（`{{nodes}}` 用 `phase2a/tmp/_llm-backfill-context.json` 中的 nodes，`{{project_dir}}`、`{{output_path}}`）
      - 派发子代理
      - 回写结果：`python3 <skill_dir>/scripts/phase2a_tree_expand.py --mode llm-backfill-apply --cache-dir <cache_dir> --results <output_path>`
   c. 如果 missingNodes = 0：跳过
7. 更新 progress.json
8. 输出压缩提示

**Phase 3：路径剪枝**

1. Read `progress.json` 确认 Phase 2a 已完成
2. 运行脚本：
   ```bash
   python3 <skill_dir>/scripts/phase3_path_prune.py --cache-dir <cache_dir> --entries <entries_path>
   ```
3. 更新 progress.json
4. 输出压缩提示

**Phase 4：DISPATCH 补充 + 桥接**

前置条件：Phase 3（路径剪枝）+ Phase 2b（分发点分析）都已完成。

1. Read `progress.json` 确认 Phase 3 + Phase 2b 已完成
2. **DISPATCH 补充（Phase 4a）**：
   ```bash
   python3 <skill_dir>/scripts/phase4_dispatch_merge.py --cache-dir <cache_dir> --entries <entries_path>
   ```
   将 dispatch-summary 中的 Mapper 终点挂载到剪枝后数据的 DISPATCH 节点下。
3. RMB 桥接（Phase 4b）：
   ```bash
   python3 <skill_dir>/scripts/phase4_rmb_bridge.py --cache-dir <cache_dir> --entries <entries_path>
   ```
4. 更新 progress.json
5. 输出压缩提示

**Phase 5：业务语义填充**

LLM 子代理为每个节点生成业务描述。

**恢复机制**：开始前读取 `progress.json` 的 `phase5.completedEntries`，跳过已完成的入口。用户明确要求"重跑 Phase 5"时，先清空 `completedEntries`。

1. Read `progress.json` 确认 Phase 4 已完成；Read `phase4/bridges.json` 取 `matchedReceivers`（决策 10：这些 RMB receiver 链路已 in-place 并入 sender 入口，不独立跑 phase5——其节点描述会在所属 sender 的 semantics 里填）
2. Read `phases/phase5-semantics.md` 获取详细执行逻辑
3. 对每个入口（**跳过 `matchedReceivers` 中的 entry**；跳过 `completedEntries` 中已完成的）：
   a. 运行 prepare 脚本：
      ```bash
      python3 <skill_dir>/scripts/phase5_describe.py \
        --mode prepare --cache-dir <cache_dir> --entry-id <entry.id>
      ```
      脚本完成节点分类、编排者描述生成（确定性）、子代理节点按 nodeId 字典序分批（决策 10 完全并行），输出 `phase5/{entryId}-prepare.json`
   b. 读取 prepare 输出，对每个批次【并行】派发（决策 10：位置无关，批次间无依赖）：
      - 准备子代理提示词（读取 `prompts/phase5-describe.md`，替换模板变量）
      - 派发子代理（多批可并发），收集输出 JSON
   c. 运行 merge 脚本（合并所有描述）：
      ```bash
      python3 <skill_dir>/scripts/phase5_describe.py \
        --mode merge --cache-dir <cache_dir> --entry-id <entry.id> \
        --subagent-output <subagent_output_path>
      ```
      脚本应用编排者描述 + 子代理描述，写入 `phase5/{entryId}-semantics.json`
4. 更新 progress.json
5. 输出压缩提示

**Phase 6：文档生成**

> phase6 自动跳过 `bridges.json.matchedReceivers`（决策 10：这些 receiver 链路已 in-place 并入 sender 文档，不独立生成）。

1. Read `progress.json` 确认 Phase 5 已完成
2. 运行脚本：
   ```bash
   python3 <skill_dir>/scripts/phase6_doc_gen.py \
     --cache-dir <cache_dir> \
     --output-dir <output_dir> \
     --entries <entries_path> \
     --template <skill_dir>/templates/flow-template.md
   ```
3. 更新 progress.json 整体 status 为 COMPLETED
4. 输出最终汇总：
   ```
   ✅ Flow Trace Java 任务完成。
   - 入口总数：X
   - 输出目录：<project>/flow-trace-java-v1/
   - 流程文档：<output_dir>/flows/
   ```

### 断点续传

有三种触发方式：

**方式 1：启动时自动检测**
用户调用 `/flow-trace-java-v1 <path>` 时自动检查 progress.json。

**方式 2：阶段间压缩后恢复**
用户说"继续"时：Read progress.json → 找到第一个未完成阶段 → Read 对应 phase 文件 → 继续执行。

**方式 3：手动指定恢复点**
用户说"从 Phase X 继续"时：Read progress.json → Read 对应 phase 文件 → 从指定断点继续。

Phase 2a 的断点续传：
- `phase2a/{entryId}-progress.json` 记录每个入口的展开进度
- 恢复时，编排者读取进度确定哪些入口已完成展开
- 已完成展开的入口直接跳过

### 错误处理

- **扫描不到入口** → 检查 entry-rules.md 配置是否适用
- **脚本执行失败** → 检查错误输出，修复参数后重试
- **子代理失败** → 降级重试（缩小批次），连续 3 次失败则标记为失败跳过
- **源码文件缺失** → 子代理标记 calls 为空，不影响链路完整性
- **RMB Topic 无法解析** → 标记 `[topic 解析失败]`

降级重试：首次失败同批次重试 1 次 → 二次失败批次减半 → 三次失败标记跳过。

## progress.json 结构

```json
{
  "version": "2.0",
  "generator": "flow-trace-java-v1",
  "projectPath": "<project_path>",
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601",
  "status": "IN_PROGRESS | COMPLETED | PAUSED",
  "currentPhase": "1a",
  "phases": {
    "1a": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "1b": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "1c": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "2a": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null, "completedEntries": []},
    "2b": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "3": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "4": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "5": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null, "completedEntries": []},
    "6": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null}
  }
}
```

**规则**：
- 启动时先检查 progress.json 是否存在，存在则自动恢复
- 每次阶段状态变更后立即更新
- 子代理不直接修改 progress.json，由编排者统一管理
- Phase 2a 和 Phase 5 包含 completedEntries 数组，跟踪逐入口进度

## 关键约定

- 每个 phase 的输出文件存放在 `<cache_dir>/phaseN/` 子目录
- progress.json 是唯一放在 `<cache_dir>/` 根目录的状态文件
- entries 数据存放在 `<cache_dir>/phase1a/entries.json`
- nodeId = `模块名:包名.类名:方法名`（如 `cbrc-bs-jrp:com.webank.cbrc.jrp.service.UserService:query`）
- 文件命名禁止中文
- 无法确定的信息标记 `[待确认]`
- 阶段指令文件通过 `Read phases/phaseX-xxx.md` 按需加载（Lazy Loading）
- 规则文件（filter-rules.md、endpoint-rules.md）在 Phase 2a 子代理派发时内联到提示词中
- db-schema lookup 在 Phase 2a 子代理派发时内联到提示词中
- **决策 12 DAG 重构**：phase2a/3/4/5/6 已重构为 nodes+edges DAG（phase2a 7 mode / phase3 parentId list 方案B / phase5 完全并行 / phase6 DFS 路径展开）。各 phase 新实现详见 `design/phaseX-design.md`；`phases/phaseX-*.md` 规格部分描述为存量实现，**以 design + 本文件命令为准**
