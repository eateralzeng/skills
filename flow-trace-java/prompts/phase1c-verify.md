# Phase 1c 精筛：分发点验证

你是 Java 代码分析专家。你的任务是验证以下候选分发点是否为真实的多态分发点。

## 输入

- 待验证的分发点列表：`{{patterns}}`
- 项目根目录：`{{project_dir}}`

## 验证步骤

对每个 pattern：

1. **读取接口/抽象类源文件**（interfaceFilePath）
   - 如果 interfaceFilePath 为 null，从接口全限定名推测文件路径（`project_dir/**/src/main/java/**/{ShortName}.java`）
2. **读取 2-3 个 sampleImplementations 的源文件**
3. **按下面的检查清单逐条判断**

## 检查清单（逐条回答 yes/no）

对每个候选，回答以下 5 个问题：

### Q1: 接口/抽象类是否定义了非 getter/setter 的行为方法？
- yes = 至少有一个方法不是 get*/set*/is*/with* 开头的
- no = 全部是 getter/setter → **直接判定 verified=false**

### Q2: 是否存在运行时选择实现的场景？
- yes = 有代码通过 List<Xxx> / Map<String, Xxx> / Stream.filter 收集并选择实现
- no = 每个实现类独立被调用（如各自绑定不同的 @RmbTopic），无统一选择逻辑

### Q3: 是否存在路由/匹配方法？
- yes = 有 support()、matches()、accept() 等方法根据条件返回 boolean
- no = 无路由方法

### Q4: 实现类之间的核心方法逻辑是否不同？
- yes = 读取 2-3 个实现类源码，核心方法的 if/for/调用链有明显差异
- no = 核心方法逻辑相同，只是操作的实体/数据不同

### Q5: 子类差异是否超过"配置级别"？
- yes = 子类有实质性的代码差异（不同的算法、不同的调用链、不同的业务规则）
- no = 子类仅差异在：RMB topic 名、cron 表达式、DAO 注入、参数值、泛型类型

## 判定规则

```
Q1=no → verified=false（纯数据接口）
Q2=no AND Q3=no → verified=false（无运行时分发机制）
Q4=no AND Q5=no → verified=false（同构操作/配置差异）
Q4=yes OR Q5=yes → verified=true
其余 → verified=true, confidence=MEDIUM
```

## 输出格式

必须输出合法 JSON，不要附加其他文字：

```json
{
  "results": [
    {
      "interface": "完整接口全限定名",
      "verified": true,
      "confidence": "HIGH",
      "reason": "判断理由（一句话）",
      "checklist": {
        "q1_has_behavior_method": true,
        "q2_runtime_selection": true,
        "q3_has_route_method": true,
        "q4_logic_differs": true,
        "q5_beyond_config": true
      }
    }
  ]
}
```

要求：
- 每个 pattern 必须有结果，不能遗漏
- `interface` 必须与输入 pattern 的 `interface` 字段完全一致
- `confidence` 取值：HIGH（所有判定条件明确）、MEDIUM（有模糊条件）、LOW（不确定）
- `checklist` 必须逐条填写，作为判定依据
- reason 简明扼要，一句话说明判断依据
