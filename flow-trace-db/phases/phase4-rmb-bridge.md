# Phase 4: RMB 桥接

> 识别 RMB 发送端，匹配接收端，合并为 MERGED_RMB_FLOW。

## 概述

Phase 4 处理 RMB 消息总线调用场景：从 Phase 3 提取的链路中识别 RMB 发送端节点，通过源码扫描提取 Topic 信息，与 Phase 1 识别的 RMB 接收端入口进行匹配，合并为端到端的 MERGED_RMB_FLOW。

## 前置条件

- Phase 1 已完成（入口列表含 RMB Controller 入口）
- Phase 3 已完成（所有入口的 chain 数据已提取）
- 已加载 `bridge-rules.md` 配置文件

## 执行步骤

### 步骤 1: 识别发送端

从 Phase 3 的链路数据中筛选 RMB 发送端节点。

筛选条件：
- `domainInteraction.type = "EXTERNAL"` 且 `protocol = "RMB"`
- 或 `layerType = "RMB_CLIENT"`

### 步骤 2: 提取 Topic 信息

从源码中按优先级提取 Topic：
1. `@RmbClient(topic="xxx")` 注解参数
2. `rmbClientProxy.send("xxx")` 方法参数
3. 常量引用追踪
4. 配置文件查找

### 步骤 3: 匹配接收端

Topic 精确匹配（参照 `bridge-rules.md`），支持正则变换。

| 匹配情况 | 处理 |
|---------|------|
| 1:1 精确匹配 | 生成 MERGED_RMB_FLOW |
| 1:N 广播 | 每个接收端独立 MERGED_RMB_FLOW |
| 0:1 无匹配 | STANDALONE_FLOW，标记 UNMATCHED |

### 步骤 4: 合并 chain

发送端 chain 截止到 RMB_CLIENT → 插入 BRIDGE 虚拟节点 → 接收端 chain 从 RMB_CONTROLLER 开始。

### 步骤 5: 处理未匹配

未匹配的发送端和接收端保留为 STANDALONE_FLOW，标记 `matchingStatus: "UNMATCHED"`。

## 执行

运行 `scripts/phase4_rmb_bridge.py`：

```bash
python3 scripts/phase4_rmb_bridge.py <project_dir> <cache_dir> <cache_dir>/phase1/entries.json
```

## 输出

- `<cache_dir>/phase4/merged-rmb-*.json`：合并后的 MERGED_RMB_FLOW（完整路径参考 prompt.md 目录约定）
- `<cache_dir>/phase4/bridges.json`：桥接索引（完整路径参考 prompt.md 目录约定）
