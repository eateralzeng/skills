# Phase 5: 文档生成

## 目标
将 Phase 0-4 的完整数据整合为结构化 JSON 和 Markdown 文档，生成单表文档和汇总文档。

## 输入
- Phase 0 的 `table-registry.json`（表注册信息）
- Phase 1 的归属链数据（Service/Dao/Mapper 关系）
- Phase 2 的 operations 数据（CRUD 操作详情）
- Phase 3 的 stateTransitions 数据（状态流转）
- Phase 4 的 flowCoverage 数据（流程覆盖度）

## 算法步骤

1. **为每张表生成结构化 JSON 文件**：合并 Phase 0-4 的所有数据，写入 `data-model-db/tables/<table-name>.json`
2. **为每张表生成 Markdown 文档**：按模板生成 `data-model-db/tables/<table-name>.md`
3. **生成汇总文件**：按汇总模板生成 `data-model-db/tables/table-summary.md`

## 输出格式

### 单表 JSON

`data-model-db/tables/<table-name>.json` — 包含 Phase 0-4 的完整数据。

### 单表 Markdown

`data-model-db/tables/<table-name>.md`，模板如下：

```markdown
# 表生命周期：<table_name>

> 模块：<所属模块>
> 生成器：flow-trace-db | 数据源：graph-db + 源码

---

## 1. 基本信息

| 项目 | 详情 |
|------|------|
| 表名 | <table_name> |
| Mapper | <mapper_class_list> |
| 所属 Service | <service_class_list> |
| 关联流程 | <N>个 |
| 覆盖状态 | COVERED / PARTIAL / ORPHAN |

---

## 2. CRUD 操作

| 操作类型 | 方法数 | 主要方法 |
|---------|--------|---------|
| SELECT | N | method1, method2, ... |
| INSERT | N | method1, method2, ... |
| UPDATE | N | method1, method2, ... |
| DELETE | N | method1, method2, ... |

---

## 3. 状态流转

字段：<status_field> (<enum_class>)

```
SUBMITTED -> DISPATCHED -> PROCESSING -> COMPLETED
                                              └-> FAILED
```

---

## 4. 归属关系

### 模块归属
| 模块 | Service | 操作 |
|------|---------|------|
| cbrc-bs | RequestDealService | 读/写 |
| ccp-rcn | CbrcRequestReconService | 读 |

### 关联流程
| 流程 | 类型 | 操作 |
|------|------|------|
| controller-001 | controller | SELECT, INSERT, UPDATE |
| rmb-128 | rmb | UPDATE |

---

## 5. 领域归属提示（需人工审核）

> 以下为自动化提示，不作为确定性结论，需结合业务知识人工判断。

- 状态流转丰富度：<N>个状态值，<M>个流转路径
- 共享 Service 数量：<N>个 Service 操作此表
- 跨模块操作：<列出跨模块的 Service>
- 潜在聚合根特征：<有/无>完整生命周期（创建->处理->终态）
```

### 汇总 Markdown

`data-model-db/tables/table-summary.md`，模板如下：

```markdown
# 数据库表生命周期汇总

## 覆盖度统计
| 状态 | 表数 |
|------|------|
| COVERED | N |
| PARTIAL | N |
| ORPHAN | N |

## 表清单
| 表名 | Mapper | Service数 | 流程数 | 状态字段 | 覆盖 |
|------|--------|----------|--------|---------|------|

## 领域归属建议
...
```

## graph.db 关键查询

本阶段不查询 graph.db，仅对已有数据做格式转换和文档生成。

## 源码验证逻辑

本阶段不进行源码验证，仅整合 Phase 0-4 已验证的数据生成文档。如需补充验证，应回溯到对应 Phase 重新执行。

## 错误处理

- **某 Phase 数据缺失**：对应章节输出 `数据未就绪`，不中断整体文档生成
- **JSON 合并冲突**：以高信任等级的数据源为准（Phase 0 > Phase 1 > ...）
- **Markdown 模板渲染失败**：降级输出纯文本格式，记录渲染错误
- **输出目录不存在**：自动创建 `data-model-db/tables/` 目录
- **文件写入权限不足**：报错并终止，提示用户检查目录权限
