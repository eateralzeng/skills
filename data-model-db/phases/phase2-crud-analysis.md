# Phase 2: CRUD 操作分析 + 源码验证

## 目标
从 graph.db 提取每张表的所有 SQL 操作，解析列级细节，并通过源码反向验证确保数据准确性。

## 输入
- Phase 0 的 `table-registry.json`（表名、mapperNamespaces）
- Phase 1 的归属链数据（Service/Dao/Mapper 关系）
- graph.db `CodeElement` 节点（kind=mybatis-statement）
- MyBatis XML 文件、Mapper Java 接口文件、Dao/Service Java 源码

## 算法步骤

1. **从 graph.db CodeElement 的 sqlText 提取**每张表的所有 SQL 语句和操作类型
2. **按 statementKind 分组**（select/insert/update/delete）
3. **对 UPDATE 语句**，解析 SET 子句中的数据库列名：
   - 正则模式：`(\w+)\s*=\s*#\{` — 匹配 `column_name = #{param}` 格式，取 `=` 左侧的列名
   - 过滤掉 `jdbcType` 等噪音（通过排除 `#{...}` 内部的匹配）
   - 对 `<include>` 引用标记为 `UNRESOLVED_INCLUDE`，不做深度解析
   - 对 `<if>` 条件块，提取内部所有符合 `(\w+)\s*=\s*#\{` 模式的列名
4. **对 INSERT 语句**，解析列名列表：
   - 正则 `INSERT\s+INTO\s+\w+\s*\(([^)]+)\)`，提取括号内逗号分隔的列名
   - `<trim>` 块内使用 `(\w+)\s*=\s*#\{` 提取
5. **对 SELECT 语句**，识别特殊查询语义（如 selectOverdue*, selectBy* 等命名模式）
6. **源码验证**：根据生成的 operations 数据，按以下优先级查找源码进行反向验证：
   - **优先路径 — MyBatis XML**：按 namespace 定位 XML 文件（如 `src/main/resources/mapper/XxxMapper.xml`），读取并比对 statementId、statementKind、setFields/列名
   - **回退路径 1 — MyBatis 注解**：未找到 XML 时，读取 Mapper Java 接口文件，提取 `@Select`/`@Insert`/`@Update`/`@Delete` 注解中的 SQL 进行比对
   - **回退路径 2 — 内联 SQL**：以上均未找到时，扫描 Dao/Service 源码中的 JdbcTemplate 调用或 EntityManager 原生查询，提取 SQL 进行比对
   - 回退路径均未找到对应源码的操作，标记为 `SOURCE_NOT_FOUND`，由人工确认
   - 不一致的项写入 `crudDiffs`，由人工确认是否以源码为准修复

## 输出格式

每张表的 `operations` 字段 + `crudDiffs` 差异报告：

```json
{
  "operations": {
    "select": [
      {
        "statementId": "selectByCondition",
        "namespace": "TidbMbpAccRequestInfoMapper",
        "fields": ["*"],
        "description": "按条件查询"
      }
    ],
    "update": [
      {
        "statementId": "updateByPk",
        "namespace": "TidbMbpAccRequestInfoMapper",
        "setFields": ["status", "ret_message", "remark", "update_time"],
        "description": "按主键更新"
      }
    ]
  },
  "crudDiffs": [
    {
      "statementId": "updateByPk",
      "source": "TidbMbpAccRequestInfoMapper.xml",
      "sourceType": "MYBATIS_XML",
      "dbSetFields": ["status", "ret_message", "remark", "update_time"],
      "sourceSetFields": ["status", "ret_message", "remark", "update_time", "operator"],
      "detail": "XML 中 updateByPk 的 SET 字段比 graph.db 多 operator"
    },
    {
      "statementId": "batchUpdate",
      "source": "SomeDao.java",
      "sourceType": "SOURCE_NOT_FOUND",
      "dbSetFields": ["status"],
      "sourceSetFields": [],
      "detail": "未找到对应源码（非 XML/注解/JdbcTemplate），需人工确认"
    }
  ]
}
```

## graph.db 关键查询

```sql
-- 查询指定表关联的所有 MyBatis Statement
SELECT id, name,
       json_extract(properties_json, '$.statementId') AS statementId,
       json_extract(properties_json, '$.statementKind') AS statementKind,
       json_extract(properties_json, '$.namespace') AS namespace,
       json_extract(properties_json, '$.sqlText') AS sqlText
FROM nodes
WHERE label = 'CodeElement'
  AND json_extract(properties_json, '$.kind') = 'mybatis-statement'
  AND json_extract(properties_json, '$.sqlText') LIKE '%<table_name>%';
```

## 源码验证逻辑

按以下优先级查找源码进行反向验证：

1. **MyBatis XML（优先路径）**：
   - 按 namespace 定位 XML 文件路径（如 `src/main/resources/mapper/XxxMapper.xml`）
   - 读取 XML 内容，按 statementId 查找对应的 SQL 块
   - 比对 statementId、statementKind 是否一致
   - 对 UPDATE/INSERT：比对 setFields/列名是否与 graph.db 提取结果一致
   - 不一致项写入 `crudDiffs`，sourceType 标记为 `MYBATIS_XML`

2. **MyBatis 注解（回退路径 1）**：
   - 读取 Mapper Java 接口文件
   - 提取 `@Select`/`@Insert`/`@Update`/`@Delete` 注解中的 SQL
   - 比对 SQL 内容和提取的列名
   - 不一致项写入 `crudDiffs`，sourceType 标记为 `MYBATIS_ANNOTATION`

3. **内联 SQL（回退路径 2）**：
   - 扫描 Dao/Service 源码中的 JdbcTemplate 调用或 EntityManager 原生查询
   - 提取 SQL 进行比对
   - 不一致项写入 `crudDiffs`，sourceType 标记为 `INLINE_SQL`

4. **均未找到**：标记为 `SOURCE_NOT_FOUND`

## 错误处理

- **graph.db 中无该表的 Statement**：该表标记为 `ORPHAN`，operations 为空对象
- **XML 文件不存在**：回退到注解路径，记录回退信息
- **XML 解析失败**：记录解析错误，保留 graph.db 原始结果，标记为 `XML_PARSE_ERROR`
- **SQL 正则提取无结果**：记录警告，保留 graph.db sqlText 原始值，setFields 标记为 `UNPARSED`
- **`<include>` 引用无法解析**：标记为 `UNRESOLVED_INCLUDE`，不中断流程
