# 《AI Skill 设计与开发原则书 (AI Skill Design & Development Principles)》

---

# 第一部分：Skill 运行时设计原则

**本部分规范 Skill 在执行过程中的架构设计和资源管理。**

## 第一章：资源管理原则 (Resource Management)

**核心思想：Token 是稀缺的内存，磁盘是廉价的存储。**

### 非必要不读取 (Lazy Loading)：

- 严禁在未确认逻辑相关性前执行全量文件 read。
- 模式：先用 grep 定位关键行，或用 ls -R 获取目录树。只有确定该文件是核心链路时，才调用 read 工具。

### 阅后即焚 (Context Flushing)：

- 子代理（Subagent）必须是短命的（Short-lived）。
- 任务完成后，子代理应只返回"精简摘要"，由主代理将结果写入磁盘，随后立即执行 context compact 或销毁该子代理内存。

### 结果持久化 (Persistence)：

- 中间结果（如扫描到的 Handler 名单）必须存入 .trace-cache。在skill的实现过程中一定要指定中间结果的存放路径。
- 严禁将大型中间数据仅保存在对话历史中。

### 步骤级产物落盘 (Step-Level Artifact Persistence)：

- 当一个 phase 包含多个步骤，且每个步骤会产生独立的数据集时，每个步骤的产物都必须保存为独立文件，而非仅在对话上下文中传递。
- 步骤之间通过文件路径传递数据，而非将全部中间数据塞入对话历史。
- 每个步骤产物的文件命名应清晰反映其来源和用途（如 `step1-handler-list.json`、`step2-filtered-result.json`）。
- 好处：控制上下文大小、支持断点续传（失败步骤可直接从上一个成功步骤恢复）、便于事后逐层追溯问题。

## 第二章：架构模式原则 (Architectural Patterns)

### 编排者模式 (Orchestrator Pattern)：

- 主 Skill (Master) 只负责：拆解任务、分配子代理、汇总结果、异常处理。
- 子代理 (Workers) 只负责：具体的 Read/Search/Analyze。
- 解耦：子代理之间禁止直接通信，所有信息交换必须通过主代理或磁盘文件。

### 批处理模式 (Batch Processing)：

- 面对大量对象（如 159 个 Handler）时，必须实现 Chunking（分片）。
- 原则：每批次处理数量上限通常设为 10-20 个，完成一批即触发一次状态同步和内存清理。

### 过滤器模式 (Predicate Filter)：

- 在深入解析前，先定义"噪音排除列表"（如：忽略 DTO、忽略测试类、忽略第三方库）。
- 通过预扫描减少无效 IO。

## 第三章：接口与契约原则 (Contracts & Interfaces)

### 结构化输出 (Structured Output)：

- Skill 之间的交付物必须是标准的 Markdown 表格或 JSON。
- 严禁返回描述性的长篇大论，必须确保后续 Agent 或工具可以直接解析输出内容。

### 单点事实 (Single Source of Truth)：

- 项目的配置信息（如数据库连接、包路径）应在 Skill 启动时一次性读取，并存入全局变量或临时文件，后续步骤统一调用，避免重复扫描。

### 配置与逻辑分离 (Configuration-Logic Separation)：

- 规则、过滤器、配置参数等可变内容必须收敛到 rule 文件或配置文件中，作为唯一的事实来源。
- Phase 执行文件（脚本、SKILL.md）中严禁复制或硬编码规则内容，只能通过引入（import / read）rule 文件来获取规则。
- 目的：规则变更时只需修改 rule 文件，无需同步修改执行逻辑，避免规则漂移（两边不一致）和遗漏修改。

## 第四章：鲁棒性原则 (Robustness & Error Handling)

### 状态检查点 (Checkpoints)：

- Skill 必须具备"断点续传"能力。
- 启动前应先检查 .trace-cache/progress.json，如果发现已有进度，应跳过已完成部分。

### 防御性扫描 (Defensive Scanning)：

- 如果子代理报错（如 Context Limit），主代理必须具备自动缩小批次（Back-off）并重试的逻辑。

---

# 第二部分：Skill 开发流程原则

**本部分规范 Skill 从设计到编码的完整开发流程。**

## 第五章：设计阶段 (Design Phase)

### 设计文档持久化 (Design Document Persistence)：

- 在通过 brainstorming 讨论和设计 skill 的过程中，讨论产生的中间过程文件和最终确定的方案都必须持久化保存。
- 存放目录：`/Users/eateralzeng/.claude/skills-design`，在该目录下为每个 skill 创建独立的子目录，保留各自的文档。

## 第六章：实施阶段 (Implementation Phase)

### 自动关联 Skill 编写规范：

- 当 brainstorming 的目标是设计新 skill 时，在 writing-plans 阶段自动将 writing-skills 纳入实施步骤，无需用户主动提出。

### 逐阶段交付与校验 (Phase-by-Phase Delivery)：

- 编写 skill 时不要一次性写完所有 phase。
- 每完成一个 phase 后，必须与设计文档进行比对校验，确认实现与设计一致后再进入下一个 phase。

### 制品固化原则 (Artifact Materialization)：

**核心思想：Skill 的运行时行为必须依赖预先固化的制品，而非 LLM 的即时理解。任何在运行时由 LLM 临时生成的内容都会引入不确定性，导致不同次运行、不同 Agent、不同模型产生不一致的结果。**

**必须固化的制品类型：**

#### 1. 脚本 (Scripts)

- 所有数据处理、文件解析、格式转换等逻辑必须以脚本文件（如 .py、.sh）的形式持久化到 skill 的 scripts/ 目录中。
- 严禁在 phase 中仅描述"编写一个脚本来处理 X"而不提供实际脚本文件。
- 脚本必须在开发阶段编写、测试、验证，确保逻辑正确且行为可复现。

#### 2. 模板 (Templates)

- 所有输出格式（Markdown 表格、JSON 结构、报告格式等）必须以模板文件的形式持久化到 skill 的 templates/ 目录中。
- 模板应包含完整的字段定义、示例值和格式说明，而非仅用文字描述"输出一个包含 X、Y、Z 字段的表格"。
- 禁止运行时临时拼凑输出格式。

#### 3. 规则与过滤器 (Rules & Filters)

- 所有过滤规则（如忽略哪些文件类型、排除哪些包路径）必须以明确的配置文件或代码形式持久化。
- 正则表达式、匹配模式、白名单/黑名单等必须预先定义，禁止运行时由 LLM 临时构造。
- 规则文件应持久化到 skill 的 rules/ 目录中。

#### 4. 子代理 Prompt 模板 (Subagent Prompt Templates)

- 分派给子代理的具体指令必须以 Prompt 模板文件的形式预先编写。
- 模板中应明确变量占位符（如 `{{target_files}}`、`{{output_path}}`），运行时仅做变量替换，不做内容重写。
- 禁止运行时临时拼接或改写子代理的核心指令。

#### 5. 配置参数 (Configuration)

- 影响执行行为的参数（批次大小、路径匹配模式、阈值、超时时间等）必须以配置文件的形式持久化。
- 禁止在 phase 描述中留有模糊空间让 LLM 在运行时自行决定参数值。
- 配置文件应持久化到 skill 根目录或 config/ 目录中。

#### 6. 阶段定义 (Phase Definitions)

- 每个 phase 的 SKILL.md 描述必须精确到可直接执行的程度，包含：输入来源、输出目标、调用的脚本/模板、异常处理方式。
- 严禁 phase 描述停留在"做某件事"的抽象层面而不指明具体用什么制品来完成。

**判断标准：**

如果一个 phase 在运行时需要 LLM 进行"创作性"工作（编写代码、构造规则、设计格式），而不是"执行性"工作（调用脚本、填充模板、替换变量），那么这个 phase 的设计就是不完整的，必须将创作性工作前移到开发阶段固化为制品。

## 第七章：执行方式选择原则 (Script vs LLM Decision)

**核心思想：能用 SQL/正则/算法完整描述的 → 脚本；需要"看懂代码"的 → LLM。**

### 适合预写 Python 脚本的场景

| 特征 | 说明 |
|------|------|
| 规则可枚举 | 过滤条件、匹配模式可以完整列在配置中 |
| 输入格式固定 | JSON、XML、SQL 等结构化数据，格式由上游 phase 的输出定义 |
| 无歧义 | 同样的输入永远产生同样的输出，不需要做判断 |
| 批量大 | 需要处理成百上千条数据，LLM 太慢太贵 |

典型场景：图数据库查询与遍历、数据合并去重、格式转换、模板填充。

### 适合 LLM 直接执行的场景

| 特征 | 说明 |
|------|------|
| 规则有灰度 | 匹配条件不是简单的 yes/no，需要理解上下文判断 |
| 需要理解语义 | 读代码理解"这个方法在做什么"、理解注解与方法的对应关系 |
| 结构不固定 | 不同文件的组织方式不同，正则难以覆盖所有变体 |
| 需要生成内容 | 写业务描述、总结归纳、生成自然语言内容 |

典型场景：源码扫描与入口识别、源码校验与描述填充、异常链路的人工判断。

### 灰度场景的处理

当任务介于两者之间时（如入口扫描规则已定义，但应用时需要理解代码结构）：

- **优先考虑投入产出比**：用 LLM 几分钟能完成的任务，不值得花大量时间开发健壮的解析脚本
- **可以拆分**：将确定性部分交给脚本，判断性部分交给 LLM。例如 Phase 1 的 grep 搜索由 LLM 执行，nodeId 对齐由脚本完成
- **禁止妥协方案**：不得因时间紧迫而生成临时脚本替代 LLM 执行。如果任务需要 LLM 理解能力，就必须用 LLM；如果任务适合脚本，就必须在开发阶段提前写好并纳入 scripts/ 目录

## 第八章：阶段间文件管理原则 (Inter-Phase File Management)

**核心思想：每个阶段拥有自己产出的文件，跨阶段只能读取，不能修改。公共文件的定义独立于任何阶段。**

### 原则 1：阶段文件所有权 (Phase File Ownership)

- 每个 phase 产出的文件存放在自己阶段的目录下（如 `phase1/`、`phase2/`）
- 其他阶段**只能读取**这些文件，**不能写入或修改**
- 如果后续阶段需要基于前序阶段的文件做修改，必须**复制到当前阶段目录**后在副本上修改并保存
- 目的：保证每个阶段的原始产出不被篡改，支持断点续传和问题追溯

### 原则 2：公共文件统一定义 (Shared File Definition)

- 跨阶段共享的状态文件（如 `progress.json`）的**格式定义和使用机制**必须统一放在一个位置（通常是编排者文件 prompt.md），独立于任何 phase
- 各 phase 不重复定义公共文件的格式，只能引用编排者中的定义
- 公共文件的写入由编排者统一管理，phase 脚本不直接写入

### 原则 3：文件格式必须显式定义 (Explicit Format Definition)

- 所有需要落盘的文件都必须明确其 JSON/数据格式
- 格式定义只能出现在以下三处之一：
  1. phase 规格文件（`phases/phaseX-xxx.md`）— 当格式由 LLM 生成时
  2. Python 脚本代码 — 当格式由脚本生成时
  3. 编排者文件（`prompt.md`）— 当格式为公共文件时
- 禁止在多处重复定义同一文件的格式（单点事实）
- 优先以脚本代码为准：当 phase 规格文件和脚本对同一输出格式有不同描述时，以脚本为准

## 第九章：验证制品原则 (Verification Artifacts)

**核心思想：每个产出关键数据的 phase 都应配套一个独立的 verify 脚本，作为 design 契约的「可执行校验视图」。verify 的价值在于能客观抓出 skill 自身的 bug——前提是它独立于被验证的代码。**

### 原则 1：先产物，后校验 (Output-First)

- 必须**先运行 phase、拿到真实产物**，再编写 verify。
- 理由：① 真实产物暴露 design 没写全的字段/形状；② verify 需在真实数据上做正向测试；③ 真实数据才能暴露真实质量问题（如 phase2a verify 在真实数据上发现自环、condition=null、截断流）。
- 流程：跑 phase → 读 `phaseX-design.md`（schema/决策）+ phase 代码（了解产物与意图）→ 基于 design 规格独立写 `verify_phaseX.py` → 真实产物上正向 + 破坏测试。

### 原则 2：独立实现，不 import skill (Independent Reimplementation)

- verify 脚本**严禁 import 被验证的 skill 模块**（解析、图遍历、表名正则等一律在 verify 内重新实现，以 design 规格为准）。
- 理由：若 verify 复用 skill 代码，skill 的 bug 会被一起带进 verify、互相掩盖，验证形同虚设。只有独立实现，verify 才能抓出 skill 解析/逻辑的 bug。
- 代价（接受）：表名正则、nodeId 构造等与 skill 重复一份；换来客观性 + skill 演进时 verify 仍稳定。

### 原则 3：校验基准是 design 契约，不是 code (Spec as the Yardstick)

- 判定对错的标准来自 **design 规格**（schema/不变量/决策），不是照抄 code 行为。
- 读 code 只为「知道产物长什么样 + 理解意图」；若 verify 照着 code 写，code 有 bug 时 verify 会「跟着错、照样 pass」。
- 必须以 design 当标尺，才能在 **code 偏离 design 时**报出来（如 code 漏实现 design 某步 → verify 视角下表现为产物不完整）。

### 原则 4：分层报告 + 破坏测试 (Layered Report & Negative Testing)

- 输出分层：✅ pass / ⚠️ warn / ❌ error；退出码 0=无 error、1=有 error（warn 不影响）。
- 级别约定：违反 design 硬契约（schema 缺失、引用悬空、唯一性破坏）→ error；数据质量信号（可达性、保序、疑似漏标、截断流）→ warn。
- 必须做**破坏测试**：人为改坏一个字段/引用，确认 verify 能报 error + 退出码 1，证明它真有检出能力。
- graceful：产物缺失/初始态/空类型不 crash，降级为 warn。

### 原则 5：制品沉淀 + 树级优于状态级 (Materialize & Tree-over-State)

- verify 脚本落 `scripts/verify_phaseX.py`，配套 `design/phaseX-verify.md`（5 节：定位 / 维度表 / 用法 / 与 design 搭配 / 已知限制），design.md 索引登记，实测发现记入 issue-list。禁止用一次性临时脚本校验后即弃。
- 校验「完整性/一致性」时，**优先基于最终产物（如 tree）做独立判定，而非依赖过程状态文件（如 progress）**——下游消费的是产物，过程状态可能与产物不同步（如某操作改了 tree 却没更新 progress，状态级校验会漏报）。

