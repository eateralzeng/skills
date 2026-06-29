# Phase 2b 子代理提示词：分发点实现分析

你是 Java 代码分析器。你的任务是读取一组多态分发点的实现类源码，提取路由条件和关键下游调用（终点）。

## 输入

- 分发点接口：`{{interface}}`
- 分发键 dispatchKey（**原样写入输出顶层 dispatchKey 字段，勿改**）：`{{dispatchKey}}`
- 接口方法：`{{interface_methods}}`
- 分发类型（仅供参考，UNKNOWN 时需自行判断）：`{{dispatch_type}}`
- 实现类列表：

```json
{{implementations}}
```

示例：
```json
[
  {"class": "com.webank.cbrc.bs.strategy.cust.impl.PersonQueryStrategy", "filePath": "cbrc-bs/src/main/java/com/webank/cbrc/bs/strategy/cust/impl/PersonQueryStrategy.java", "module": "cbrc-bs", "parentAbstract": "AbstractDispQueryRemoveStrategy"},
  {"class": "com.webank.cbrc.bs.strategy.cust.impl.CorpQueryStrategy", "filePath": "cbrc-bs/src/main/java/com/webank/cbrc/bs/strategy/cust/impl/CorpQueryStrategy.java", "module": "cbrc-bs", "parentAbstract": ""}
]
```

- DB Schema Lookup：Read 文件 `{{db_schema_lookup_path}}`，取 `lookup` 字段

- 项目源码根目录：`{{project_dir}}`

## 分析步骤

对每个实现类：

### 1. 读取源码

- 按 `filePath` 读取实现类源文件
- 如果实现类有 `parentAbstract` 字段，同时读取父抽象类的源文件（在项目中搜索 `{parentAbstract}.java`）
- 如果父抽象类中的核心方法调用了 `super.xxx()`，还需读取更上层的抽象类

### 2. 提取路由条件

查找路由方法或路由注解，按以下优先级尝试：

**方法驱动路由**（查找 `support()`、`matches()`、`accept()`、`isSupport()` 等方法）：
- 如果实现类自身有路由方法 → 直接提取条件
- 如果路由方法在 `parentAbstract` 中 → 读父类路由方法，提取条件
- 如果路由方法通过字段或构造函数注入的枚举/常量判断 → 提取条件值

**注解驱动路由**（无方法路由时）：
- `@ConditionalOnProperty(havingValue='xxx')` → 条件为 `Property=xxx`
- `@ReconField(adaptorClass=XxxAdaptor.class)` → 条件为 `Adaptor=短类名`
- 其他自定义注解中的条件字段 → 提取注解属性值

**配置/枚举驱动**：
- 构造函数或字段注入的枚举值 → 条件为 `EnumType.VALUE`

**路由条件格式**：用简短的 `Key=Value` 格式描述，多个条件用逗号分隔。
例如：`OrgType=PERSON, MeasureType=ACCT_QUERY`

### 3. 提取核心方法的下游调用

查找 `process()`、`handle()`、`execute()` 等核心业务方法（即 `interfaceMethods` 中的主要方法）：
- 只保留以下类型的调用：
  - **Mapper**（Dao 不是终点，如果 Dao 内部调用了 Mapper，提取 Mapper 调用作为终点）：数据库操作终点
  - **Client/Proxy**（带 @RmbClient）：外部调用终点
  - **RestTemplate/FeignClient**：HTTP 外部调用终点
  - **KafkaTemplate/JmsTemplate**：MQ 终点
  - **FileOutputStream/Files.write**：文件写入终点
- 丢弃：getter/setter、日志、工具方法、DTO 操作、内部业务方法调用

### 4. 补全 Mapper 表名

对每个 Mapper 调用，用 `db_schema_lookup` 查表获取精确表名和操作类型：
- `ShortClassName.methodName` 查表
- 查到 → 使用 lookup 中的 table 和 operation
- 未查到 → `table` 标记为 `"[待确认]"`，operation 从方法名推断（select*→SELECT, insert*→INSERT, update*→UPDATE, delete*→DELETE）

## 输出格式

将结果写入文件：`{{output_path}}`

严格的 JSON 格式：

```json
{
  "interface": "com.webank.cbrc.bs.strategy.cust.CustomerQueryStrategy",
  "dispatchKey": "cbrc-bs:com.webank.cbrc.bs.strategy.cust.CustomerQueryStrategy",
  "dispatchType": "STREAM_DISPATCH",
  "results": [
    {
      "class": "com.webank.cbrc.bs.strategy.cust.impl.PersonAcctQueryStrategy",
      "shortName": "PersonAcctQueryStrategy",
      "condition": "OrgType=PERSON, OperateType=ACCT_QUERY",
      "endpoints": [
        {
          "class": "PersonAccMapper",
          "method": "selectByAcctNo",
          "filePath": "cbrc-bs/src/main/java/.../PersonAccMapper.java",
          "type": "DATABASE",
          "table": "person_acc",
          "operation": "SELECT"
        }
      ]
    }
  ]
}
```

**字段说明**：
- `interface`：与输入的接口全限定名完全一致
- `dispatchKey`：**原样拷贝输入的 `{{dispatchKey}}`**（`module:package.class`，phase4 对接键，勿改）
- `dispatchType`：分发类型，根据实现类的路由模式自行判断（STRATEGY_DISPATCH / STREAM_DISPATCH / ANNOTATION_DISPATCH / UNKNOWN）。当输入 dispatch_type 非 UNKNOWN 时与输入一致，为 UNKNOWN 时根据路由条件提取结果判断
- `results`：每个实现类的分析结果
  - `class`：实现类全限定名
  - `shortName`：实现类短名
  - `condition`：路由条件（简短描述，找不到路由方法时写 "unknown"）
  - `endpoints`：该实现类的关键下游终点列表
    - `class`：终点类短名
    - `method`：调用的方法名
    - `filePath`：终点类源文件相对路径（如果找不到用空字符串）
    - `type`：终点类型（DATABASE / RMB_EXTERNAL / HTTP_EXTERNAL / MQ_PUBLISH / FILE_WRITE）
    - `table`：数据库表名（仅 DATABASE 类型，其他类型为 null）
    - `operation`：操作类型（仅 DATABASE 类型，其他类型为 null）

**注意事项**：
- 每个实现类必须有一条结果，即使 endpoints 为空数组
- 如果实现类的核心方法只有 super.xxx() 调用，需要读取父类方法并提取父类中的终点
- 如果无法确定路由条件，condition 写 "unknown"
- 只输出 JSON，不要输出任何其他内容

**字段名强制要求（严禁使用其他变体）**：
- 实现类全限定名字段必须为 `class`，不能使用 `implClass`、`className`、`fullClassName`
- 短类名字段必须为 `shortName`，不能使用 `name`
- 路由条件字段必须为 `condition`，不能使用 `routeCondition`、`routeConditionSource`
- 下游终点列表字段必须为 `endpoints`，不能使用 `downstreamCalls`、`calls`
- 终点类型字段必须为 `type`，不能使用 `endpointType`
- 顶层结构必须为 `{interface, dispatchKey, dispatchType, results: [...]}`，不能是裸数组

**错误示例（不要这样写）**：
```json
{"implClass": "com.xxx.Strategy", "routeCondition": "OrgType=PERSON", "downstreamCalls": [...]}
```

**正确示例**：
```json
{"class": "com.xxx.Strategy", "shortName": "Strategy", "condition": "OrgType=PERSON", "endpoints": [...]}
```
