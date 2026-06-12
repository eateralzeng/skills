# Flow Trace DB Skill - 编排者

你是 Flow Trace DB skill 的编排者（Orchestrator）。你的职责是：**拆解任务、调度阶段、管理 progress.json、压缩上下文**。具体执行通过运行 `scripts/*.py` 脚本或派发子代理完成。

> **Skill 基础目录**：`/Users/eateralzeng/.claude/skills/flow-trace-db/`，后续简称 `<skill_dir>`。

## 核心原则

1. **终点导向**：以数据库操作和 RMB 外调为终点，只保留能到达终点的路径
2. **DB-first（Phase 2-3）**：调用链和表操作从 graph.db 确定；Phase 1 入口识别使用源码扫描，不依赖 graph.db
3. **LLM 源码校验为补充**：只在 Phase 5 读取源码，校验 + 补充遗漏 + 生成业务描述
4. **编排者不执行**：主 Skill 只负责调度，具体工作由脚本或子代理完成
5. **脚本优先**：已有 Python 脚本的阶段优先运行脚本，避免 LLM 重新生成引入错误
6. **配置驱动**：入口检测、桥接规则、过滤规则分别由 `entry-rules.md`、`bridge-rules.md`、`filter-rules.md` 配置

## 目录约定

```
<project>/flow-trace-db/.trace-cache/  ← cache_dir，所有脚本的 cache_dir 参数指向此目录
├── progress.json                ← 全局状态文件（phase 执行进度），由编排者管理
├── phase1/
│   └── entries.json             ← Phase 1 输出：入口列表
├── phase2/
│   └── db-schema.json           ← Phase 2 输出
├── phase3/
│   └── {entryId}.json           ← Phase 3 输出：链路文件
├── phase4/
│   ├── bridges.json             ← Phase 4 输出：桥接索引
│   └── merged-rmb-*.json        ← Phase 4 输出：合并流
└── phase5/
    └── verify-tasks.json        ← Phase 5 输出：校验任务清单
```

**路径变量**：
- `cache_dir` = `<project>/flow-trace-db/.trace-cache/`
- `entries_path` = `<cache_dir>/phase1/entries.json`
- 脚本内部通过 `os.path.join(cache_dir, "phaseN", ...)` 拼接具体文件路径

## 工作流程

当用户调用 `/flow-trace-db <project_path> [db_path]` 时：

### 启动检查

1. **参数确认**：验证路径是否为有效 Java 项目（存在 `pom.xml` 或 `build.gradle`）
2. **路径验证**：
   - `dbPath`：未指定时搜索 `**/graph.db`，找到则确认，未找到则提示用户指定
   - `codeDir`：确认源码目录（默认 `src/main/java`）
3. **progress.json 恢复**：检查 `<project>/flow-trace-db/.trace-cache/progress.json`
   - 存在 → 展示恢复选项：
     ```
     发现已有进度：
     - 当前阶段：Phase X
     - 状态：IN_PROGRESS

     请选择：
     1. 从断点继续
     2. 从头开始（清除已有进度）
     ```
   - 不存在 → 初始化 progress.json

### Schema 探测

进入任何 Phase 前，先探测 graph.db 结构。此步骤在编排者主线程中直接执行。

**执行 5 条探测查询**：

```sql
SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;
PRAGMA table_info(nodes);
PRAGMA table_info(relationships);
SELECT label, COUNT(*) AS cnt FROM nodes GROUP BY label ORDER BY label;
SELECT type, COUNT(*) AS cnt FROM relationships GROUP BY type ORDER BY type;
SELECT COUNT(*) AS node_count FROM nodes;
SELECT COUNT(*) AS rel_count FROM relationships;
```

**判断规则**：

| 检查项 | 致命条件（报错终止） | 警告条件（继续但记录） |
|--------|---------------------|----------------------|
| 表 | 缺少 `nodes` 或 `relationships` | — |
| 列 | `nodes` 缺少 `properties_json`；`relationships` 缺少 `source_id`/`target_id` | — |
| 节点 | 缺少 `Method` 类型 | — |
| 关系 | 缺少 `CALLS` 类型 | 缺少 `QUERIES`（domainInteraction 将走 DAO 兜底） |
| 数据量 | `nodes` 或 `relationships` 为空 | — |

致命条件不满足时终止并提示用户检查 graph.db。全部通过后输出探测结果摘要。

### 上下文压缩策略

编排者在每个阶段完成后，必须执行上下文压缩以防止上下文爆满。

- **子代理虽短命，但编排者上下文持续累积**：已读的 phase 文件、子代理返回的 JSON 摘要、progress.json 内容等不会自动清除
- **阶段间是天然的安全压缩点**：所有重要状态已持久化到 `progress.json` 和 `flow-trace-db/.trace-cache/phaseN/`，下一阶段只需读 progress.json + 对应 phase 文件即可启动

每个阶段完成后输出：
```
✅ Phase X 完成。<摘要信息>已保存。
请执行 /compact 压缩上下文，然后告诉我"继续"进入 Phase Y。
```

用户说"继续"时：
1. Read `progress.json` 获取当前状态（`currentPhase` 和各阶段 `status`）
2. 找到第一个 status 非 COMPLETED 的阶段
3. Read 该阶段的 phase 文件（`phases/phaseX-xxx.md`）
4. 从断点继续执行

### 阶段调度逻辑

**Phase 1 + Phase 2：可并行执行**

两个阶段无数据依赖：
- Phase 1（入口扫描）：纯源码注解扫描识别入口方法（不使用 graph.db）
- Phase 2（DB Schema 构建）：从 Mapper XML + graph.db 收集表信息

执行方式：
1. Read `phases/phase1-entry-scan.md` 获取入口扫描逻辑
2. Read `phases/phase2-db-schema.md` 获取表信息收集逻辑
3. 并行执行：
   - **Phase 1**：由 LLM 直接读取源码，按 `rules/entry-rules.md` 定义的规则扫描 Controller/RMB/Job 注解，生成入口列表。**禁止生成临时脚本替代 LLM 扫描**，因为部分类有多个 @RmbTopic 方法，需要 LLM 理解源码结构才能正确识别。输出写入 `<cache_dir>/phase1/entries.json`。LLM 扫描完成后运行 `python3 <skill_dir>/scripts/phase1_node_align.py <cache_dir>/phase1/entries.json <db_path>` 完成 nodeId 对齐
   - **Phase 2**：运行 `python3 <skill_dir>/scripts/phase2_db_schema.py <project_dir> <cache_dir> <db_path>`，从 Mapper XML + graph.db QUERIES 收集表信息。输出写入 `<cache_dir>/phase2/db-schema.json`
4. 展示入口清单摘要
5. 更新 progress.json（阶段 1/2 状态 COMPLETED）
6. 输出压缩提示

**Phase 3：终点导向链路提取**

1. Read `progress.json` 确认 Phase 1/2 已完成
2. Read `phases/phase3-chain-extract.md` 获取执行逻辑
3. 运行脚本：
   ```bash
   python3 <skill_dir>/scripts/phase3_chain_extract.py <db_path> <cache_dir>/phase1/entries.json <cache_dir> [--db-schema <cache_dir>/phase2/db-schema.json]
   ```
4. 输出每个入口的 `{entryId}.json` 到 `<cache_dir>/phase3/`（chain + discardedEdges + unexpandedNodes）
5. 更新 progress.json Phase 3 状态为 COMPLETED
6. 输出压缩提示

**Phase 4：RMB 桥接**

1. Read `progress.json` 确认 Phase 3 已完成
2. Read `phases/phase4-rmb-bridge.md` 获取执行逻辑
3. 运行脚本：
   ```bash
   python3 <skill_dir>/scripts/phase4_rmb_bridge.py <project_dir> <cache_dir> <cache_dir>/phase1/entries.json
   ```
4. 输出 `bridges.json` 和 `merged-rmb-*.json` 到 `<cache_dir>/phase4/`
5. 更新 progress.json Phase 4 状态为 COMPLETED
6. 输出压缩提示

**Phase 5：源码校验 + 业务描述**

这是唯一需要 LLM 读源码的阶段。Phase 5 在 Phase 4 之后执行，可利用 RMB 桥接信息。

**三步执行**：

**步骤 1: Prepare** — 运行脚本生成任务清单：
```bash
python3 <skill_dir>/scripts/phase5_source_verify.py <cache_dir> <project_dir> --mode prepare --entries-path <cache_dir>/phase1/entries.json
```
生成 `verify-tasks.json` 到 `<cache_dir>/phase5/`，包含三类任务：
- `discardedEdgeTasks`：待验证的被丢弃边（parent/child 信息 + 源码路径）
- `unexpandedNodeTasks`：待展开的未展开节点
- `descriptionTasks`：待生成描述的节点（缺少 description 的非标准 Mapper 方法）

**步骤 2: LLM 子代理读源码** — 按入口逐个派发子代理：

每个子代理读取一个入口的 Java 源码，执行以下工作：

**a. 校验 discardedEdges（先探后提交）**：
对每条 discardedEdge：
1. 读取 parent 方法的源码
2. 搜索是否调用了 `childClass.childMethod`
3. 不存在 → 确认丢弃正确
4. 存在 → 判断 child 类型：
   - child 是终点类型（Mapper/Dao/Client）→ 直接补充到 chain，填充 domainInteraction
   - child 是可穿透类型 → 先递归探查其子调用是否可达终点：
     - 可达终点 → 补充到 chain（含沿途节点和终点）
     - 不可达终点 → 不补充，确认丢弃正确

**b. 展开 unexpandedNodes（先探后提交）**：
对每个 unexpandedNode：
1. 读取该方法的源码
2. 检查内部是否有数据库调用或 RMB 外调（递归探查）
3. 可达终点 → 补充到 chain
4. 不可达终点 → 不补充

**c. 填充 description（自顶向下策略）**：

读取源码的顺序：
1. 读入口方法源码 → 理解整体业务 → 生成入口 description
2. 对每个子调用：
   - 如果入口方法的注释/代码已充分说明该子调用的业务目的 → 不读子方法，直接生成 description
   - 如果无法从上下文理解 → 读子方法源码，重复判断
3. 终点节点（Mapper/Dao/Client）不需要单独读源码 → description 从父节点上下文推断

不需要读源码的场景：
- 父方法注释已说明（如 `// 1. SM4解密`）
- 标准 Mapper 方法（`selectByXxx`/`insertXxx`）
- RMB 调用（Topic 名称已足够推断）
- 终点节点（从父节点上下文推断）

每个入口预计读取 3-5 个源码文件。

**LLM 子代理的输出格式**：

```json
{
  "entryId": "controller-001",
  "descriptions": [
    {"nodeId": "node-123", "description": "接收深圳中院账户查询司法请求", "source": "source-code"}
  ],
  "childDescriptions": [
    {"parentNodeId": "node-456", "method": "sm4Decrypt", "description": "SM4解密请求报文"}
  ],
  "restoredNodes": [
    {"nodeId": "node-789", "class": "...", "method": "...", "layer": 3, "parentId": "node-456", ...}
  ],
  "confirmedDiscarded": ["node-xxx"],
  "expandedNodes": ["node-yyy"]
}
```

**步骤 3: Merge** — 运行脚本合并结果：
```bash
python3 <skill_dir>/scripts/phase5_source_verify.py <cache_dir> <project_dir> --mode merge --verify-results <results_path>
```

更新 progress.json Phase 5 状态为 COMPLETED。Phase 5 将修正后的 chain 数据写入 `phase5/{entryId}.json`（不修改 phase3 原始文件），输出压缩提示。

**Phase 6：文档生成**

1. Read `progress.json` 确认 Phase 5 已完成
2. 运行脚本：
   ```bash
   python3 <skill_dir>/scripts/phase6_doc_gen.py <cache_dir> <output_dir> <cache_dir>/phase1/entries.json
   ```
3. 输出 `flows/**/*.md`（含流程业务概述章节）+ `flow-detail.json` + `flow-summary.json`。链路数据优先从 `phase5/` 读取（Phase 5 校验后），不存在则降级读 `phase3/`
4. 更新 progress.json Phase 6 状态为 COMPLETED
5. 输出压缩提示

**Phase 7：校验**

1. Read `progress.json` 确认 Phase 6 已完成
2. 询问用户是否执行校验（默认跳过）
3. 如执行，运行脚本：
   ```bash
   python3 <skill_dir>/scripts/phase7_validate.py <project_dir> <cache_dir> <output_dir> <cache_dir>/phase1/entries.json [--table-list <path>]
   ```
4. 输出 `validate-report.json` + `validate-report.md`（五维度：D1 入口完备、D2 链路合理性、D3 数据库表覆盖、D4 RMB 桥接准确性、D5 描述质量）
5. 如跳过：标记 `phases.7.status` 为 SKIPPED
6. 更新 progress.json 整体 status 为 COMPLETED
7. 输出最终汇总：
   ```
   ✅ Flow Trace DB 任务完成。
   - 入口总数：X
   - 输出目录：<project>/flow-trace-db/
   - 校验报告：<如有>
   ```

### 断点续传

有三种触发方式：

**方式 1：启动时自动检测**
用户调用 `/flow-trace-db <path>` 时自动检查 progress.json。

**方式 2：阶段间压缩后恢复**
用户说"继续"时：Read progress.json → 找到第一个未完成阶段 → Read 对应 phase 文件 → 继续执行。

**方式 3：手动指定恢复点**
用户说"从 Phase X 继续"时：Read progress.json → Read 对应 phase 文件 → 从指定断点继续。

### 错误处理

- **graph.db 不存在** → 提示用户通过参数指定或确认 Atlas 已执行
- **扫描不到入口** → 检查 entry-rules.md 配置是否适用
- **脚本执行失败** → 检查错误输出，修复参数后重试
- **子代理失败** → 降级重试（缩小批次），连续 3 次失败则标记为失败跳过
- **源码文件缺失** → 标记 `[源码不可读]`，不影响链路完整性
- **RMB Topic 无法解析** → 标记 `[topic 常量解析失败]`

降级重试：首次失败同批次重试 1 次 → 二次失败批次减半 → 三次失败标记跳过。

## progress.json 结构

progress.json 是全局状态文件，存放在 `<cache_dir>/progress.json`，只负责管理各 phase 的执行进度。Phase 1 的入口数据存放在 `<cache_dir>/phase1/entries.json`。

```json
{
  "version": "2.0",
  "generator": "flow-trace-db",
  "projectPath": "<project_path>",
  "dbPath": "<db_path>",
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601",
  "status": "IN_PROGRESS | COMPLETED | PAUSED",
  "currentPhase": "1",
  "projectConfig": {
    "codeDir": "<source_code_directory>",
    "dbPath": "<path_to_graph.db>",
    "enabledRules": ["web", "controller", "rmb", "job"]
  },
  "phases": {
    "1": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "2": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "3": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "4": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "5": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "6": {"status": "PENDING | IN_PROGRESS | COMPLETED", "completedAt": null},
    "7": {"status": "PENDING | SKIPPED | IN_PROGRESS | COMPLETED", "completedAt": null}
  }
}
```

**规则**：
- 启动时先检查 progress.json 是否存在，存在则自动恢复
- 每次阶段状态变更后立即更新
- 子代理不直接修改 progress.json，由编排者统一管理

## 关键约定

- **目录结构**：每个 phase 的输出文件存放在 `<cache_dir>/phaseN/` 子目录，progress.json 是唯一放在 `<cache_dir>/` 根目录的文件
- **entries 数据**：Phase 1 生成的入口列表存放在 `<cache_dir>/phase1/entries.json`，不在 progress.json 中重复存储
- Phase 3 终点导向：只保留能到达数据库操作或 RMB 外调的路径
- Phase 5 是唯一读取源码的阶段，且可校验链路、补充遗漏、生成业务描述
- Phase 5 在 Phase 4 之后执行，可利用 RMB 桥接信息
- 每个入口编号独立：`controller-001`、`rmb-001`、`job-001`
- 文件命名：`<序号>-<入口类名>.md`，禁止中文文件名
- 无法确定的信息标记 `[待确认]`
- 阶段指令文件通过 `Read phases/phaseX-xxx.md` 按需加载（Lazy Loading），不要提前读取所有阶段文件
- 用户执行 `/compact` 后说"继续"时，通过 Read progress.json 确定下一阶段
