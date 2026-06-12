# Phase 4: 流程关联 + 覆盖度分析

## 目标
建立表与流程文档的反向索引，分析每张表的操作在流程中的覆盖情况。

## 输入
- Phase 1 的归属链数据（Service/Dao/Mapper 关系）
- `.trace-cache/` 下的 chain JSON 文件（flow-trace-db 产物）
- Phase 2 的 operations 数据

## 算法步骤

1. **扫描 chain JSON 文件**：读取 `.trace-cache/` 下的所有 chain JSON 文件，提取 domainInteraction 中的 tableName
2. **建立反向索引**：按表名分组，建立表 -> 流程的反向索引
3. **交叉验证**：与 Phase 1 的归属链交叉验证：Service 层面的归属是否与流程一致
4. **标记覆盖状态**：根据流程覆盖情况标记每张表的状态：
   - `COVERED`：该表的所有操作都被至少一个流程文档覆盖
   - `PARTIAL`：部分 Mapper 操作未被任何流程覆盖
   - `ORPHAN`：表存在但无流程关联
5. **收集孤儿操作**：标记未被任何流程覆盖的 Mapper 操作，写入 `orphanOperations`

## 输出格式

每张表的 `flowCoverage` 字段：

```json
{
  "flowCoverage": {
    "status": "COVERED",
    "flows": [
      {
        "entryId": "controller-001",
        "entryType": "controller",
        "operations": ["SELECT", "INSERT", "UPDATE"]
      },
      {
        "entryId": "rmb-128",
        "entryType": "rmb",
        "operations": ["SELECT", "UPDATE"]
      }
    ],
    "orphanOperations": []
  }
}
```

覆盖状态说明：
- `COVERED`：该表的所有操作都被至少一个流程文档覆盖
- `PARTIAL`：部分 Mapper 操作未被任何流程覆盖
- `ORPHAN`：表存在但无流程关联

## graph.db 关键查询

本阶段不直接查询 graph.db，主要依赖 `.trace-cache/` 下的 chain JSON 文件。

如需交叉验证，可使用以下查询辅助：

```sql
-- 查询指定 Service 的所有方法（辅助交叉验证）
SELECT id, name, file_path
FROM nodes
WHERE label = 'CodeElement'
  AND json_extract(properties_json, '$.kind') = 'method'
  AND file_path LIKE '%<ServiceClassName>.java';
```

## 源码验证逻辑

本阶段的验证主要通过与已有流程文档的交叉比对完成：

1. **流程归属验证**：检查 chain JSON 中的 domainInteraction.tableName 与 Phase 0 的表名是否一致（注意大小写和命名风格差异）
2. **Service 归属一致性**：chain JSON 中涉及的 Service 是否与 Phase 1 归属链中的 Service 一致
3. **操作类型一致性**：chain JSON 中记录的操作类型（SELECT/INSERT/UPDATE/DELETE）是否与 Phase 2 的 operations 一致
4. 不一致的项记录到覆盖度报告中，标记为 `FLOW_MISMATCH`

## 错误处理

- **`.trace-cache/` 目录不存在**：所有表的 flowCoverage.status 标记为 `ORPHAN`，flows 为空数组，不影响其他 Phase 执行
- **chain JSON 解析失败**：跳过该文件，记录解析错误到日志
- **domainInteraction 中无 tableName 字段**：跳过该 domainInteraction，记录警告
- **表名大小写/风格不匹配**：尝试下划线转驼峰等多种方式匹配，均失败则标记为 `NAME_MISMATCH`
- **交叉验证发现 Service 不一致**：记录到 `flowDiffs`，由人工确认归属链或流程文档的准确性
