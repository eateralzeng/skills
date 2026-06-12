# 设计模式识别规则

## 概述

设计模式识别的核心原则：**基于结构特征而非命名猜测，需要多个特征共同验证。**

识别过程遵循以下步骤：
1. 通过 Grep 搜索定位候选代码结构
2. 验证多个结构特征是否同时满足
3. 根据匹配特征数量确定置信度
4. 排除不符合最小特征要求的误报

---

## 高频模式识别规则（7种）

### 1. Template Method（模板方法） — behavioral

**结构特征：**

```bash
# 搜索条件1：抽象类中的 protected abstract 方法
grep -rn "protected abstract" --include="*.java" <target-dir>

# 搜索条件2：子类 override 抽象方法
grep -rn "@Override" --include="*.java" <target-dir> | grep -A 5 "protected"

# 搜索条件3：抽象类中的模板方法（public final 或 public 方法调用 abstract 方法）
grep -rn "public.*final\|public.*void.*{" --include="*.java" <target-dir>
```

代码模式：
```java
// 抽象类定义模板方法和抽象步骤
public abstract class AbstractProcessor {
    public final void process() {     // 模板方法
        step1();
        step2();
        step3();
    }
    protected abstract void step1();   // 抽象步骤
    protected abstract void step2();
    protected void step3() { }        // 可选钩子方法
}

// 具体子类实现抽象步骤
public class ConcreteProcessor extends AbstractProcessor {
    @Override
    protected void step1() { /* 具体实现 */ }
    @Override
    protected void step2() { /* 具体实现 */ }
}
```

**参与者角色：**
- AbstractClass：定义模板方法和抽象步骤的抽象类
- ConcreteClass：实现具体步骤的子类

**置信度判断标准：**
- **high**：抽象类有 `protected abstract` 方法 + 模板方法（`public final` 或普通 `public` 方法）调用这些抽象方法 + 具体子类 `@Override` 实现
- **medium**：仅有抽象类 + 子类继承关系，但模板方法调用链不明确
- **low**：仅有抽象类，无子类实现

---

### 2. Strategy（策略） — behavioral

**结构特征：**

```bash
# 搜索条件1：策略接口及其多个实现类
grep -rn "implements.*Strategy\|implements.*Handler\|implements.*Resolver\|implements.*Processor" --include="*.java" <target-dir>

# 搜索条件2：通过 Map 或条件语句选择策略
grep -rn "Map.*Strategy\|Map.*Handler\|Map.*Resolver\|Map.*Processor" --include="*.java" <target-dir>

# 搜索条件3：@Autowired 或构造注入策略集合
grep -rn "List<.*Strategy>\|Map<String,.*Strategy>\|@Autowired.*List" --include="*.java" <target-dir>

# 搜索条件4：条件选择逻辑
grep -rn "getStrategy\|getHandler\|getBean.*strategy\|switch.*type" --include="*.java" <target-dir>
```

代码模式：
```java
// 策略接口
public interface PaymentStrategy {
    Result process(PaymentRequest request);
}

// 策略实现A
@Component("creditPaymentStrategy")
public class CreditPaymentStrategy implements PaymentStrategy { ... }

// 策略实现B
@Component("debitPaymentStrategy")
public class DebitPaymentStrategy implements PaymentStrategy { ... }

// 持有方通过 Map 选择策略
@Service
public class PaymentService {
    @Autowired
    private Map<String, PaymentStrategy> strategyMap;

    public Result process(String type, PaymentRequest request) {
        PaymentStrategy strategy = strategyMap.get(type + "PaymentStrategy");
        return strategy.process(request);
    }
}
```

**参与者角色：**
- Strategy：策略接口
- ConcreteStrategy：策略的具体实现类（>=2个）
- Context：持有策略引用并根据条件选择的类

**置信度判断标准：**
- **high**：接口 + >=2个实现类 + 持有方通过 `Map<String, Interface>` 或 `List<Interface>` 注入 + 明确的选择逻辑（`get(key)`、`switch`、`if-else` 按类型分发）
- **medium**：接口 + 多个实现类，但选择逻辑不明确或未在代码中体现
- **low**：仅有接口 + 单个实现类

---

### 3. Factory（工厂） — creational

**结构特征：**

```bash
# 搜索条件1：类名含 Factory/Creator
grep -rn "class.*Factory\|class.*Creator" --include="*.java" <target-dir>

# 搜索条件2：create/build 工厂方法返回接口类型
grep -rn "create.*(\|build.*(\|newInstance.*(\|getInstance.*(" --include="*.java" <target-dir> | grep -v "StringBuilder\|StringBuffer"

# 搜索条件3：工厂方法内部的 new 或 Bean 创建
grep -rn "new.*Impl\|new.*Service\|applicationContext.getBean\|BeanUtils.instantiate" --include="*.java" <target-dir>
```

代码模式：
```java
// 工厂类
@Component
public class ReportFactory {
    public ReportService create(ReportType type) {
        switch (type) {
            case DAILY:   return new DailyReportServiceImpl();
            case MONTHLY: return new MonthlyReportServiceImpl();
            default: throw new IllegalArgumentException("Unknown type: " + type);
        }
    }
}

// 或使用 Spring 注入的工厂
@Service
public class TransactionHandlerFactory {
    @Autowired
    private Map<String, TransactionHandler> handlerMap;

    public TransactionHandler create(String transType) {
        return handlerMap.get(transType + "Handler");
    }
}
```

**参与者角色：**
- Factory：工厂类，负责创建对象
- Product：产品接口或抽象类
- ConcreteProduct：具体产品类
- Client：调用工厂方法的类

**置信度判断标准：**
- **high**：类名含 Factory/Creator + 有 `create`/`build`/`newInstance` 方法 + 方法返回接口/抽象类型 + 内部有对象创建逻辑
- **medium**：仅有静态创建方法（如 `static Xxx create(...)`），但类名不含 Factory
- **low**：仅有 `new` 操作，无封装

---

### 4. Adapter（适配器） — structural

**结构特征：**

```bash
# 搜索条件1：类名含 Adapter/Wrapper
grep -rn "class.*Adapter\|class.*Wrapper" --include="*.java" <target-dir>

# 搜索条件2：持有被适配对象字段
grep -rn "private.*adaptee\|private.*delegate\|private.*target\|private.*wrapped" --include="*.java" <target-dir>

# 搜索条件3：实现目标接口
grep -rn "implements.*Adapter\|class.*Adapter implements" --include="*.java" <target-dir>

# 搜索条件4：方法中委托调用被适配对象
grep -rn "adaptee\.\|delegate\.\|wrapped\." --include="*.java" <target-dir>
```

代码模式：
```java
// 目标接口
public interface TargetService {
    Response process(Request request);
}

// 被适配者（已有系统）
public class LegacySystem {
    public LegacyResponse doProcess(LegacyRequest request) { ... }
}

// 适配器
@Component
public class LegacySystemAdapter implements TargetService {
    private final LegacySystem legacySystem;  // 持有被适配对象

    @Override
    public Response process(Request request) {
        LegacyRequest legacyReq = convert(request);
        LegacyResponse legacyResp = legacySystem.doProcess(legacyReq);  // 委托调用
        return convert(legacyResp);
    }
}
```

**参与者角色：**
- Target：客户端期望的接口
- Adapter：实现 Target 接口并持有 Adaptee 引用的类
- Adaptee：需要被适配的已有类

**置信度判断标准：**
- **high**：类名含 Adapter/Wrapper + 持有被适配对象字段（`private` 引用）+ 实现目标接口 + 方法中委托调用被适配对象
- **medium**：仅有 Wrapper/Adapter 命名 + 委托调用，但接口实现关系不明确
- **low**：仅有 Adapter 命名，无结构特征

---

### 5. Builder（建造者） — creational

**结构特征：**

```bash
# 搜索条件1：类名含 Builder
grep -rn "class.*Builder" --include="*.java" <target-dir>

# 搜索条件2：链式 setter 方法（返回 this）
grep -rn "public.*Builder.*with\|public.*Builder.*set\|return this;" --include="*.java" <target-dir>

# 搜索条件3：build() 终端方法
grep -rn "public.*build()" --include="*.java" <target-dir>

# 搜索条件4：静态 builder() 工厂方法
grep -rn "static.*builder()\|Builder\.builder()\|\.builder()" --include="*.java" <target-dir>
```

代码模式：
```java
// 外部类的静态内部 Builder
public class QueryCondition {
    private String field;
    private String operator;
    private Object value;

    private QueryCondition() {}

    public static QueryConditionBuilder builder() {
        return new QueryConditionBuilder();
    }

    public static class QueryConditionBuilder {
        private QueryCondition condition = new QueryCondition();

        public QueryConditionBuilder field(String field) {
            condition.field = field;
            return this;             // 链式 setter
        }
        public QueryConditionBuilder operator(String operator) {
            condition.operator = operator;
            return this;
        }
        public QueryConditionBuilder value(Object value) {
            condition.value = value;
            return this;
        }
        public QueryCondition build() {  // 终端方法
            return condition;
        }
    }
}
```

**参与者角色：**
- Builder：定义链式 setter 和 build 方法的类
- Product：被构建的复杂对象
- Director（可选）：指导构建流程的类

**置信度判断标准：**
- **high**：类名含 Builder + >=2个链式 setter 方法（返回 `this` 或 `Builder` 类型）+ `build()` 终端方法 + 静态 `builder()` 入口
- **medium**：仅有链式调用模式（多个方法返回 `this`），但无 `build()` 方法或 Builder 命名
- **low**：仅有方法链式调用风格（如 `StringBuilder` 使用），无自定义 Builder 结构

---

### 6. Singleton（单例） — creational

**结构特征：**

```bash
# 搜索条件：Spring Bean 注解（框架级单例）
grep -rn "@Component\|@Service\|@Repository\|@Controller\|@RestController\|@Configuration\|@Bean" --include="*.java" <target-dir>

# 经典单例模式（非 Spring 管理）
grep -rn "private static.*instance\|getInstance()\|INSTANCE" --include="*.java" <target-dir>
```

**说明：** 在 Spring 框架中，所有被 `@Component`、`@Service`、`@Repository`、`@Controller`、`@Configuration`、`@Bean` 标注的类默认都是单例（Singleton）。这是**框架级模式**，在架构分析中不单独列出，除非存在自定义的线程安全单例实现（如枚举单例、双重检查锁定等）。

**仅在以下情况单独列出：**
- 使用了经典的 `private static` + `getInstance()` 手动实现
- 使用枚举实现单例
- 使用 `@Scope("singleton")` 显式声明

**参与者角色：**
- Singleton：持有唯一实例并提供全局访问点的类

**置信度判断标准：**
- **high**（经典实现）：`private static` 实例 + 私有构造函数 + `public static getInstance()` + 线程安全措施（`synchronized`、`volatile`、枚举）
- **medium**（Spring Bean）：Spring 注解标注的 Bean — 标注为"框架级"
- **low**：仅有 `static` 字段，但无访问控制

---

### 7. Facade（外观） — structural

**结构特征：**

```bash
# 搜索条件1：Service 类持有多个其他 Service/DAO/Repository 引用
grep -rn "@Autowired\|@Resource\|@Inject" --include="*.java" <target-dir> | grep -c "Service\|DAO\|Repository\|Mapper"

# 搜索条件2：方法体中包含委托调用
grep -rn "\.query\|\.save\|\.update\|\.delete\|\.insert\|\.find\|\.process\|\.handle\|\.execute" --include="*.java" <target-dir>

# 搜索条件3：类名含 Facade 或持有大量依赖
grep -rn "class.*Facade\|class.*Manager\|class.*Orchestrator" --include="*.java" <target-dir>
```

代码模式：
```java
@Service
public class TradeFacade {
    @Autowired private AccountService accountService;
    @Autowired private RiskService riskService;
    @Autowired private MarketDataService marketDataService;
    @Autowired private OrderService orderService;
    @Autowired private NotificationService notificationService;

    @Transactional
    public TradeResult executeTrade(TradeRequest request) {
        // 1. 委托调用 accountService
        accountService.checkBalance(request.getAccountId());
        // 2. 委托调用 riskService
        riskService.evaluate(request);
        // 3. 委托调用 marketDataService
        MarketData data = marketDataService.getQuote(request.getSymbol());
        // 4. 委托调用 orderService
        Order order = orderService.create(request, data);
        // 5. 委托调用 notificationService
        notificationService.notify(request.getAccountId(), order);
        return TradeResult.success(order);
    }
}
```

**参与者角色：**
- Facade：提供简化接口的外观类，持有多个子系统引用
- Subsystem：被委托调用的各个 Service/DAO/Repository

**置信度判断标准：**
- **high**：Service 类持有 >=3 个其他 Service/DAO 引用 + 方法中包含 >=3 个委托调用 + 类名含 Facade/Manager/Orchestrator
- **medium**：持有 2 个其他 Service/DAO 引用 + 有委托调用，但类名不含 Facade
- **low**：持有 1 个引用，可能是简单代理而非 Facade

---

## 中频模式识别规则（5种）

### 1. Proxy（代理） — structural

**结构特征：**

```bash
# 搜索条件1：AOP 注解
grep -rn "@Aspect\|@Around\|@Before\|@After\|@Pointcut" --include="*.java" <target-dir>

# 搜索条件2：JDK 动态代理
grep -rn "InvocationHandler\|Proxy.newProxyInstance" --include="*.java" <target-dir>

# 搜索条件3：CGLIB 代理
grep -rn "MethodInterceptor\|Enhancer\|Callback" --include="*.java" <target-dir>

# 搜索条件4：Spring AOP 配置
grep -rn "@EnableAspectJAutoProxy\|proxyTargetClass" --include="*.java" --include="*.xml" --include="*.yml" --include="*.yaml" --include="*.properties" <target-dir>
```

代码模式：
```java
// AOP 代理示例
@Aspect
@Component
public class LoggingProxy {
    @Around("execution(* com.bank..service.*.*(..))")
    public Object around(ProceedingJoinPoint pjp) throws Throwable {
        log.info("Before: {}", pjp.getSignature());
        Object result = pjp.proceed();  // 委托给目标对象
        log.info("After: {}", pjp.getSignature());
        return result;
    }
}

// JDK 动态代理示例
public class RemoteServiceProxy implements InvocationHandler {
    private Object target;

    public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
        // 前置增强
        Object result = method.invoke(target, args);  // 委托调用
        // 后置增强
        return result;
    }
}
```

**参与者角色：**
- Subject：目标接口
- RealSubject：被代理的真实对象
- Proxy：代理对象（AOP切面、动态代理处理器）

**置信度判断标准：**
- **high**：`@Aspect` + `@Around`/`@Before`/`@After` + `ProceedingJoinPoint.proceed()` 委托调用
- **medium**：仅有 `@Aspect` 注解但无明确的切面逻辑，或仅使用 JDK 动态代理但场景不明确
- **low**：仅有 `@EnableAspectJAutoProxy` 配置，无自定义切面

---

### 2. Decorator（装饰器） — structural

**结构特征：**

```bash
# 搜索条件1：实现同一接口并持有同接口字段
grep -rn "implements.*Decorator\|implements.*Wrapper" --include="*.java" <target-dir>

# 搜索条件2：构造函数或 setter 注入同类型接口
grep -rn "private.*sameInterface\|this\.\w* = \w*;" --include="*.java" <target-dir>

# 搜索条件3：类名含 Decorator
grep -rn "class.*Decorator\|class.*Decorator" --include="*.java" <target-dir>

# 搜索条件4：方法中调用被装饰对象并增加行为
grep -rn "super\.\|delegate\.\|wrapped\.\|this\.\w*\.\w*(" --include="*.java" <target-dir>
```

代码模式：
```java
// 装饰器接口（与被装饰对象相同）
public interface DataSource {
    String read();
    void write(String data);
}

// 具体组件
public class FileDataSource implements DataSource {
    public String read() { /* 读文件 */ }
    public void write(String data) { /* 写文件 */ }
}

// 装饰器基类
public class DataSourceDecorator implements DataSource {
    protected DataSource wrappee;  // 持有同接口字段

    public DataSourceDecorator(DataSource source) {
        this.wrappee = source;
    }

    public String read() { return wrappee.read(); }
    public void write(String data) { wrappee.write(data); }
}

// 具体装饰器：增加加密行为
public class EncryptionDecorator extends DataSourceDecorator {
    public EncryptionDecorator(DataSource source) { super(source); }

    @Override
    public String read() {
        return decrypt(wrappee.read());  // 增强行为
    }
    @Override
    public void write(String data) {
        wrappee.write(encrypt(data));    // 增强行为
    }
}
```

**参与者角色：**
- Component：定义接口的抽象组件
- ConcreteComponent：被装饰的具体对象
- Decorator：持有 Component 引用并实现同一接口的装饰器基类
- ConcreteDecorator：增加额外行为的具体装饰器

**置信度判断标准：**
- **high**：实现同一接口 + 持有同接口类型的 `protected`/`private` 字段 + 方法中先调用被装饰对象再增加行为（增强逻辑明确）
- **medium**：实现同一接口 + 持有同接口字段，但增强行为不明确（简单委托）
- **low**：仅有同接口持有，无行为增强

---

### 3. Observer（观察者） — behavioral

**结构特征：**

```bash
# 搜索条件1：Spring ApplicationEvent 机制
grep -rn "ApplicationEvent\|@EventListener\|ApplicationEventPublisher" --include="*.java" <target-dir>

# 搜索条件2：事件发布
grep -rn "applicationEventPublisher\.publishEvent\|publishEvent(" --include="*.java" <target-dir>

# 搜索条件3：事件监听
grep -rn "@EventListener\|@TransactionalEventListener" --include="*.java" <target-dir>

# 搜索条件4：自定义事件类
grep -rn "extends ApplicationEvent\|extends.*Event" --include="*.java" <target-dir>
```

代码模式：
```java
// 自定义事件
public class TradeCompletedEvent extends ApplicationEvent {
    private TradeResult result;
    public TradeCompletedEvent(Object source, TradeResult result) {
        super(source);
        this.result = result;
    }
}

// 事件发布者（Subject）
@Service
public class TradeService {
    @Autowired
    private ApplicationEventPublisher publisher;

    public void executeTrade(TradeRequest request) {
        TradeResult result = doTrade(request);
        publisher.publishEvent(new TradeCompletedEvent(this, result));  // 发布事件
    }
}

// 事件监听者（Observer）
@Component
public class TradeNotificationListener {
    @EventListener
    public void onTradeCompleted(TradeCompletedEvent event) {
        // 处理事件：发送通知等
        sendNotification(event.getResult());
    }
}

@Component
public class TradeAuditListener {
    @EventListener
    @Async
    public void onTradeCompleted(TradeCompletedEvent event) {
        // 处理事件：记录审计日志
        auditLog(event.getResult());
    }
}
```

**参与者角色：**
- Subject（Publisher）：发布事件的核心类
- Observer（Listener）：监听并处理事件的类
- Event：事件对象，承载传递的数据

**置信度判断标准：**
- **high**：自定义事件类（`extends ApplicationEvent`）+ `ApplicationEventPublisher.publishEvent()` + `@EventListener` 监听方法 + >=2个监听者
- **medium**：使用 `@EventListener` 但事件是框架内置类型，或仅有1个监听者
- **low**：仅注入了 `ApplicationEventPublisher`，未实际发布事件

---

### 4. Chain of Responsibility（责任链） — behavioral

**结构特征：**

```bash
# 搜索条件1：Handler 接口及其多个实现
grep -rn "interface.*Handler\|interface.*Filter\|interface.*Interceptor" --include="*.java" <target-dir>

# 搜索条件2：持有 next Handler 字段
grep -rn "private.*next\|protected.*next\|this\.next\|this\.successor" --include="*.java" <target-dir>

# 搜索条件3：链式调用 next.handle()
grep -rn "next\.handle\|next\.process\|next\.doFilter\|chain\." --include="*.java" <target-dir>

# 搜索条件4：Handler 列表或有序集合
grep -rn "List.*Handler\|Ordered\|@Order\|getOrder" --include="*.java" <target-dir>
```

代码模式：
```java
// Handler 接口
public interface ValidationHandler {
    void setNext(ValidationHandler next);
    ValidationResult handle(TradeRequest request);
}

// 抽象 Handler 基类
public abstract class AbstractValidationHandler implements ValidationHandler {
    private ValidationHandler next;

    @Override
    public void setNext(ValidationHandler next) {
        this.next = next;
    }

    protected ValidationResult handleNext(TradeRequest request) {
        if (next != null) {
            return next.handle(request);  // 链式调用
        }
        return ValidationResult.success();
    }
}

// 具体 Handler A
@Component
@Order(1)
public class ParameterValidationHandler extends AbstractValidationHandler {
    @Override
    public ValidationResult handle(TradeRequest request) {
        if (!validateParams(request)) {
            return ValidationResult.fail("参数校验失败");
        }
        return handleNext(request);  // 传递给下一个 Handler
    }
}

// 具体 Handler B
@Component
@Order(2)
public class RiskValidationHandler extends AbstractValidationHandler {
    @Override
    public ValidationResult handle(TradeRequest request) {
        if (!checkRisk(request)) {
            return ValidationResult.fail("风控校验失败");
        }
        return handleNext(request);
    }
}
```

**参与者角色：**
- Handler：定义处理接口和后继链的抽象
- ConcreteHandler：具体处理者，决定是否处理及是否传递
- Client：发起链式处理的调用者

**置信度判断标准：**
- **high**：>=2 个 Handler 实现同一接口 + 持有 `next` Handler 字段 + 明确的链式调用（`next.handle()`/`chain.proceed()`）+ `@Order` 或有序集合编排
- **medium**：多个 Handler 实现同一接口 + `@Order` 注解，但链式传递逻辑不明确（可能是循环调用而非链式传递）
- **low**：仅有多个 Handler，无链式结构

---

### 5. State（状态） — behavioral

**结构特征：**

```bash
# 搜索条件1：类名含 State/Status
grep -rn "class.*State\|class.*Status\|enum.*State\|enum.*Status" --include="*.java" <target-dir>

# 搜索条件2：状态枚举或状态字段
grep -rn "enum.*\{.*APPROVED\|enum.*\{.*REJECTED\|enum.*\{.*PENDING" --include="*.java" <target-dir>

# 搜索条件3：状态转换方法
grep -rn "transition\|changeState\|setState\|updateStatus\|nextState" --include="*.java" <target-dir>

# 搜索条件4：基于状态的条件分支
grep -rn "switch.*state\|switch.*status\|state ==\|status ==" --include="*.java" <target-dir>
```

代码模式：
```java
// 状态枚举
public enum OrderState {
    CREATED, SUBMITTED, APPROVED, REJECTED, COMPLETED, CANCELLED;

    public boolean canTransitionTo(OrderState target) {
        // 定义合法的状态转换
        switch (this) {
            case CREATED:    return target == SUBMITTED || target == CANCELLED;
            case SUBMITTED:  return target == APPROVED || target == REJECTED;
            case APPROVED:   return target == COMPLETED || target == CANCELLED;
            default:         return false;
        }
    }
}

// 上下文持有状态
@Entity
public class Order {
    @Enumerated(EnumType.STRING)
    private OrderState state = OrderState.CREATED;

    public void submit() {
        transitionTo(OrderState.SUBMITTED);
    }

    public void approve() {
        transitionTo(OrderState.APPROVED);
    }

    private void transitionTo(OrderState newState) {
        if (!state.canTransitionTo(newState)) {
            throw new IllegalStateException(
                "Cannot transition from " + state + " to " + newState);
        }
        this.state = newState;
    }
}
```

**参与者角色：**
- State：定义状态的枚举或接口
- ConcreteState：具体状态（枚举值或状态实现类）
- Context：持有当前状态并触发状态转换的类

**置信度判断标准：**
- **high**：状态枚举 + 状态转换方法（`canTransitionTo`/`transition`/`nextState`）+ 上下文类持有状态字段 + 合法转换校验逻辑
- **medium**：状态枚举 + 上下文持有状态字段 + `switch` 语句按状态分支，但转换规则散落在多处
- **low**：仅有状态枚举或 `status` 字段，无转换逻辑

---

## 排除规则

以下情况**不算**设计模式，应从结果中排除：

| 误判场景 | 被误判为 | 排除原因 |
|---------|---------|---------|
| 简单的 `if-else` / `switch` 条件分支 | Strategy | 缺少策略接口和多态分发，仅是条件逻辑 |
| 普通的类继承（子类覆写方法） | Template Method | 缺少模板方法（调用抽象步骤的固定流程方法） |
| 简单的 `new Xxx()` 对象创建 | Factory | 缺少工厂封装层，直接创建不算工厂模式 |
| 普通的方法链（如 `sb.append().append()`） | Builder | 缺少 `build()` 终端方法和独立 Builder 结构 |
| 单纯的包装类（仅持有对象并无行为增强） | Decorator | 缺少行为增强（装饰器必须在委托基础上增加新行为） |
| 普通的 setter 注入 | Observer | 缺少事件发布/订阅机制 |
| Spring Bean 默认单例 | Singleton（经典） | 属于框架级行为，不单独列为设计模式 |
| 简单的 DTO/VO 对象转换 | Adapter | 缺少接口适配，仅为数据映射 |

---

## 置信度通用标准

### High（高置信度）
- **命名语义明确**：类名/接口名包含模式关键词（Factory、Strategy、Adapter、Builder 等）
- **结构完整**：模式的所有核心参与者均存在（接口 + 实现 + 使用方）
- **至少 3 个特征匹配**：满足该模式定义中的 3 个或以上结构特征

### Medium（中置信度）
- **结构基本完整**：核心参与者存在但部分缺失（如缺少使用方或编排逻辑）
- **2 个特征匹配**：满足该模式定义中的 2 个结构特征
- 可能在代码演进中成为完整模式

### Low（低置信度）
- **仅有 1 个特征**：可能是巧合，不足以确认
- **命名暗示但结构不匹配**：类名含模式关键词但代码结构不符合
- **建议操作**：在报告中标注为"疑似模式"，由人工确认
