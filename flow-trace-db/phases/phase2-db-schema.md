# Phase 2: 数据库表信息收集

> 多源并行合并，生成 `db-schema.json`（含 lookup 字典），为 Phase 3 链路提取提供数据库终点判断依据。

## 概述

Phase 2 从 3 个数据源并行收集数据库表操作信息，合并去重后生成 `db-schema.json`。本阶段与 Phase 1 无数据依赖，可并行执行。

## 前置条件

- 已知项目源码目录路径（`projectPath`）
- 已知 graph.db 文件路径（`dbPath`）

## 数据源

| 数据源 | 脚本函数 | 提供内容 |
|--------|---------|---------|
| Java Mapper 注解 | `source_annotation_sql()` | 扫描 `@Select`/`@Insert`/`@Update`/`@Delete`，从 SQL 文本提取表名 |
| XML Mapper 文件 | `source_xml_mapper()` | 解析 `<select>`/`<insert>`/`<update>`/`<delete>` 标签，从 SQL body 提取表名 |
| graph.db QUERIES | `source_graph_db()` | 查询 `relationships(type='QUERIES')` 关联的 CodeElement 节点属性 |

三个数据源自动启用，无需用户配置。

## 执行步骤

### 步骤 1: Java Mapper 注解扫描（Source 1）

扫描 `**/*Mapper.java` 和 `**/*Dao.java` 文件：

1. 逐字符定位 `@Select`/`@Insert`/`@Update`/`@Delete` 注解
2. 提取注解括号内的 SQL 文本（支持 `{"line1", "line2"}` 多行格式和 `"单行"` 格式）
3. 从注解后的内容中提取紧随的方法签名
4. 操作类型直接取注解名（如 `@Select` → `SELECT`），比 SQL 文本推断更可靠
5. 用正则 `TABLE_PATTERNS` 从 SQL 文本提取表名

### 步骤 2: XML Mapper 文件解析（Source 2）

扫描 `**/*Mapper.xml` 和 `**/*mapper*.xml`（覆盖 `resources/` 和 `java/` 两个目录）：

1. 从 `<mapper namespace="...">` 提取 namespace，取最后一段作为 mapperClass
2. 遍历 `<select>`/`<insert>`/`<update>`/`<delete>` 标签
3. 从标签 `id` 属性取 statementId
4. 标签名直接决定操作类型
5. 从 SQL body 用 `TABLE_PATTERNS` 正则提取表名

### 步骤 3: graph.db QUERIES 查询（Source 3）

```sql
SELECT
    m.name AS mapper_method,
    c.name AS mapper_class,
    ce.name AS statement_id,
    json_extract(ce.properties_json, '$.statementKind') AS statement_kind,
    json_extract(ce.properties_json, '$.tableName') AS table_name_direct,
    json_extract(ce.properties_json, '$.sqlText') AS sql_text,
    json_extract(ce.properties_json, '$.namespace') AS namespace
FROM relationships r
JOIN nodes m ON m.id = r.source_id AND m.label = 'Method'
JOIN nodes ce ON ce.id = r.target_id
LEFT JOIN relationships hm ON hm.target_id = m.id AND hm.type = 'HAS_METHOD'
LEFT JOIN nodes c ON c.id = hm.source_id
WHERE r.type = 'QUERIES'
```

**与 Spec 原版的差异**（脚本已优化）：
- `JOIN nodes m` 加了 `AND m.label = 'Method'` 过滤，避免非 Method 节点混入
- `JOIN relationships hm` 和 `JOIN nodes c` 改为 `LEFT JOIN`，处理 HAS_METHOD 缺失的情况

**表名提取优先级**：`table_name_direct` → SQL 文本正则 → namespace 类名转下划线

### 步骤 4: 合并去重

按以下规则合并三个源的 entries：

1. **表名**为唯一键，空表名直接丢弃
2. 每个 table 下按 `(mapperClass, mapperMethod, operation)` 三元组去重
3. 同一操作被多个源发现时，`sources` 数组记录所有来源

### 步骤 5: 构建 lookup 字典 + Tidb 前缀兼容

从合并后的 tables 构建 lookup 字典：

```
key = "MapperClass.methodName" → {table: "表名", operation: "SELECT"}
```

额外为每个条目生成 `Tidb` 前缀版本（如 `CbrcAccountInfoMapper.selectByTokenKey` → `TidbCbrcAccountInfoMapper.selectByTokenKey`），供 Phase 3 匹配带 Tidb 前缀的 Mapper 类。

### 步骤 6: 写入 db-schema.json

```bash
python3 <skill_dir>/scripts/phase2_db_schema.py <project_dir> <cache_dir> <db_path>
```

输出写入 `<cache_dir>/phase2/db-schema.json`。

## 输出文件格式

> 以下格式基于脚本 `scripts/phase2_db_schema.py` 实际输出编写。脚本是格式的权威定义。

`<cache_dir>/phase2/db-schema.json`：

```json
{
  "version": "2.0",
  "sources": ["annotation-sql", "mapper-xml", "graph-db"],
  "totalTables": 116,
  "totalOperations": 1058,
  "lookupSize": 1998,
  "tables": [
    {
      "tableName": "cbrc_access_token",
      "operations": [
        {
          "mapperClass": "CbrcAccessTokenMapper",
          "statementId": "deleteByTokenKey",
          "type": "SELECT",
          "sources": ["annotation-sql", "graph-db"]
        }
      ],
      "sources": ["annotation-sql", "graph-db"]
    }
  ],
  "lookup": {
    "CbrcAccessTokenMapper.selectByTokenKey": {"table": "cbrc_access_token", "operation": "SELECT"},
    "TidbCbrcAccessTokenMapper.selectByTokenKey": {"table": "cbrc_access_token", "operation": "SELECT"}
  }
}
```

**tables[].operations[] 字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| mapperClass | string | Mapper/Dao 类名（不含包路径） |
| statementId | string | SQL 语句标识（XML 的 id / Java 方法名） |
| type | string | 操作类型：SELECT / INSERT / UPDATE / DELETE |
| sources | string[] | 发现该操作的数据源列表 |

**lookup 字典说明**：

Phase 3 链路提取时的消费方式：

```python
key = f"{child_class}.{child_method}"
if key in lookup:
    # → 这是数据库终点
    domainInteraction = {
        type: "DATABASE",
        table: lookup[key]["table"],
        operation: lookup[key]["operation"]
    }
```

## 错误处理

| 场景 | 处理 |
|------|------|
| Mapper XML 不存在 | 跳过，仅用其他源 |
| graph.db 无 QUERIES | 跳过，仅用其他源 |
| 所有源无数据 | 输出空 schema（totalTables: 0） |
