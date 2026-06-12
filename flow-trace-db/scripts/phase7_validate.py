"""Phase 7: Five-dimension validation

D1: Entry Completeness (unchanged)
D2: Chain Sanity (downgraded - lightweight checks only, no graph.db verification)
D3: Database Coverage (upgraded - external table-list as benchmark)
D4: RMB Bridge Accuracy (unchanged)
D5: Description Quality (new - empty, mechanical, missing context checks)
"""
import json, glob, os, re, argparse
from collections import defaultdict, Counter
from datetime import datetime


def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_chain(cache_dir, eid):
    """Load chain data: prefer phase5 (verified), fall back to phase3 (original)."""
    p5 = os.path.join(cache_dir, "phase5", f"{eid}.json")
    if os.path.exists(p5):
        return load_json(p5)
    return load_json(os.path.join(cache_dir, "phase3", f"{eid}.json"))


def parse_args():
    p = argparse.ArgumentParser(description="Phase 7: Validation")
    p.add_argument("project_dir", help="Java project root")
    p.add_argument("cache_dir", help="Cache root directory (.trace-cache/)")
    p.add_argument("output_dir", help="flow-trace-db output directory")
    p.add_argument("entries_path", help="Entry list JSON")
    p.add_argument("--table-list", help="External table list file (one table name per line)")
    return p.parse_args()


# ── D1: Entry Completeness ──────────────────────────────────────────────

def d1_entry_completeness(project_dir, entries, source_dir):
    issues = []
    total_checks = len(entries)

    controller_annos = ('@RequestMapping', '@GetMapping', '@PostMapping',
                        '@PutMapping', '@DeleteMapping', '@PatchMapping')
    source_entries = {}

    # Scan Controller entries
    for java_file in glob.glob(os.path.join(source_dir, '**/*.java'), recursive=True):
        with open(java_file, errors='replace') as f:
            content = f.read()
        for anno in controller_annos:
            if anno not in content:
                continue
            cls_m = re.search(r'(?:public\s+)?(?:class|interface)\s+(\w+)', content)
            if not cls_m:
                continue
            cls_name = cls_m.group(1)
            if 'Controller' not in cls_name:
                continue
            for m in re.finditer(
                r'@(?:Request|Get|Post|Put|Delete|Patch)Mapping\s*(?:\([^)]*\))?\s*\n\s*(?:public|private|protected)\s+\S+\s+(\w+)\s*\(',
                content
            ):
                method_name = m.group(1)
                key = f"{cls_name}.{method_name}"
                rel_path = java_file.replace(project_dir + "/", "")
                source_entries[key] = {"class": cls_name, "method": method_name, "file": rel_path}

    # Scan RMB handlers
    for java_file in glob.glob(os.path.join(source_dir, '**/*.java'), recursive=True):
        if '/client/' in java_file:
            continue
        with open(java_file, errors='replace') as f:
            content = f.read()
        if '@RmbTopic' not in content:
            continue
        cls_m = re.search(r'(?:public\s+)?(?:class|interface)\s+(\w+)', content)
        if not cls_m:
            continue
        cls_name = cls_m.group(1)
        for m in re.finditer(r'@RmbTopic\s*\([^)]*\)\s*(?:@\w+(?:\([^)]*\))?\s*)*\s*RmbResponse\s+(\w+)\s*\(', content, re.DOTALL):
            method_name = m.group(1)
            key = f"{cls_name}.{method_name}"
            rel_path = java_file.replace(project_dir + "/", "")
            source_entries[key] = {"class": cls_name, "method": method_name, "file": rel_path}

    # Scan Jobs
    for java_file in glob.glob(os.path.join(source_dir, '**/*.java'), recursive=True):
        with open(java_file, errors='replace') as f:
            content = f.read()
        if '@Scheduled' not in content and '@CronQuartzJob' not in content and '@XxlJob' not in content:
            continue
        cls_m = re.search(r'(?:public\s+)?(?:class|interface)\s+(\w+)', content)
        if not cls_m:
            continue
        cls_name = cls_m.group(1)
        for m in re.finditer(r'@(?:Scheduled|CronQuartzJob|XxlJob)\s*(?:\([^)]*\))?\s*\n\s*(?:public|private|protected)\s+\S+\s+(\w+)\s*\(', content):
            method_name = m.group(1)
            key = f"{cls_name}.{method_name}"
            rel_path = java_file.replace(project_dir + "/", "")
            source_entries[key] = {"class": cls_name, "method": method_name, "file": rel_path}

    # Build entry lookup
    entry_lookup = {}
    for e in entries:
        cls = e.get("className", e.get("class", ""))
        mth = e.get("methodName", e.get("method", ""))
        key = f"{cls}.{mth}"
        entry_lookup[key] = e

    # Source → entries (missing)
    for key, info in source_entries.items():
        if key not in entry_lookup:
            issues.append({
                "type": "ENTRY_MISSING",
                "severity": "WARNING",
                "class": info["class"],
                "method": info["method"],
                "file": info.get("file", ""),
                "message": f"源码中发现入口方法 {key} 但 entries 中未包含"
            })

    # Entries → source (phantom)
    for key, e in entry_lookup.items():
        if key not in source_entries:
            fpath = e.get("file", e.get("filePath", ""))
            full_path = os.path.join(project_dir, fpath)
            if not os.path.exists(full_path):
                issues.append({
                    "type": "ENTRY_NOT_FOUND_IN_SOURCE",
                    "severity": "ERROR",
                    "entryId": e.get("id", ""),
                    "class": e.get("className", e.get("class", "")),
                    "method": e.get("methodName", e.get("method", "")),
                    "message": f"入口 {key} 的源码文件不存在: {fpath}"
                })

    total_checks = max(total_checks, len(source_entries))
    errors = sum(1 for i in issues if i["severity"] == "ERROR")
    warnings = sum(1 for i in issues if i["severity"] == "WARNING")
    status = "PASS" if not issues else ("FAIL" if errors > 0 else "PASS_WITH_WARNINGS")

    return {
        "status": status, "totalChecks": total_checks,
        "errors": errors, "warnings": warnings, "infos": 0,
        "issues": issues
    }


# ── D2: Chain Sanity (downgraded - lightweight only) ────────────────────

def d2_chain_sanity(cache_dir, detail_flows):
    """Lightweight chain sanity checks. No graph.db verification."""
    issues = []
    total_checks = 0

    for df in detail_flows:
        eid = df.get("entryId", "")
        entry_class = df.get("entryClass", "")
        entry_method = df.get("entryMethod", "")

        chain_data = load_chain(cache_dir, eid)
        if not chain_data:
            issues.append({
                "type": "CHAIN_FILE_MISSING",
                "severity": "ERROR",
                "entryId": eid,
                "message": f"入口 {eid} 的 chain 文件不存在"
            })
            continue

        chain = chain_data.get("chain", [])
        chain_status = chain_data.get("status", "")

        # Check chain not empty
        total_checks += 1
        if len(chain) <= 1:
            issues.append({
                "type": "CHAIN_EMPTY",
                "severity": "WARNING",
                "entryId": eid,
                "entry": f"{entry_class}.{entry_method}",
                "nodeCount": len(chain),
                "message": f"入口 {eid} chain 仅含 {len(chain)} 个节点，可能未提取到有效调用"
            })

        # Check chain status
        total_checks += 1
        if chain_status in ("TRUNCATED", "PARTIAL"):
            issues.append({
                "type": "CHAIN_INCOMPLETE",
                "severity": "INFO",
                "entryId": eid,
                "status": chain_status,
                "nodeCount": len(chain),
                "message": f"链路状态为 {chain_status}，包含 {len(chain)} 个节点"
            })

        # Check terminal nodes have domainInteraction
        terminals = [n for n in chain if n.get("terminal")]
        for tn in terminals:
            total_checks += 1
            if not tn.get("domainInteraction"):
                issues.append({
                    "type": "TERMINAL_MISSING_DOMAIN_INTERACTION",
                    "severity": "WARNING",
                    "entryId": eid,
                    "node": f"{tn.get('class', '')}.{tn.get('method', '')}",
                    "message": f"终点节点 {tn.get('class', '')}.{tn.get('method', '')} 缺少 domainInteraction"
                })

    errors = sum(1 for i in issues if i["severity"] == "ERROR")
    warnings = sum(1 for i in issues if i["severity"] == "WARNING")
    infos = sum(1 for i in issues if i["severity"] == "INFO")
    status = "PASS" if not issues else ("FAIL" if errors > 0 else "PASS_WITH_WARNINGS")

    return {
        "status": status, "totalChecks": total_checks,
        "errors": errors, "warnings": warnings, "infos": infos,
        "issues": issues
    }


# ── D3: Database Coverage (upgraded - external table-list) ──────────────

def d3_database_coverage(detail_flows, cache_dir, table_list_path=None):
    """Check database table coverage. Uses external table-list if provided."""
    issues = []
    total_checks = 0

    # Collect tables from domainInteractions in chain files
    covered_tables = set()
    table_sources = {}  # table -> list of {entryId, node, operation}

    for df in detail_flows:
        eid = df.get("entryId", "")
        chain_data = load_chain(cache_dir, eid)
        if not chain_data:
            continue

        for node in chain_data.get("chain", []):
            di = node.get("domainInteraction")
            if not di:
                continue
            if di.get("type") != "DATABASE":
                continue
            table = di.get("table", "")
            if table and table != "—":
                covered_tables.add(table)
                if table not in table_sources:
                    table_sources[table] = []
                table_sources[table].append({
                    "entryId": eid,
                    "node": f"{node.get('class', '')}.{node.get('method', '')}",
                    "operation": di.get("operation", ""),
                })

    # Load benchmark table list
    benchmark_tables = set()
    table_comments = {}

    if table_list_path and os.path.exists(table_list_path):
        with open(table_list_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    benchmark_tables.add(line)
    else:
        # Fallback to db-schema.json if no table-list
        db_schema = load_json(os.path.join(cache_dir, "phase2", "db-schema.json"))
        if db_schema:
            for t in db_schema.get("tables", []):
                tname = t.get("tableName", t.get("name", ""))
                if tname:
                    benchmark_tables.add(tname)
                    table_comments[tname] = t.get("comment", t.get("description", ""))

    total_checks = len(benchmark_tables) if benchmark_tables else len(covered_tables)

    if benchmark_tables:
        # Tables in benchmark but not covered
        uncovered = benchmark_tables - covered_tables
        for t in sorted(uncovered):
            issues.append({
                "type": "TABLE_NOT_COVERED",
                "severity": "WARNING",
                "table": t,
                "comment": table_comments.get(t, ""),
                "message": f"基准表清单中的表 {t} 未被任何流程引用"
            })

        # Tables in flows but not in benchmark
        extra = covered_tables - benchmark_tables
        for t in sorted(extra):
            issues.append({
                "type": "TABLE_NOT_IN_BENCHMARK",
                "severity": "INFO",
                "table": t,
                "message": f"流程引用的表 {t} 不在基准表清单中（可能是推断的表名）"
            })
    else:
        # No benchmark available - just report what we found
        issues.append({
            "type": "NO_BENCHMARK",
            "severity": "INFO",
            "message": "未提供基准表清单(--table-list)，也无法从 db-schema.json 加载，跳过覆盖度校验"
        })

    errors = sum(1 for i in issues if i["severity"] == "ERROR")
    warnings = sum(1 for i in issues if i["severity"] == "WARNING")
    infos = sum(1 for i in issues if i["severity"] == "INFO")
    status = "PASS" if not issues else ("FAIL" if errors > 0 else "PASS_WITH_WARNINGS")

    return {
        "status": status, "totalChecks": total_checks,
        "errors": errors, "warnings": warnings, "infos": infos,
        "issues": issues,
        "stats": {
            "benchmarkTables": len(benchmark_tables),
            "coveredTables": len(covered_tables & benchmark_tables) if benchmark_tables else len(covered_tables),
            "coverageRate": f"{len(covered_tables & benchmark_tables) / len(benchmark_tables) * 100:.1f}%" if benchmark_tables else "N/A",
        }
    }


# ── D4: RMB Bridge Accuracy (unchanged) ─────────────────────────────────

def d4_rmb_bridge_accuracy(bridges_data, cache_dir, source_dir):
    issues = []
    total_checks = 0

    if not bridges_data:
        return {"status": "PASS", "totalChecks": 0, "errors": 0,
                "warnings": 0, "infos": 0, "issues": []}

    bridges = bridges_data.get("bridges", [])

    for b in bridges:
        total_checks += 1
        status = b.get("matchingStatus", "")

        if status == "MATCHED":
            merged_id = b.get("mergedFlowId", "")
            if merged_id:
                merged_path = os.path.join(cache_dir, "phase4", f"{merged_id}.json")
                merged = load_json(merged_path)
                if not merged:
                    issues.append({
                        "type": "BRIDGE_CHAIN_BROKEN",
                        "severity": "ERROR",
                        "topic": b.get("topic", ""),
                        "mergedId": merged_id,
                        "message": f"已匹配的桥接 {merged_id} 的 merged flow 文件不存在"
                    })
                else:
                    chain = merged.get("chain", [])
                    bridge_nodes = [n for n in chain if n.get("layerType") == "BRIDGE"]
                    if not bridge_nodes:
                        issues.append({
                            "type": "BRIDGE_CHAIN_BROKEN",
                            "severity": "WARNING",
                            "topic": b.get("topic", ""),
                            "mergedId": merged_id,
                            "message": f"merged flow {merged_id} 中无 BRIDGE 节点"
                        })
                    recv_nodes = [n for n in chain if n.get("layerType") in ("RMB_CONTROLLER", "SERVICE", "MAPPER")]
                    if len(recv_nodes) < 2:
                        issues.append({
                            "type": "BRIDGE_RECEIVER_SHALLOW",
                            "severity": "INFO",
                            "topic": b.get("topic", ""),
                            "mergedId": merged_id,
                            "recvNodeCount": len(recv_nodes),
                            "message": f"接收端链路较浅，仅 {len(recv_nodes)} 个节点"
                        })

        elif status == "UNMATCHED":
            topic = b.get("topic", "")
            is_fallback_topic = topic and topic[0].islower() and '-' not in topic and '.' not in topic
            if is_fallback_topic:
                continue

            found_in_source = False
            if topic:
                for handler_file in glob.glob(os.path.join(source_dir, '**/handler/**/*.java'), recursive=True):
                    with open(handler_file, errors='replace') as f:
                        content = f.read()
                    for m in re.finditer(r'@RmbTopic\s*\([^)]+\)', content):
                        if topic in m.group(0):
                            found_in_source = True
                            break
                    if found_in_source:
                        break

            if found_in_source:
                issues.append({
                    "type": "BRIDGE_MATCH_MISSED",
                    "severity": "WARNING",
                    "topic": topic,
                    "senderEntryId": b.get("senderEntryId", ""),
                    "message": f"UNMATCHED 发送端 topic={topic} 但源码中找到对应接收端"
                })

    errors = sum(1 for i in issues if i["severity"] == "ERROR")
    warnings = sum(1 for i in issues if i["severity"] == "WARNING")
    infos = sum(1 for i in issues if i["severity"] == "INFO")
    status = "PASS" if not issues else ("FAIL" if errors > 0 else "PASS_WITH_WARNINGS")

    return {
        "status": status, "totalChecks": total_checks,
        "errors": errors, "warnings": warnings, "infos": infos,
        "issues": issues
    }


# ── D5: Description Quality (new) ───────────────────────────────────────

# Mechanical translation patterns: method name segments that indicate direct translation
_MECHANICAL_PATTERNS = [
    re.compile(r'^(获取|设置|得到|执行|处理|调用|查询|插入|更新|删除|添加|移除|检查|判断|返回)[A-Z]'),
]


def _is_mechanical_translation(description, method_name):
    """Check if description is just a mechanical translation of method name."""
    if not description or not method_name:
        return False

    # Extract Chinese segments from method name via common Java→Chinese patterns
    # e.g., getAbsolutePath → "获取AbsolutePath"
    # If description starts with verb + raw English segment from method name, it's mechanical
    desc = description.strip()

    # Check: description is too similar to just "verb + method_name without get/set"
    base_name = method_name
    for prefix in ('get', 'set', 'is', 'has', 'do'):
        if method_name.startswith(prefix) and len(method_name) > len(prefix):
            base_name = method_name[len(prefix):]
            break

    # "获取" + capitalized base_name → mechanical
    mechanical_cn = f"获取{base_name}"
    mechanical_cn2 = f"获取{base_name[0].upper()}{base_name[1:]}" if base_name else ""
    if desc == mechanical_cn or desc == mechanical_cn2:
        return True

    # "设置" pattern
    mechanical_set = f"设置{base_name}"
    mechanical_set2 = f"设置{base_name[0].upper()}{base_name[1:]}" if base_name else ""
    if desc == mechanical_set or desc == mechanical_set2:
        return True

    # Description is just the method name itself (camelCase)
    if desc.replace(" ", "") == method_name:
        return True

    # Very short description with only Chinese verb + English
    if len(desc) < 15 and re.match(r'^[一-鿿]{1,2}[A-Z]', desc):
        return True

    return False


def d5_description_quality(cache_dir, detail_flows):
    """Check description quality across all chain nodes."""
    issues = []
    total_checks = 0

    for df in detail_flows:
        eid = df.get("entryId", "")
        chain_data = load_chain(cache_dir, eid)
        if not chain_data:
            continue

        chain = chain_data.get("chain", [])
        for node in chain:
            desc = node.get("description", "")
            cls = node.get("class", "")
            mth = node.get("method", "")
            terminal = node.get("terminal", False)
            di = node.get("domainInteraction")

            total_checks += 1

            # Check 1: empty description
            if not desc or not desc.strip():
                issues.append({
                    "type": "DESCRIPTION_EMPTY",
                    "severity": "WARNING",
                    "entryId": eid,
                    "node": f"{cls}.{mth}",
                    "layer": node.get("layer", 0),
                    "message": f"节点 {cls}.{mth} 的 description 为空"
                })
                continue

            # Check 2: mechanical translation
            if _is_mechanical_translation(desc, mth):
                issues.append({
                    "type": "DESCRIPTION_MECHANICAL",
                    "severity": "WARNING",
                    "entryId": eid,
                    "node": f"{cls}.{mth}",
                    "description": desc,
                    "message": f"节点 {cls}.{mth} 的描述可能是方法名的机械翻译: \"{desc}\""
                })

            # Check 3: terminal node description should reference table/Topic
            if terminal and di:
                di_type = di.get("type", "")
                table = di.get("table", "")
                target = di.get("target", "")
                has_reference = False

                if di_type == "DATABASE" and table:
                    # Description should mention the table name
                    if table.lower() in desc.lower():
                        has_reference = True
                elif di_type == "EXTERNAL" and target:
                    # Description should mention the target/topic
                    if target.lower() in desc.lower():
                        has_reference = True

                if not has_reference and (table or target):
                    ref_info = f"表={table}" if table else f"Topic={target}"
                    issues.append({
                        "type": "TERMINAL_DESCRIPTION_MISSING_CONTEXT",
                        "severity": "INFO",
                        "entryId": eid,
                        "node": f"{cls}.{mth}",
                        "description": desc,
                        "refInfo": ref_info,
                        "message": f"终点节点 {cls}.{mth} 的描述未引用{ref_info}: \"{desc}\""
                    })

    errors = sum(1 for i in issues if i["severity"] == "ERROR")
    warnings = sum(1 for i in issues if i["severity"] == "WARNING")
    infos = sum(1 for i in issues if i["severity"] == "INFO")
    status = "PASS" if not issues else ("FAIL" if errors > 0 else "PASS_WITH_WARNINGS")

    return {
        "status": status, "totalChecks": total_checks,
        "errors": errors, "warnings": warnings, "infos": infos,
        "issues": issues
    }


# ── Main ────────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    project_dir = args.project_dir
    cache_dir = args.cache_dir
    output_dir = args.output_dir
    entries_path = args.entries_path
    table_list_path = args.table_list
    source_dir = project_dir

    print("Phase 7: Validation")
    print("=" * 50)

    # Load data
    entries_raw = load_json(entries_path)
    entries = entries_raw.get("entries", entries_raw) if isinstance(entries_raw, dict) else entries_raw
    detail = load_json(os.path.join(output_dir, "flow-detail.json"))
    detail_flows = detail.get("flows", []) if detail else []
    bridges_data = load_json(os.path.join(cache_dir, "phase4", "bridges.json"))

    now = datetime.now().strftime("%Y-%m-%d")

    # D1
    print("\n[D1] Entry Completeness...")
    d1 = d1_entry_completeness(project_dir, entries, source_dir)
    print(f"  Status: {d1['status']}, Checks: {d1['totalChecks']}, "
          f"Errors: {d1['errors']}, Warnings: {d1['warnings']}")

    # D2
    print("\n[D2] Chain Sanity...")
    d2 = d2_chain_sanity(cache_dir, detail_flows)
    print(f"  Status: {d2['status']}, Checks: {d2['totalChecks']}, "
          f"Errors: {d2['errors']}, Warnings: {d2['warnings']}, Infos: {d2['infos']}")

    # D3
    print("\n[D3] Database Coverage...")
    d3 = d3_database_coverage(detail_flows, cache_dir, table_list_path)
    print(f"  Status: {d3['status']}, Checks: {d3['totalChecks']}, "
          f"Errors: {d3['errors']}, Warnings: {d3['warnings']}, Infos: {d3['infos']}")
    if "stats" in d3:
        print(f"  Stats: {d3['stats']}")

    # D4
    print("\n[D4] RMB Bridge Accuracy...")
    d4 = d4_rmb_bridge_accuracy(bridges_data, cache_dir, source_dir)
    print(f"  Status: {d4['status']}, Checks: {d4['totalChecks']}, "
          f"Errors: {d4['errors']}, Warnings: {d4['warnings']}, Infos: {d4['infos']}")

    # D5
    print("\n[D5] Description Quality...")
    d5 = d5_description_quality(cache_dir, detail_flows)
    print(f"  Status: {d5['status']}, Checks: {d5['totalChecks']}, "
          f"Errors: {d5['errors']}, Warnings: {d5['warnings']}, Infos: {d5['infos']}")

    # Aggregate
    all_issues = d1["issues"] + d2["issues"] + d3["issues"] + d4["issues"] + d5["issues"]
    total_errors = sum(i["severity"] == "ERROR" for i in all_issues)
    total_warnings = sum(i["severity"] == "WARNING" for i in all_issues)
    total_infos = sum(i["severity"] == "INFO" for i in all_issues)

    if total_errors > 0:
        overall = "FAIL"
    elif total_warnings > 0:
        overall = "PASS_WITH_WARNINGS"
    else:
        overall = "PASS"

    # Build report JSON
    report = {
        "version": "2.0",
        "generator": "flow-trace-db",
        "generateDate": now,
        "overallStatus": overall,
        "summary": {
            "totalIssues": len(all_issues),
            "errors": total_errors,
            "warnings": total_warnings,
            "infos": total_infos,
        },
        "dimensions": {
            "D1_entryCompleteness": d1,
            "D2_chainSanity": d2,
            "D3_databaseCoverage": d3,
            "D4_rmbBridgeAccuracy": d4,
            "D5_descriptionQuality": d5,
        }
    }
    report_path = os.path.join(output_dir, "validate-report.json")
    save_json(report_path, report)
    print(f"\n  Report: {report_path}")

    # Build report MD
    md = [
        "# 校验报告\n",
        f"> 生成工具: flow-trace-db",
        f"> 生成日期: {now}",
        f"> 总体状态: **{overall}**\n",
        "## 概览\n",
        "| 维度 | 状态 | 检查数 | 错误 | 警告 | 信息 |",
        "|------|------|--------|------|------|------|",
        f"| D1 入口完备性 | {d1['status']} | {d1['totalChecks']} | {d1['errors']} | {d1['warnings']} | {d1['infos']} |",
        f"| D2 链路合理性 | {d2['status']} | {d2['totalChecks']} | {d2['errors']} | {d2['warnings']} | {d2['infos']} |",
        f"| D3 数据库表覆盖 | {d3['status']} | {d3['totalChecks']} | {d3['errors']} | {d3['warnings']} | {d3['infos']} |",
        f"| D4 RMB 桥接准确性 | {d4['status']} | {d4['totalChecks']} | {d4['errors']} | {d4['warnings']} | {d4['infos']} |",
        f"| D5 业务描述质量 | {d5['status']} | {d5['totalChecks']} | {d5['errors']} | {d5['warnings']} | {d5['infos']} |",
        "",
    ]

    # D3 coverage stats
    if "stats" in d3 and d3["stats"].get("coverageRate", "N/A") != "N/A":
        md.append("### D3 覆盖统计\n")
        stats = d3["stats"]
        md.append(f"- 基准表数: {stats['benchmarkTables']}")
        md.append(f"- 已覆盖表: {stats['coveredTables']}")
        md.append(f"- 覆盖率: {stats['coverageRate']}")
        md.append("")

    # D1 details
    d1_issues = [i for i in d1["issues"] if i["severity"] != "INFO"]
    if d1_issues:
        md.append("## D1: 入口完备性\n")
        missing = [i for i in d1_issues if i["type"] == "ENTRY_MISSING"]
        if missing:
            md.append("### 遗漏的入口\n")
            md.append("| 类名 | 方法 | 文件 | 说明 |")
            md.append("|------|------|------|------|")
            for i in missing:
                md.append(f"| {i['class']} | {i['method']} | `{i.get('file', '')}` | {i['message']} |")
            md.append("")
        not_found = [i for i in d1_issues if i["type"] == "ENTRY_NOT_FOUND_IN_SOURCE"]
        if not_found:
            md.append("### 不存在的入口\n")
            md.append("| ID | 类名 | 方法 | 说明 |")
            md.append("|----|------|------|------|")
            for i in not_found:
                md.append(f"| {i.get('entryId', '')} | {i['class']} | {i['method']} | {i['message']} |")
            md.append("")

    # D2 details
    d2_issues = [i for i in d2["issues"] if i["severity"] != "INFO"]
    if d2_issues:
        md.append("## D2: 链路合理性\n")
        empty_chains = [i for i in d2_issues if i["type"] == "CHAIN_EMPTY"]
        if empty_chains:
            md.append("### 空 chain\n")
            md.append("| 入口ID | 入口方法 | 节点数 | 说明 |")
            md.append("|--------|---------|--------|------|")
            for i in empty_chains:
                md.append(f"| {i['entryId']} | {i.get('entry', '')} | {i.get('nodeCount', 0)} | {i['message']} |")
            md.append("")
        missing_di = [i for i in d2_issues if i["type"] == "TERMINAL_MISSING_DOMAIN_INTERACTION"]
        if missing_di:
            md.append("### 终点缺少 domainInteraction\n")
            md.append("| 入口ID | 节点 | 说明 |")
            md.append("|--------|------|------|")
            for i in missing_di:
                md.append(f"| {i['entryId']} | {i['node']} | {i['message']} |")
            md.append("")

    # D3 details
    d3_issues = [i for i in d3["issues"] if i["severity"] != "INFO"]
    if d3_issues:
        md.append("## D3: 数据库表覆盖\n")
        uncovered = [i for i in d3_issues if i["type"] == "TABLE_NOT_COVERED"]
        if uncovered:
            md.append("### 未被流程覆盖的表\n")
            md.append("| 表名 | 注释 | 说明 |")
            md.append("|------|------|------|")
            for i in uncovered:
                md.append(f"| `{i['table']}` | {i.get('comment', '')} | {i['message']} |")
            md.append("")

    # D4 details
    d4_issues = [i for i in d4["issues"] if i["severity"] != "INFO"]
    if d4_issues:
        md.append("## D4: RMB 桥接准确性\n")
        md.append("| 类型 | 严重性 | Topic | 说明 |")
        md.append("|------|--------|-------|------|")
        for i in d4_issues:
            md.append(f"| {i['type']} | {i['severity']} | {i.get('topic', '')} | {i['message']} |")
        md.append("")

    # D5 details
    d5_issues = [i for i in d5["issues"] if i["severity"] != "INFO"]
    if d5_issues:
        md.append("## D5: 业务描述质量\n")
        empty_desc = [i for i in d5_issues if i["type"] == "DESCRIPTION_EMPTY"]
        if empty_desc:
            md.append("### 空描述\n")
            md.append("| 入口ID | 节点 | 层级 | 说明 |")
            md.append("|--------|------|------|------|")
            for i in empty_desc:
                md.append(f"| {i['entryId']} | {i['node']} | {i.get('layer', '')} | {i['message']} |")
            md.append("")
        mechanical = [i for i in d5_issues if i["type"] == "DESCRIPTION_MECHANICAL"]
        if mechanical:
            md.append("### 机械翻译\n")
            md.append("| 入口ID | 节点 | 描述 | 说明 |")
            md.append("|--------|------|------|------|")
            for i in mechanical:
                md.append(f"| {i['entryId']} | {i['node']} | {i['description']} | {i['message']} |")
            md.append("")
        missing_ctx = [i for i in d5_issues if i["type"] == "TERMINAL_DESCRIPTION_MISSING_CONTEXT"]
        if missing_ctx:
            md.append("### 终点描述缺少上下文\n")
            md.append("| 入口ID | 节点 | 引用信息 | 说明 |")
            md.append("|--------|------|---------|------|")
            for i in missing_ctx:
                md.append(f"| {i['entryId']} | {i['node']} | {i.get('refInfo', '')} | {i['message']} |")
            md.append("")

    # Summary
    md.append("## 总结\n")
    md.append(f"- 总检查项: {d1['totalChecks'] + d2['totalChecks'] + d3['totalChecks'] + d4['totalChecks'] + d5['totalChecks']}")
    md.append(f"- 错误: {total_errors}")
    md.append(f"- 警告: {total_warnings}")
    md.append(f"- 信息: {total_infos}")
    if total_errors == 0 and total_warnings == 0:
        md.append("\n所有维度校验通过，无问题。")

    md_path = os.path.join(output_dir, "validate-report.md")
    with open(md_path, 'w') as f:
        f.write('\n'.join(md))
    print(f"  Report (MD): {md_path}")

    print(f"\nPhase 7 Complete: {overall}")
    print(f"  Total issues: {len(all_issues)} (E:{total_errors} W:{total_warnings} I:{total_infos})")


if __name__ == '__main__':
    main()
