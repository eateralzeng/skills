#!/usr/bin/env python3
"""
Phase 5: Document Generation
Reads Phase 0-4 JSON files and generates per-table JSON + Markdown documents,
plus a summary document.
"""

import json
import os
import sys
from collections import defaultdict
from typing import Any


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_table_index(phase_data_list: list[dict]) -> dict[str, dict]:
    """Build a unified index keyed by tableName from multiple phases."""
    index: dict[str, dict] = {}
    for phase_data in phase_data_list:
        tables = phase_data.get("tables", [])
        for t in tables:
            name = t.get("tableName", "")
            if name not in index:
                index[name] = {"tableName": name}
    return index


def merge_phase0(index: dict[str, dict], phase0: dict) -> None:
    """Merge discovery (Phase 0) data."""
    for t in phase0.get("tables", []):
        name = t["tableName"]
        if name in index:
            entry = index[name]
            entry["source"] = t.get("source", "")
            entry["mapperNamespaces"] = t.get("mapperNamespaces", [])
            entry["coverage"] = t.get("coverage", "")
            entry["statementCount"] = t.get("statementCount", {})
            entry["inDbSchema"] = t.get("inDbSchema", False)


def merge_phase1(index: dict[str, dict], phase1: dict) -> None:
    """Merge ownership (Phase 1) data."""
    for t in phase1.get("tables", []):
        name = t["tableName"]
        if name in index:
            entry = index[name]
            entry["ownership"] = t.get("ownership")
            entry["ownershipDiffs"] = phase1.get("ownershipDiffs", [])


def merge_phase2(index: dict[str, dict], phase2: dict) -> None:
    """Merge CRUD operations (Phase 2) data."""
    for t in phase2.get("tables", []):
        name = t["tableName"]
        if name in index:
            entry = index[name]
            entry["operations"] = t.get("operations", {})


def merge_phase3(index: dict[str, dict], phase3: dict) -> None:
    """Merge state inference (Phase 3) data."""
    for t in phase3.get("tables", []):
        name = t["tableName"]
        if name in index:
            entry = index[name]
            entry["stateTransitions"] = t.get("stateTransitions", [])


def merge_phase4(index: dict[str, dict], phase4: dict) -> None:
    """Merge flow coverage (Phase 4) data."""
    for t in phase4.get("tables", []):
        name = t["tableName"]
        if name in index:
            entry = index[name]
            entry["flowCoverage"] = t.get("flowCoverage", {})
            entry["ownershipCrossValidation"] = t.get("ownershipCrossValidation", {})


def get_mapper_classes(entry: dict) -> list[str]:
    """Extract short mapper class names from namespaces."""
    namespaces = entry.get("mapperNamespaces", [])
    result = []
    for ns in namespaces:
        parts = ns.split(".")
        result.append(parts[-1] if parts else ns)
    return result


def get_service_classes(entry: dict) -> list[str]:
    """Extract unique service/dao class names from ownership."""
    ownership = entry.get("ownership")
    if not ownership:
        return []
    classes = []
    seen = set()
    for key in ("services", "daos"):
        for item in ownership.get(key, []):
            cn = item.get("className", "")
            if cn and cn not in seen:
                seen.add(cn)
                classes.append(cn)
    return classes


def get_primary_module(entry: dict) -> str:
    """Determine primary module from ownership data."""
    ownership = entry.get("ownership")
    if not ownership:
        return "-"
    modules: dict[str, int] = {}
    for key in ("services", "daos"):
        for item in ownership.get(key, []):
            mod = item.get("module", "")
            if mod:
                modules[mod] = modules.get(mod, 0) + 1
    if not modules:
        return "-"
    return max(modules, key=modules.get)  # type: ignore[arg-type]


def get_flow_count(entry: dict) -> int:
    """Get number of associated flows."""
    fc = entry.get("flowCoverage", {})
    return len(fc.get("flows", []))


def get_coverage_status(entry: dict) -> str:
    """Get flow coverage status."""
    fc = entry.get("flowCoverage", {})
    return fc.get("status", "ORPHAN")


def get_module_ownership(entry: dict) -> list[dict]:
    """Build module ownership table rows."""
    ownership = entry.get("ownership")
    if not ownership:
        return []
    # Collect module -> { class -> set of read/write }
    module_map: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for key in ("services", "daos"):
        for item in ownership.get(key, []):
            mod = item.get("module", "-")
            cn = item.get("className", "-")
            # Determine if this is read or write based on context
            mappers = item.get("mappers", [])
            if mappers:
                module_map[mod][cn].add("读写")
            else:
                via = item.get("via", [])
                if via:
                    module_map[mod][cn].add("读写")
                else:
                    module_map[mod][cn].add("读写")
    result = []
    for mod in sorted(module_map):
        for cn in sorted(module_map[mod]):
            ops = module_map[mod][cn]
            result.append({
                "module": mod,
                "service": cn,
                "operation": "/".join(sorted(ops)),
            })
    return result


def get_flow_operations(entry: dict) -> list[dict]:
    """Build flow association table rows."""
    fc = entry.get("flowCoverage", {})
    flows = fc.get("flows", [])
    if not flows:
        return []
    result = []
    for flow in flows:
        flow_id = flow.get("flowId", flow.get("name", "-"))
        flow_type = flow.get("type", "-")
        ops = flow.get("operations", [])
        result.append({
            "flow": flow_id,
            "type": flow_type,
            "operation": ", ".join(ops) if ops else "-",
        })
    return result


def escape_md(text: str) -> str:
    """Escape special markdown characters in table cells."""
    return text.replace("|", "\\|").replace("\n", " ")


def generate_table_markdown(entry: dict) -> str:
    """Generate markdown document for a single table."""
    table_name = entry["tableName"]
    primary_module = get_primary_module(entry)
    mapper_classes = get_mapper_classes(entry)
    service_classes = get_service_classes(entry)
    flow_count = get_flow_count(entry)
    coverage_status = get_coverage_status(entry)
    source = entry.get("source", "-")

    lines: list[str] = []

    # Header
    lines.append(f"# 表生命周期：{table_name}")
    lines.append("")
    lines.append(f"> 模块：{primary_module}")
    lines.append("> 生成器：data-model-db | 数据源：graph-db + 源码")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 1: Basic info
    lines.append("## 1. 基本信息")
    lines.append("")
    lines.append("| 项目 | 详情 |")
    lines.append("|------|------|")
    lines.append(f"| 表名 | {table_name} |")
    lines.append(f"| 来源 | {source} |")
    mapper_str = escape_md(", ".join(mapper_classes)) if mapper_classes else "-"
    lines.append(f"| Mapper | {mapper_str} |")
    service_str = escape_md(", ".join(service_classes)) if service_classes else "-"
    lines.append(f"| 所属 Service | {service_str} |")
    lines.append(f"| 关联流程 | {flow_count}个 |")
    lines.append(f"| 覆盖状态 | {coverage_status} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 2: CRUD operations
    lines.append("## 2. CRUD 操作")
    lines.append("")
    lines.append("| 操作类型 | 方法数 | 主要方法 |")
    lines.append("|---------|--------|---------|")

    operations = entry.get("operations", {})
    for op_type in ("select", "insert", "update", "delete"):
        op_label = op_type.upper()
        ops = operations.get(op_type, [])
        count = len(ops)
        if count == 0:
            lines.append(f"| {op_label} | 0 | - |")
        else:
            method_names = [o.get("statementId", "?") for o in ops[:5]]
            method_str = escape_md(", ".join(method_names))
            if count > 5:
                method_str += f", ... (共{count}个)"
            lines.append(f"| {op_label} | {count} | {method_str} |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 3: State transitions
    lines.append("## 3. 状态流转")
    lines.append("")

    state_transitions = entry.get("stateTransitions", [])
    if not state_transitions:
        lines.append("无状态流转字段")
    else:
        for st in state_transitions:
            field = st.get("field", "-")
            enum_class = st.get("enumClass", "-")
            match_type = st.get("matchType", "-")
            confidence = st.get("confidence", "-")
            values = st.get("values", [])
            transitions = st.get("transitions", [])

            lines.append(f"字段：{field} ({enum_class})")
            lines.append(f"匹配方式：{match_type}")
            lines.append(f"置信度：{confidence}")
            lines.append("")

            if values:
                # Build a simple text state diagram
                value_names = [v.get("name", "?") for v in values]
                lines.append("```")

                # Try to build transition diagram
                if transitions:
                    # Group transitions by 'from' state
                    from_map: dict[str, list[str]] = defaultdict(list)
                    root_states = []
                    for tr in transitions:
                        from_state = tr.get("from", "")
                        to_state = tr.get("to", "")
                        if not from_state and not to_state:
                            continue
                        if not from_state:
                            root_states.append(to_state)
                        else:
                            from_map[from_state].append(to_state)

                    if from_map or root_states:
                        # Render transitions
                        rendered = set()
                        for from_s, to_list in from_map.items():
                            for idx, to_s in enumerate(to_list):
                                prefix = "     " if idx > 0 else ""
                                connector = "└→ " if idx > 0 else "→ "
                                if idx == 0:
                                    lines.append(f"{from_s} → {to_s}")
                                else:
                                    lines.append(f"     {connector}{to_s}")
                                rendered.add(from_s)
                                rendered.add(to_s)
                        # Add states without transitions
                        remaining = [v for v in value_names if v not in rendered]
                        if remaining:
                            lines.append(f"独立状态: {', '.join(remaining)}")
                    else:
                        lines.append(" → ".join(value_names[:8]))
                        if len(value_names) > 8:
                            lines.append(f"... 共 {len(value_names)} 个状态")
                else:
                    lines.append(" → ".join(value_names[:8]))
                    if len(value_names) > 8:
                        lines.append(f"... 共 {len(value_names)} 个状态")

                lines.append("```")
            lines.append("")

    lines.append("---")
    lines.append("")

    # Section 4: Ownership
    lines.append("## 4. 归属关系")
    lines.append("")
    lines.append("### 模块归属")
    lines.append("")
    lines.append("| 模块 | Service | 操作 |")
    lines.append("|------|---------|------|")

    module_rows = get_module_ownership(entry)
    if module_rows:
        for row in module_rows:
            lines.append(f"| {row['module']} | {row['service']} | {row['operation']} |")
    else:
        lines.append("| - | - | - |")

    lines.append("")
    lines.append("### 关联流程")
    lines.append("")
    lines.append("| 流程 | 类型 | 操作 |")
    lines.append("|------|------|------|")

    flow_rows = get_flow_operations(entry)
    if flow_rows:
        for row in flow_rows:
            lines.append(f"| {row['flow']} | {row['type']} | {row['operation']} |")
    else:
        lines.append("| - | - | - |")

    lines.append("")
    lines.append("---")
    lines.append("")

    # Section 5: Domain hints
    lines.append("## 5. 领域归属提示（需人工审核）")
    lines.append("")
    lines.append("> 以下为自动化提示，不作为确定性结论。")
    lines.append("")

    # State richness
    total_state_values = 0
    total_transition_paths = 0
    for st in state_transitions:
        values = st.get("values", [])
        transitions = st.get("transitions", [])
        total_state_values += len(values)
        total_transition_paths += len(transitions)
    lines.append(f"- 状态流转丰富度：{total_state_values}个状态值，{total_transition_paths}个流转路径")

    # Shared services count
    lines.append(f"- 共享 Service 数量：{len(service_classes)}个 Service 操作此表")

    # Cross-module operations
    cross_modules = set()
    ownership = entry.get("ownership")
    if ownership:
        for key in ("services", "daos"):
            for item in ownership.get(key, []):
                mod = item.get("module", "")
                if mod and mod != primary_module:
                    cn = item.get("className", "")
                    if cn:
                        cross_modules.add(f"{cn}({mod})")
    if cross_modules:
        lines.append(f"- 跨模块操作：{', '.join(sorted(cross_modules))}")
    else:
        lines.append("- 跨模块操作：无")

    # Aggregate root hint
    has_create = bool(operations.get("insert"))
    has_update = bool(operations.get("update"))
    has_complete = False
    for st in state_transitions:
        values = st.get("values", [])
        # Check if there are terminal states (success/failed/etc.)
        for v in values:
            desc = v.get("description", "").lower()
            name = v.get("name", "").lower()
            if any(kw in desc or kw in name for kw in ["成功", "success", "失败", "failed", "完成", "done", "终态"]):
                has_complete = True
                break
        if has_complete:
            break

    if has_create and has_update and has_complete:
        lines.append("- 潜在聚合根特征：有完整生命周期（创建 → 处理 → 终态）")
    else:
        lines.append("- 潜在聚合根特征：无完整生命周期")

    lines.append("")

    return "\n".join(lines)


def generate_summary(index: dict[str, dict]) -> str:
    """Generate table-summary.md content."""
    lines: list[str] = []

    lines.append("# 数据库表生命周期汇总")
    lines.append("")

    # Coverage statistics
    status_counts: dict[str, int] = defaultdict(int)
    for entry in index.values():
        status = get_coverage_status(entry)
        status_counts[status] += 1

    lines.append("## 覆盖度统计")
    lines.append("")
    lines.append("| 状态 | 表数 |")
    lines.append("|------|------|")
    for status in ("COVERED", "PARTIAL", "ORPHAN"):
        lines.append(f"| {status} | {status_counts.get(status, 0)} |")
    lines.append("")

    # Table list
    lines.append("## 表清单")
    lines.append("")
    lines.append("| 表名 | Mapper | Service数 | 流程数 | 状态字段 | 覆盖 |")
    lines.append("|------|--------|----------|--------|---------|------|")

    for name in sorted(index.keys()):
        entry = index[name]
        mappers = get_mapper_classes(entry)
        mapper_str = escape_md(", ".join(mappers[:2]))
        if len(mappers) > 2:
            mapper_str += f" +{len(mappers)-2}"
        services = get_service_classes(entry)
        service_count = len(services)
        flow_count = get_flow_count(entry)
        state_transitions = entry.get("stateTransitions", [])
        state_fields = [st.get("field", "") for st in state_transitions if st.get("field")]
        state_str = escape_md(", ".join(state_fields)) if state_fields else "-"
        coverage = get_coverage_status(entry)
        lines.append(f"| {name} | {mapper_str} | {service_count} | {flow_count} | {state_str} | {coverage} |")

    lines.append("")

    # Domain attribution suggestions
    lines.append("## 领域归属建议")
    lines.append("")

    # Find aggregate root candidates
    aggregate_candidates = []
    multi_service_tables = []
    cross_module_tables = []

    for name, entry in index.items():
        operations = entry.get("operations", {})
        state_transitions = entry.get("stateTransitions", [])
        services = get_service_classes(entry)
        primary_module = get_primary_module(entry)

        # Check aggregate root
        has_create = bool(operations.get("insert"))
        has_update = bool(operations.get("update"))
        has_complete = False
        for st in state_transitions:
            for v in st.get("values", []):
                desc = v.get("description", "").lower()
                vname = v.get("name", "").lower()
                if any(kw in desc or kw in vname for kw in ["成功", "success", "失败", "failed", "完成", "done"]):
                    has_complete = True
                    break
        if has_create and has_update and has_complete:
            aggregate_candidates.append(name)

        # Multi-service
        if len(services) >= 3:
            multi_service_tables.append((name, len(services)))

        # Cross-module
        ownership = entry.get("ownership")
        if ownership:
            modules = set()
            for key in ("services", "daos"):
                for item in ownership.get(key, []):
                    mod = item.get("module", "")
                    if mod:
                        modules.add(mod)
            if len(modules) >= 2:
                cross_module_tables.append((name, sorted(modules)))

    if aggregate_candidates:
        lines.append("### 聚合根候选")
        lines.append(f"以下表具有完整生命周期特征（创建 → 处理 → 终态），可能是领域聚合根：")
        for t in aggregate_candidates:
            lines.append(f"- **{t}**")
        lines.append("")

    if multi_service_tables:
        lines.append("### 高共享表（3+ Service）")
        lines.append("以下表被多个 Service 共同操作，需关注归属边界：")
        for t, count in multi_service_tables:
            lines.append(f"- **{t}**：{count} 个 Service")
        lines.append("")

    if cross_module_tables:
        lines.append("### 跨模块表")
        lines.append("以下表被多个模块操作，建议明确领域归属：")
        for t, mods in cross_module_tables:
            lines.append(f"- **{t}**：{', '.join(mods)}")
        lines.append("")

    lines.append("> 以上建议基于自动化分析，需结合业务语义人工审核确认。")
    lines.append("")

    return "\n".join(lines)


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 phase5_doc_gen.py <cache_dir> <output_dir>", file=sys.stderr)
        sys.exit(1)

    cache_dir = sys.argv[1]
    output_dir = sys.argv[2]

    # Load all phase data
    phase0 = load_json(os.path.join(cache_dir, "phase0-registry.json"))
    phase1 = load_json(os.path.join(cache_dir, "phase1-ownership.json"))
    phase2 = load_json(os.path.join(cache_dir, "phase2-operations.json"))
    phase3 = load_json(os.path.join(cache_dir, "phase3-states.json"))
    phase4 = load_json(os.path.join(cache_dir, "phase4-coverage.json"))

    # Build unified table index
    index = build_table_index([phase0, phase1, phase2, phase3, phase4])

    # Merge all phases
    merge_phase0(index, phase0)
    merge_phase1(index, phase1)
    merge_phase2(index, phase2)
    merge_phase3(index, phase3)
    merge_phase4(index, phase4)

    # Create output directory
    tables_dir = os.path.join(output_dir, "tables")

    # Generate per-table JSON and Markdown, organized by source
    source_counts: dict[str, int] = defaultdict(int)
    for name, entry in sorted(index.items()):
        source = entry.get("source", "UNKNOWN")
        source_dir = os.path.join(tables_dir, source)
        os.makedirs(source_dir, exist_ok=True)

        # JSON
        json_path = os.path.join(source_dir, f"{name}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)

        # Markdown
        md_path = os.path.join(source_dir, f"{name}.md")
        md_content = generate_table_markdown(entry)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        source_counts[source] += 1

    # Generate summary
    os.makedirs(tables_dir, exist_ok=True)
    summary_path = os.path.join(tables_dir, "table-summary.md")
    summary_content = generate_summary(index)
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_content)

    for source, count in sorted(source_counts.items()):
        print(f"[Phase 5]   {source}: {count} tables")
    print(f"[Phase 5] Total: {len(index)} table documents in {tables_dir}")
    print(f"[Phase 5] Summary: {summary_path}")


if __name__ == "__main__":
    main()
