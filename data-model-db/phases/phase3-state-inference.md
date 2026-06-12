# Phase 3: 状态流转推断 + 源码验证

## 目标
从 UPDATE 操作的状态类字段推断数据状态流转路径，通过枚举类源码验证确保准确性。

## 输入
- Phase 2 的 UPDATE setFields（状态类字段列表）
- graph.db `Enum` 节点（枚举常量值）
- graph.db `Property` 节点的 `declaredType` 字段
- 枚举类 Java 源码文件
- 流程文档中的调用上下文（可选）

## 算法步骤

1. **识别状态类字段**：从 Phase 2 的 UPDATE setFields 中匹配命名模式：`*status*`, `*state*`, `*flag*`, `*step*`
2. **确定性匹配（优先路径）**：通过 Property 节点的 `declaredType` 字段查找对应的枚举类
   - 当 `declaredType` 为非基础类型（非 String/Integer/int 等），在 graph.db Enum 节点中按名称精确匹配
   - 示例：Entity 字段 `private ControlRequestStatus status` -> declaredType=`ControlRequestStatus` -> 精确匹配到 Enum 节点
3. **推测性匹配（回退路径）**：当 declaredType 为基础类型时，按字段名 + 枚举类名的模糊匹配
   - 字段名 `request_status` -> 候选枚举 `RequestStatus`, `ControlRequestStatus` 等
   - 采用子串包含 + 驼峰分词匹配：将字段名和枚举类名按驼峰分词后计算词元交集率，取最高分者
4. **读取枚举类源码**：从 Enum 节点的 filePath 获取路径，读取 Java 源码，提取枚举常量值
5. **推断状态变迁方向**：结合流程文档中的调用上下文，推断状态变迁方向
6. **处理无法匹配的字段**：对无法匹配枚举的状态字段，仅记录字段名和出现在哪些 UPDATE 语句中
7. **源码验证**：根据生成的 stateTransitions 数据，读取对应的源码文件反向验证：
   - 读取枚举类 Java 源码，比对 graph.db 提取的枚举值与源码实际内容是否一致
   - 对推测性匹配的字段，源码中可能存在实际使用的枚举类（如 Service 中 import 的枚举、常量类中的值定义），可直接从源码修正
   - 不一致的项写入 `stateDiffs`，由人工确认是否以源码为准修复

## 输出格式

每张表的 `stateTransitions` 字段 + `stateDiffs` 差异报告：

```json
{
  "stateTransitions": [
    {
      "field": "status",
      "enumClass": "ControlRequestStatus",
      "enumFile": "cbrc-bs/src/main/java/com/webank/cbrc/bs/constants/ControlRequestStatus.java",
      "values": [
        {"name": "SUBMITTED", "value": "0", "description": "已提交"},
        {"name": "DISPATCHED", "value": "1", "description": "已分发"},
        {"name": "PROCESSING", "value": "2", "description": "处理中"},
        {"name": "COMPLETED", "value": "3", "description": "已完成"},
        {"name": "FAILED", "value": "9", "description": "失败"}
      ],
      "transitions": [
        {"from": "SUBMITTED", "to": "DISPATCHED", "trigger": "SzCourtReqReceiveHandler"},
        {"from": "DISPATCHED", "to": "PROCESSING", "trigger": "CustomerControlStrategy"},
        {"from": "PROCESSING", "to": "COMPLETED", "trigger": "FeedbackStrategy"}
      ]
    }
  ],
  "stateDiffs": [
    {
      "field": "status",
      "enumClass": "ControlRequestStatus",
      "source": "ControlRequestStatus.java",
      "dbValues": ["SUBMITTED", "DISPATCHED", "PROCESSING", "COMPLETED", "FAILED"],
      "sourceValues": ["SUBMITTED", "DISPATCHED", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"],
      "detail": "源码中多出 CANCELLED(\"4\") 枚举值，graph.db 未记录"
    }
  ]
}
```

状态流转推断的置信度标注：
- `HIGH`：从代码中明确看到状态变更逻辑
- `MEDIUM`：从枚举值命名和顺序推断
- `LOW`：仅有枚举值，无法确定流转方向

## graph.db 关键查询

```sql
-- 查询所有枚举类及其常量值
SELECT id, name, file_path,
       json_extract(properties_json, '$.constants') AS constants
FROM nodes
WHERE label = 'Enum';

-- 查询 Entity 中 declaredType 为非基础类型的 Property
SELECT c.name AS owner_class, c.file_path,
       p.name AS prop_name,
       json_extract(p.properties_json, '$.declaredType') AS declared_type
FROM relationships r
JOIN nodes c ON c.id = r.source_id AND c.label = 'Class'
JOIN nodes p ON p.id = r.target_id AND p.label = 'Property'
WHERE r.type = 'HAS_PROPERTY'
  AND json_extract(p.properties_json, '$.declaredType') NOT IN ('String', 'Integer', 'int', 'Long', 'long', 'Boolean', 'boolean', 'BigDecimal', 'Date', 'LocalDate', 'LocalDateTime');
```

## 源码验证逻辑

1. **枚举值验证**：读取枚举类 Java 源码，提取所有枚举常量及其构造参数，与 graph.db 的 Enum 节点数据比对
2. **推测性匹配修正**：对通过模糊匹配关联的枚举类，从源码中检查：
   - Service 中 import 的枚举类型
   - 常量类中的值定义
   - 如发现更准确的匹配，修正关联关系
3. **差异记录**：枚举值不一致时写入 `stateDiffs`，包含 dbValues 和 sourceValues 的完整列表

## 错误处理

- **无状态类字段**：该表不产生 stateTransitions，正常跳过
- **枚举类文件不存在**：保留 graph.db 枚举值，标记为 `SOURCE_NOT_FOUND`，置信度标为 `LOW`
- **枚举类源码解析失败**：保留 graph.db 枚举值，标记为 `PARSE_ERROR`
- **推测性匹配无候选**：仅记录字段名和关联的 UPDATE 语句，不生成 transitions，标记 `NO_ENUM_MATCH`
- **流转方向无法推断**：记录枚举值列表但不生成 transitions，置信度标为 `LOW`
- **流程文档不存在**：跳过步骤 5 的上下文推断，仅基于枚举值生成，置信度降级
