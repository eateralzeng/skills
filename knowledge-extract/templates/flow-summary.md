# {{project_name}} 流程梳理汇总 v{{version}}

> 项目：{{project_name}}（{{project_description}}）
> 扫描日期：{{scan_date}}
> 总流程入口：{{total_flows}} 个
> 版本：v{{version}}（{{version_notes}}）

## 统计信息

| 指标 | 数量 |
|------|------|
| 流程入口总数 | {{total_flows}} |
| **合并 RMB 流程** | **{{merged_count}}** ({{merged_range}}) |
| **独立流程** | **{{standalone_count}}** ({{standalone_range}}) |
| Web 页面入口 | {{web_page_count}} |
| Controller 入口 | {{controller_count}} 个端点（{{controller_class_count}} 个类） |
| RMB 接收端入口 | {{rmb_count}} 个 |
| Job 入口 | {{job_count}} 个 |
| 涉及数据库表 | {{total_tables}}+ |
| 涉及外部系统 | {{external_systems_count}}+ |
| RMB 桥接成功 | {{rmb_bridge_success}} 对 |
| 外部发送端 | ~{{external_sender_count}} 个（{{external_sender_note}}） |

---

## 流程分类

| 分类 | 说明 | 编号范围 | 数量 |
|------|------|---------|------|
| MERGED_RMB_FLOW | 通过 RMB Topic 成功桥接发送端和接收端的完整链路 | 001-099 | {{merged_count}} |
| STANDALONE_FLOW | 独立流程（无 RMB 桥接或发送端为外部系统） | 100+ | {{standalone_count}} |

---

## 合并 RMB 流程（MERGED_RMB_FLOW）

以下流程成功将 @RmbClient 发送端与 @RmbController 接收端通过 Topic 串联为完整链路。

| 序号 | 流程名称 | 发送方模块 | RMB Topic | 接收方模块 | 说明 |
|------|---------|-----------|-----------|-----------|------|
| {{merged_flow_rows}} |

---

## 流程总览

### 合并流程（{{merged_range}}）

| 序号 | 入口名称 | 类型 | 流程分类 | 链路深度 | 涉及表/外部系统 |
|------|---------|------|---------|---------|----------------|
| {{merged_overview_rows}} |

### 独立流程（100+）

#### Job 入口（{{job_range}}）

| 序号 | 入口名称 | 模块 | 功能 | 涉及表/外部系统 |
|------|---------|------|------|----------------|
| {{job_overview_rows}} |

#### RMB 接收端入口（{{rmb_range}}）

| 序号 | 模块 | 数量 | 主要功能 | 涉及数据库表 | 发送方 |
|------|------|------|---------|-------------|--------|
| {{rmb_overview_rows}} |

---

## RMB 桥接统计

### 内部桥接（{{internal_bridge_count}} 对）

| 发送方 | Topic | 接收方 | 模式 |
|--------|-------|--------|------|
| {{internal_bridge_rows}} |

### 外部发送端（{{external_sender_topic}}）

| Topic | 接收方模块 | Handler 数量 | 外部发送方 |
|-------|-----------|-------------|-----------|
| {{external_sender_rows}} |

---

## 数据库操作汇总

| 表名 | SELECT | INSERT | UPDATE | DELETE | 涉及流程 |
|------|--------|--------|--------|--------|---------|
| {{db_summary_rows}} |

---

## 外部系统集成汇总

| 外部系统 | 集成方式 | 涉及模块 | 说明 |
|---------|---------|---------|------|
| {{external_integration_rows}} |

---

## 架构层次说明

```
┌──────────────────────────────────────────────────────────┐
│ 外部入口层                                                │
│ ├── HTTP Controllers ({{controller_desc}})                │
│ └── Scheduled Jobs ({{job_desc}})                         │
├──────────────────────────────────────────────────────────┤
│ 前置层 ({{pre_layer_modules}})                             │
│ ├── 协议转换 (外部协议 ↔ RMB)                              │
│ ├── 文件处理 (扫描/上传/下载/清理)                          │
│ ├── 加解密 ({{crypto_desc}})                               │
│ └── RMB Client (转发至业务层)                              │
│     {{rmb_client_topics}}
├─────────────────── RMB 桥接 ─────────────────────────────┤
│ 业务层 ({{business_layer_modules}})                        │
│ ├── RMB Controller (接收前置层请求)                         │
│ │   {{rmb_controller_sources}}
│ ├── {{business_service_desc}}
│ ├── RMB Client → {{core_systems}}
│ └── DAO/Mapper → Database ({{db_type}})                   │
├──────────────────────────────────────────────────────────┤
│ 数据层 ({{db_type}})                                       │
│ ├── {{table_prefix_1}} ({{table_group_desc_1}})            │
│ ├── {{table_prefix_2}} ({{table_group_desc_2}})            │
│ └── {{table_prefix_3}} ({{table_group_desc_3}})            │
└──────────────────────────────────────────────────────────┘
```

---

## 变更历史

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| {{version}} | {{scan_date}} | {{version_change_desc}} |
| 1.0 | {{initial_date}} | 初始版本 |
