# Endpoint Rules - 终点类型规则

本文件定义流程追踪的终点类型。Skill 运行时会展示当前配置的终点规则，用户可以：
- 选择当前已支持的终点类型
- 在本文件中增加新的终点规则
- 在对话中直接描述自定义终点类型

---

## 默认终点类型

| 类型 | 匹配规则 | domainInteraction |
|------|---------|-------------------|
| DATABASE | 类名含 Mapper/Repository | {type: "DATABASE", operation, table} |
| RMB_EXTERNAL | 类名含 Client/Proxy, 有 @RmbClient 注解 | {type: "EXTERNAL", direction: "OUT", target, protocol: "RMB"} |
| HTTP_EXTERNAL | 调用 RestTemplate / FeignClient / HttpClient | {type: "EXTERNAL", direction: "OUT", target, protocol: "HTTP"} |
| FILE_WRITE | 调用文件写入 API (FileOutputStream / Files.write) | {type: "FILE", operation: "WRITE"} |
| MQ_PUBLISH | 调用 MQ 发送 API (KafkaTemplate / JmsTemplate) | {type: "MQ", direction: "OUT", topic} |

## 自定义终点

在下方添加项目特有的终点规则：

<!-- 示例：
### 自定义终点：Redis 缓存写入
匹配规则：调用 RedisTemplate.opsForValue().set() / StringRedisTemplate.set()
domainInteraction: {type: "CACHE", operation: "WRITE", target: "Redis"}
-->