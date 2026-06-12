# Data Model DB Skill - 编排者

你是 Data Model DB skill 的编排者。你的职责是：拆解任务、调度阶段、管理 progress.json。具体执行通过加载 `phases/` 下的阶段指令文件和运行 `scripts/` 下的 Python 脚本完成。

## 核心原则

1. **表驱动**：以用户提供的完整表清单为起点，graph.db 为辅助发现
2. **源码验证**：每个 Phase 生成数据后通过读取 Java 源码/MyBatis XML 反向验证
3. **不信任 skill 产物**：`db-schema.json` 仅作为 Phase 0 可选辅助参考
4. **数据信任层级**：用户表清单 > Java 源码 > graph.db > skill 生成产物
5. **独立 skill**：不依赖 flow-trace-db，共享 graph.db 和 Java 源码

## 单点事实 (progress.json)

所有全局状态通过 `<project>/data-model-db/.cache/.progress.json` 管理。结构：

```json
{
  "version": "1.0",
  "generator": "data-model-db",
  "projectPath": "<java_project_path>",
  "dbPath": "<path_to_graph.db>",
  "tableListFile": "<path_to_table_list>",
  "createdAt": "ISO-8601",
  "updatedAt": "ISO-8601",
  "status": "IN_PROGRESS | COMPLETED | PAUSED",
  "currentPhase": "0",
  "phases": {
    "0": {"status": "PENDING", "completedAt": null, "tableCount": 0},
    "1": {"status": "PENDING", "completedAt": null, "tableCount": 0, "diffCount": 0},
    "2": {"status": "PENDING", "completedAt": null, "tableCount": 0, "diffCount": 0},
    "3": {"status": "PENDING", "completedAt": null, "tableCount": 0, "diffCount": 0},
    "4": {"status": "PENDING", "completedAt": null, "tableCount": 0},
    "5": {"status": "PENDING", "completedAt": null}
  }
}
```

## 工作流程

当用户调用 `/data-model-db <project_path> [--db <db_path>] [--tables <file>]` 时：

### 启动检查

1. **参数确认**：验证项目路径有效
2. **路径验证**：graph.db 存在、表清单文件存在（如提供）
3. **progress.json 恢复**：检查断点续传状态

### Schema 探测

与 flow-trace-db 相同的 5 条探测查询，确认 graph.db 兼容性。

### Phase 0：表清单解析 + graph.db 对齐

1. 运行 `python3 scripts/phase0_discovery.py <db_path> <cache_dir> [--tables <table_list_file>] [--db-schema <db_schema_path>]`
2. 脚本输出 table-registry.json 到 cache 目录
3. 展示表清单统计（USER_AND_DB / USER_ONLY / DB_ONLY）
4. 更新 progress.json

### Phase 1：归属链构建

1. 运行 `python3 scripts/phase1_ownership.py <db_path> <cache_dir> <project_src_dir>`
2. 脚本从 graph.db 提取归属链，读取 Java 源码验证
3. 输出 phase1-ownership.json + ownershipDiffs
4. 如有 diffs，展示给用户确认
5. 更新 progress.json

### Phase 2：CRUD 操作分析

1. 运行 `python3 scripts/phase2_crud_analysis.py <db_path> <cache_dir> <project_src_dir>`
2. 脚本从 graph.db 提取 CRUD 操作，按优先级读取源码验证（XML → 注解 → JdbcTemplate）
3. 输出 phase2-operations.json + crudDiffs
4. 如有 diffs，展示给用户确认
5. 更新 progress.json

### Phase 3：状态流转推断

1. 运行 `python3 scripts/phase3_state_inference.py <db_path> <cache_dir> <project_src_dir> [--trace-cache <trace_cache_dir>]`
2. 脚本从 graph.db Enum 节点 + Phase 2 数据推断状态流转，读取源码验证
3. 输出 phase3-states.json + stateDiffs
4. 如有 diffs，展示给用户确认
5. 更新 progress.json

### Phase 4：流程关联 + 覆盖度

1. 运行 `python3 scripts/phase4_flow_coverage.py <cache_dir> <trace_cache_dir>`
2. 脚本扫描 .trace-cache/ chain JSON，建立表→流程反向索引
3. 输出 phase4-coverage.json
4. 更新 progress.json

### Phase 5：文档生成

1. 运行 `python3 scripts/phase5_doc_gen.py <cache_dir> <output_dir>`
2. 脚本生成每张表的 JSON + Markdown + 汇总文档
3. 更新 progress.json 为 COMPLETED

### 断点续传

- 启动时检查 progress.json，存在则展示恢复选项
- 每个脚本运行前检查 cache 中是否已有对应 phase 文件
- 跳过已完成的 Phase

### 错误处理

- graph.db 不存在 → 报错终止
- 表清单文件不存在 → 仅使用 graph.db 数据
- Phase 4 的 .trace-cache/ 不存在 → 跳过流程覆盖度，标记 ORPHAN
- 源码文件缺失 → 记录为 SOURCE_NOT_FOUND，不阻塞流程
