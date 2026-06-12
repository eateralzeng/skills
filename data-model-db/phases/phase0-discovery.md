# Phase 0: 表清单解析 + graph.db 对齐

## 目标
以用户提供的表清单为唯一可信源，与 graph.db 中的表映射做并集对齐，建立完整的表注册表。

## 输入
- 用户提供完整表清单文件（txt/DDL/直连导出，每行一个表名）— 唯一可信源
- graph.db `CodeElement` 节点（kind=mybatis-statement）— 辅助验证，发现代码中的表映射
- 已有 `db-schema.json`（可选）— 辅助验证，交叉比对；如文件不存在则跳过，不影响执行

## 算法步骤

1. **解析用户表清单文件**，得到用户表名列表
2. **从 graph.db 提取表名**：查询 `CodeElement` 节点（kind=mybatis-statement），从 sqlText 中按 SQL 类型分别提取表名：
   - SELECT：`FROM\s+(\w+)`, `JOIN\s+(\w+)`（支持多表查询）
   - UPDATE：`UPDATE\s+(\w+)`（首个表名）
   - INSERT：`INTO\s+(\w+)`（INTO 后的表名）
   - DELETE：`DELETE\s+FROM\s+(\w+)`（FROM 后的表名）
   - 忽略 `<if>`, `<set>`, `<include>` 等 MyBatis 动态标签
   - 多表 JOIN 时，所有涉及的表都关联该 statement
3. **对用户表清单与 graph.db 表名做并集**，通过 `source` 字段标记每张表的来源：
   - `USER_AND_DB`：用户清单和 graph.db 都有，数据最完整
   - `USER_ONLY`：仅在用户清单中，代码中无 MyBatis 映射（可能通过 JDBC 直接访问或已废弃）
   - `DB_ONLY`：仅在 graph.db 中，用户未列出（可能是遗漏、测试表、或已下线的表）
4. **对并集中的每张表**，按 namespace 分组建立 Mapper → 表映射
5. **计算覆盖状态**（仅对 source 为 USER_AND_DB 或 DB_ONLY 的表有意义）：
   - `FULL`：表在 graph.db 中有完整的 CRUD 覆盖（至少有 SELECT + 一种写操作）
   - `PARTIAL`：表在 graph.db 中仅有部分操作（如只有 SELECT）
   - `ORPHAN`：表在 graph.db 中无对应数据（source 为 USER_ONLY 的表固定为 ORPHAN）
6. **可选交叉比对**：如 `db-schema.json` 存在，读取其中的表信息与当前结果做交叉比对，发现可能的遗漏（不作为可信源，仅辅助发现）

## 输出格式

`table-registry.json`:

```json
{
  "tables": [
    {
      "tableName": "mbp_acc_request_info",
      "source": "USER_AND_DB",
      "mapperNamespaces": ["com.webank.cbrc.tidb.mapper.TidbMbpAccRequestInfoMapper"],
      "coverage": "FULL",
      "statementCount": { "select": 10, "insert": 3, "update": 4, "delete": 2 }
    },
    {
      "tableName": "some_partial_table",
      "source": "USER_AND_DB",
      "mapperNamespaces": ["com.webank.cbrc.repo.mapper.SomeMapper"],
      "coverage": "PARTIAL",
      "statementCount": { "select": 2 },
      "note": "仅有 SELECT 操作，缺少 INSERT/UPDATE/DELETE"
    },
    {
      "tableName": "some_user_only_table",
      "source": "USER_ONLY",
      "mapperNamespaces": [],
      "coverage": "ORPHAN",
      "statementCount": {},
      "note": "用户清单中存在，但代码中无 MyBatis 映射"
    },
    {
      "tableName": "some_db_only_table",
      "source": "DB_ONLY",
      "mapperNamespaces": ["com.webank.cbrc.repo.mapper.SomeInternalMapper"],
      "coverage": "PARTIAL",
      "statementCount": { "select": 1 },
      "note": "代码中有 MyBatis 映射，但用户未列入清单"
    }
  ]
}
```

来源状态说明：
- `USER_AND_DB`：用户清单和 graph.db 都有，数据可信度最高
- `USER_ONLY`：仅在用户清单中，可能通过非 MyBatis 方式访问或已废弃
- `DB_ONLY`：仅在 graph.db 中，需人工确认是否遗漏

覆盖状态说明（仅对 source 为 USER_AND_DB 或 DB_ONLY 的表有意义）：
- `FULL`：表在 graph.db 中有完整的 CRUD 覆盖（至少有 SELECT + 一种写操作）
- `PARTIAL`：表在 graph.db 中仅有部分操作（如只有 SELECT）
- `ORPHAN`：表在 graph.db 中无对应数据（source 为 USER_ONLY 的表固定为 ORPHAN）

## graph.db 关键查询

```sql
-- 查询所有 MyBatis Statement 及其 SQL 文本
SELECT id, name, file_path,
       json_extract(properties_json, '$.statementId') AS statementId,
       json_extract(properties_json, '$.statementKind') AS statementKind,
       json_extract(properties_json, '$.namespace') AS namespace,
       json_extract(properties_json, '$.sqlText') AS sqlText
FROM nodes
WHERE label = 'CodeElement'
  AND json_extract(properties_json, '$.kind') = 'mybatis-statement';
```

## 源码验证逻辑

本阶段以用户表清单为 Ground truth，源码验证主要用于：
- 对 `DB_ONLY` 的表，确认 graph.db 提取的表名确实出现在 Mapper XML 或注解中
- 对 `USER_ONLY` 的表，可搜索 Java 源码中是否有 JDBC/JdbcTemplate 直接 SQL 引用该表名

## 错误处理

- **用户表清单文件不存在**：终止执行，要求用户提供表清单文件
- **graph.db 不存在或无法连接**：降级为仅使用用户表清单，所有表标记为 `USER_ONLY`，coverage 为 `ORPHAN`
- **db-schema.json 不存在**：跳过交叉比对步骤，不影响执行
- **SQL 解析失败**：记录警告日志，跳过该 statement，不中断整体流程
- **表名提取为空**：跳过该 statement，记录到错误日志
