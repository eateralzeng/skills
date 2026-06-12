# Phase 1: 归属链构建（表 <- Mapper <- Service）

## 目标
通过 graph.db 的 HAS_PROPERTY 关系和源码验证，构建每张表的完整归属链（表 <- Mapper <- [Dao] <- Service）。

## 输入
- Phase 0 的 `table-registry.json`（包含每张表的 mapperNamespaces 列表）
- graph.db `Class` 节点和 `HAS_PROPERTY` 关系
- Java 源码文件

## 算法步骤

1. **Mapper -> Service/Dao**：通过 graph.db `HAS_PROPERTY` 关系查找持有该 Mapper 的类
   - 实际查询路径：`Class -[HAS_PROPERTY]-> Property`，再通过 `Property.properties_json.declaredType` 字段与 Mapper 类名匹配
2. **Dao -> Mapper**：对于 Dao 类（如 `MssDeleteHisDataDao`），同样通过 HAS_PROPERTY 的 declaredType 找到 Dao 持有的 Mapper
3. **拼接归属链**：表 <- Mapper <- [Dao] <- Service
4. **从 file_path 提取模块信息**（如 `cbrc-bs/src/main/java/...` -> `cbrc-bs`）
5. **源码验证**：根据生成的归属链，读取链路中每个类的 Java 源码文件，验证归属关系是否真实存在：
   - 读取 Service 源码，确认确实声明了 Dao/Mapper 类型的字段（与 graph.db 的 declaredType 一致）
   - 读取 Dao 源码，确认确实声明了 Mapper 类型的字段
   - 不一致的项写入 `ownershipDiffs`，由人工确认是否以源码为准修复

## 输出格式

每张表的归属树 + 差异报告，附加到 `table-registry.json`：

```json
{
  "ownership": {
    "services": [
      {
        "className": "RequestDealService",
        "module": "cbrc-bs",
        "file": "cbrc-bs/src/main/java/com/webank/cbrc/bs/service/RequestDealService.java",
        "via": ["requestInfoDao"]
      }
    ],
    "daos": [
      {
        "className": "AccRequestInfoDao",
        "module": "cbrc-repo",
        "file": "cbrc-repo/src/main/java/com/webank/cbrc/repo/dao/AccRequestInfoDao.java",
        "mappers": ["TidbMbpAccRequestInfoMapper"]
      }
    ]
  },
  "ownershipDiffs": [
    {
      "chain": "RequestDealService -> accRequestInfoDao",
      "expectedType": "AccRequestInfoDao",
      "sourceActual": "TidbAccRequestInfoDao",
      "sourceFile": "cbrc-bs/src/main/java/com/webank/cbrc/bs/service/RequestDealService.java",
      "detail": "源码中 requestInfoDao 字段类型为 TidbAccRequestInfoDao，与 graph.db 记录的 AccRequestInfoDao 不一致"
    }
  ]
}
```

## graph.db 关键查询

```sql
-- 查找持有指定 Mapper 的类（通过 HAS_PROPERTY 的 declaredType 匹配）
SELECT c.name AS owner_class, c.file_path,
       p.name AS prop_name,
       json_extract(p.properties_json, '$.declaredType') AS mapper_type
FROM relationships r
JOIN nodes c ON c.id = r.source_id AND c.label = 'Class'
JOIN nodes p ON p.id = r.target_id AND p.label = 'Property'
WHERE r.type = 'HAS_PROPERTY'
  AND json_extract(p.properties_json, '$.declaredType') = '<MapperClassName>';

-- 查找持有指定 Dao 的类（同理）
SELECT c.name AS owner_class, c.file_path,
       p.name AS prop_name,
       json_extract(p.properties_json, '$.declaredType') AS dao_type
FROM relationships r
JOIN nodes c ON c.id = r.source_id AND c.label = 'Class'
JOIN nodes p ON p.id = r.target_id AND p.label = 'Property'
WHERE r.type = 'HAS_PROPERTY'
  AND json_extract(p.properties_json, '$.declaredType') = '<DaoClassName>';
```

## 源码验证逻辑

根据生成的归属链，按以下步骤进行源码反向验证：
1. 读取 Service 源码文件，搜索字段声明，确认是否存在类型为 Dao/Mapper 的字段
2. 读取 Dao 源码文件，搜索字段声明，确认是否存在类型为 Mapper 的字段
3. 比对源码中的实际类型与 graph.db 记录的 declaredType：
   - 一致：验证通过
   - 不一致：写入 `ownershipDiffs`，记录期望类型和源码实际类型
4. 对源码中存在但 graph.db 未记录的 Dao/Mapper 关系，作为新增发现追加到归属链

## 错误处理

- **Mapper 在 graph.db 中无 Property 引用**：该 Mapper 可能未被任何类注入，标记为 `ORPHAN_MAPPER`，记录到差异报告
- **源码文件不存在**：标记为 `SOURCE_NOT_FOUND`，保留 graph.db 结果，记录到差异报告
- **多层 Dao 嵌套**：最多追踪 3 层（Dao -> Dao -> Mapper），超过则截断并标记 `DEPTH_EXCEEDED`
- **declaredType 为空或为通配符**：跳过该 Property，不纳入归属链
