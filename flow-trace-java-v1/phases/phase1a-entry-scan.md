# Phase 1a: 入口扫描

## 概述

扫描 Java 项目源码，识别所有流程入口点（Controller、RMB 接收端、定时任务）。

## 输入

- 项目源码目录（`<project_dir>`）

## 输出

- `phase1a/entries.json`

## 前置条件

- 项目目录下存在 Java 源码（`src/main/java` 目录或 `.java` 文件）
- `rules/entry-rules.md` 可访问

## 执行步骤

1. 运行脚本：
   ```bash
   python3 <skill_dir>/scripts/phase1a_entry_scan.py <project_dir> <cache_dir> [--rules <rules_path>]
   ```
2. 脚本从 `rules/entry-rules.json` 加载规则配置（默认路径，可通过 `--rules` 覆盖）
3. 自动完成：收集常量 → 按规则扫描各类型入口 → 去噪
4. 输出 `phase1a/entries.json`

## 输出文件格式

```json
{
  "version": "2.0",
  "generator": "flow-trace-java",
  "entries": [
    {
      "id": "controller-001",
      "type": "controller | rmb | job",
      "className": "ClassName",
      "methodName": "methodName",
      "filePath": "relative/path/to/File.java",
      "httpMapping": "PostMapping(/api/xxx) | null",
      "rmbTopic": "topic-name | null",
      "nodeId": "模块名:包名.类名:方法名"
    }
  ],
  "summary": {
    "controller": 0,
    "rmb": 0,
    "job": 0,
    "total": 0
  }
}
```

## 错误处理

- 如果未找到任何入口，输出空 entries 数组，summary.total=0，并发出警告
- 文件路径必须相对于项目根目录
