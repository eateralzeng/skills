# Phase 1b: DB Schema 收集

## 概述

从 Mapper XML 文件和 Java Mapper 注解中预收集数据库表操作信息，构建 lookup 字典。此阶段与 Phase 1 无数据依赖，可并行执行。

## 输入

- 项目源码目录（`<project_dir>`）

## 输出

- `phase1b/db-schema-tables.json` — 表结构详情（供审查）
- `phase1b/db-schema-lookup.json` — lookup 字典（Phase 2a 消费）

## 前置条件

- 无（可与 Phase 1 并行）

## 执行步骤

1. 运行脚本：
   ```bash
   python3 <skill_dir>/scripts/phase1b_db_schema.py <project_dir> <cache_dir>
   ```
2. 脚本自动扫描 Java Mapper 注解和 XML Mapper 文件
3. 输出两个文件：`db-schema-tables.json` 和 `db-schema-lookup.json`

## 输出文件格式

**db-schema-tables.json**：
```json
{
  "version": "2.0",
  "sources": ["annotation-sql", "mapper-xml"],
  "totalTables": 0,
  "totalOperations": 0,
  "tables": [
    {
      "tableName": "table_name",
      "operations": [
        {
          "mapperClass": "MapperClass",
          "statementId": "methodName",
          "type": "SELECT | INSERT | UPDATE | DELETE",
          "sources": ["annotation-sql"]
        }
      ],
      "sources": ["annotation-sql"]
    }
  ]
}
```

**db-schema-lookup.json**：
```json
{
  "version": "2.0",
  "lookupSize": 0,
  "lookup": {
    "MapperClass.methodName": {"table": "table_name", "operation": "SELECT"}
  }
}
```

## 错误处理

- 如果未找到任何 Mapper 文件，输出空的 tables 数组和 lookup 字典
- 脚本不应因单个文件解析错误而终止
