"""Phase 6: Document Generation for flow-trace-java

Generates Markdown flow documents from semantics-annotated call trees.
Reads from phase5/ (semantics), falls back to phase4/ (bridged), then phase3/ (pruned).

Output: flows/**/*.md, flow-detail.json, flow-summary.json, flow-data-lineage.json
"""
import json, os, argparse
from datetime import datetime
from collections import defaultdict


def parse_args():
    p = argparse.ArgumentParser(description="Phase 6: Document Generation")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    p.add_argument("--output-dir", required=True, help="Output directory for docs")
    p.add_argument("--entries", required=True, help="Path to entries.json")
    p.add_argument("--template", help="Path to flow-template.md (currently unused)")
    return p.parse_args()


def _load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _get_module(filepath):
    if not filepath:
        return ""
    return filepath.split('/')[0] if '/' in filepath else ""


def _extract_package(filepath):
    if not filepath:
        return ""
    marker = "src/main/java/"
    idx = filepath.find(marker)
    if idx < 0:
        return ""
    pkg = filepath[idx + len(marker):]
    pkg = pkg.rsplit('/', 1)[0] if '/' in pkg else pkg
    return pkg.replace('/', '.')


def _di_marker(node):
    """Generate terminal marker: [读]/[写]/[删]/[RMB外调]/[多态分发]"""
    et = node.get('endpointType', '').upper()
    if et == 'DISPATCH':
        impl_count = node.get('dispatchImpl', '').count(',') + 1 if node.get('dispatchImpl') else 0
        if impl_count > 0:
            return f" [多态分发 - {impl_count}个实现类]"
        return " [多态分发]"

    di = node.get('domainInteraction', {})
    if not di:
        # Check for DISPATCH_IMPL
        if node.get('callType') == 'DISPATCH_IMPL':
            impl = node.get('dispatchImpl', '')
            return f" ({impl})" if impl else ""
        return ""
    dtype = di.get('type', '').upper()
    if dtype == 'DATABASE':
        op = di.get('operation', '').upper()
        if op in ('INSERT', 'UPDATE'):
            return " [写]"
        elif op == 'DELETE':
            return " [删]"
        elif op == 'SELECT':
            return " [读]"
        return " [DB]"
    elif dtype == 'EXTERNAL':
        direction = di.get('direction', '').upper()
        if direction == 'OUT':
            return " [RMB外调]"
        elif direction == 'IN':
            return " [RMB接收]"
        return " [外调]"
    return ""


def load_flow_data(cache_dir, entry_id):
    """Load flow data: prefer phase5 (semantics), then phase4 (bridged), then phase3 (pruned)."""
    for phase in ("phase5", "phase4", "phase3"):
        for suffix in (f"{entry_id}-semantics.json", f"merged-rmb-{entry_id}-*.json",
                       f"{entry_id}.json", f"{entry_id}-pruned.json"):
            if '*' in suffix:
                import glob
                matches = glob.glob(os.path.join(cache_dir, phase, suffix))
                if matches:
                    return _load_json(matches[0])
            else:
                path = os.path.join(cache_dir, phase, suffix)
                if os.path.exists(path):
                    return _load_json(path)
    return None


def build_by_parent(chain):
    by_parent = defaultdict(list)
    for n in chain[1:]:
        pid = n.get('parentId', '')
        if pid:
            by_parent[pid].append(n)
    return by_parent


def render_call_chain(chain):
    """Render indented call chain tree."""
    if not chain:
        return "", [], []

    lines = []
    counter = [0]
    all_nodes = []
    db_ops = []
    ext_calls = []

    by_parent = build_by_parent(chain)

    def process_node(node, prefix="", is_last=True):
        counter[0] += 1
        idx = counter[0]
        cls = node.get('class', '?')
        method = node.get('method', '?')
        desc = node.get('description', '')
        marker = _di_marker(node)
        mod = _get_module(node.get('filePath', ''))
        mod_tag = f"  [{mod}]" if mod else ""

        di = node.get('domainInteraction', {})
        db_extra = ""
        if di and di.get('type', '').upper() == 'DATABASE':
            op = di.get('operation', '')
            table = di.get('table', '')
            if op and table:
                db_extra = f" — {op} {table}"

        n_info = {
            'idx': idx, 'class': cls, 'method': method, 'module': mod,
            'filePath': node.get('filePath', ''), 'description': desc,
            'package': node.get('package', _extract_package(node.get('filePath', ''))),
            'domainInteraction': di if di else None,
        }
        all_nodes.append(n_info)

        # Track db ops and external calls
        if di and isinstance(di, dict):
            dtype = di.get('type', '').upper()
            if dtype == 'DATABASE':
                db_ops.append({
                    'module': mod, 'dao': cls, 'operation': di.get('operation', '').upper(),
                    'table': di.get('table', ''), 'description': desc,
                })
            elif dtype == 'EXTERNAL':
                ext_calls.append({
                    'method': method, 'target': di.get('target', ''),
                    'description': desc or method,
                })

        connector = "└──" if is_last else "├──"
        lines.append(f"{prefix}{connector} [{idx}] {cls}.{method}(){marker}{mod_tag}")

        detail = desc + db_extra
        if detail:
            ext = "    " if is_last else "│   "
            lines.append(f"{prefix}{ext}└── {detail}")

        nid = node.get('nodeId', '')
        children = by_parent.get(nid, [])
        if children:
            child_prefix = prefix + ("    " if is_last else "│   ")
            for i, child in enumerate(children):
                process_node(child, child_prefix, i == len(children) - 1)

    entry = chain[0]
    cls = entry.get('class', '?')
    method = entry.get('method', '?')
    lines.append(f"[入口] {cls}.{method}()")

    nid = entry.get('nodeId', '')
    children = by_parent.get(nid, [])
    for i, child in enumerate(children):
        process_node(child, "  ", i == len(children) - 1)

    return '\n'.join(lines), all_nodes, db_ops, ext_calls


def generate_business_overview(chain, db_ops, ext_calls):
    """Generate business overview section."""
    lines = []
    steps = [n.get('description', '') for n in chain if n.get('description')]
    steps = list(dict.fromkeys(steps))  # dedupe preserving order

    if steps:
        lines.append("主要步骤：")
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    if db_ops:
        lines.append("### 数据操作")
        lines.append("")
        lines.append("| 操作 | 表 | 说明 |")
        lines.append("|------|-----|------|")
        for op in db_ops:
            op_label = {'SELECT': '读', 'INSERT': '写', 'UPDATE': '写', 'DELETE': '删'}.get(op['operation'], op['operation'])
            lines.append(f"| {op_label} | {op['table']} | {op['description']} |")
        lines.append("")

    if ext_calls:
        lines.append("### 外部调用")
        lines.append("")
        lines.append("| 调用 | 目标 | 说明 |")
        lines.append("|------|------|------|")
        for ec in ext_calls:
            lines.append(f"| {ec['method']} | {ec['target']} | {ec['description']} |")
        lines.append("")

    return '\n'.join(lines)


def generate_dispatch_tables(chain, cache_dir):
    """Generate dispatch routing tables for DISPATCH nodes in chain."""
    tables = []
    dispatch_nodes = [n for n in chain if n.get('endpointType') == 'DISPATCH' and n.get('patternRef')]

    for dn in dispatch_nodes:
        pattern_ref = dn['patternRef']
        summary_path = os.path.join(cache_dir, "phase2b", f"dispatch-summary-{pattern_ref}.json")
        if not os.path.exists(summary_path):
            continue

        summary = _load_json(summary_path)
        interface = summary.get("interface", pattern_ref)
        results = summary.get("results", [])

        table_lines = []
        table_lines.append(f"### 分发路由：{pattern_ref}")
        table_lines.append("")
        table_lines.append(f"接口：`{interface}`")
        table_lines.append(f"实现类数量：{len(results)}")
        table_lines.append("")
        table_lines.append("| 路由条件 | 实现类 | 涉及的数据库操作 |")
        table_lines.append("|---------|--------|-----------------|")

        for r in results:
            impl_name = r.get("shortName", r.get("class", "").rsplit(".", 1)[-1])
            condition = r.get("condition", "unknown")
            db_endpoints = [ep for ep in r.get("endpoints", []) if ep.get("type") == "DATABASE"]
            if db_endpoints:
                db_str = ", ".join(
                    f"{ep.get('table', '?')}.{ep.get('operation', '?')}"
                    for ep in db_endpoints
                )
            else:
                db_str = "—"
            table_lines.append(f"| {condition} | {impl_name} | {db_str} |")

        table_lines.append("")
        tables.append('\n'.join(table_lines))

    return tables


def generate_flow_md(entry, flow_data, chain, cache_dir=None):
    """Generate complete Markdown document for a flow."""
    if not chain:
        return None, [], []

    entry_node = chain[0]
    entry_class = entry_node.get('class', '?')
    entry_method = entry_node.get('method', '?')
    entry_desc = entry_node.get('description', '')
    entry_type = entry.get('type', 'unknown')

    call_chain_text, all_nodes, db_ops, ext_calls = render_call_chain(chain)
    biz_overview = generate_business_overview(chain, db_ops, ext_calls)

    lines = []
    lines.append(f"# {entry_class}.{entry_method}()")
    lines.append("")
    if entry_desc:
        lines.append(f"> {entry_desc}")
    lines.append("")

    lines.append("## 1. 流程业务概述")
    lines.append("")
    lines.append(biz_overview)

    lines.append("## 2. 完整调用链路")
    lines.append("")
    lines.append("```")
    lines.append(call_chain_text)
    lines.append("```")
    lines.append("")

    section_num = 3

    # Dispatch routing tables
    if cache_dir:
        dispatch_tables = generate_dispatch_tables(chain, cache_dir)
        if dispatch_tables:
            lines.append(f"## {section_num}. 分发路由详情")
            lines.append("")
            for table in dispatch_tables:
                lines.append(table)
            section_num += 1

    # RMB bridge section
    rmb_bridge = flow_data.get('rmbBridge')
    if rmb_bridge and rmb_bridge.get('matchingStatus') == 'MATCHED':
        lines.append(f"## {section_num}. RMB 桥接")
        lines.append("")
        lines.append(f"- Topic: {rmb_bridge.get('topic', '')}")
        lines.append(f"- 模式: {rmb_bridge.get('topicMode', '')}")
        lines.append(f"- 发送端: {rmb_bridge.get('senderHandlerId', '')}")
        lines.append(f"- 接收端: {rmb_bridge.get('receiverHandlerId', '')}")
        lines.append("")
        section_num += 1

    # Data operations summary
    if db_ops:
        lines.append(f"## {section_num}. 数据操作汇总")
        lines.append("")
        lines.append("| 操作 | 表 | 类.方法 | 说明 |")
        lines.append("|------|-----|---------|------|")
        for op in db_ops:
            op_label = {'SELECT': '读', 'INSERT': '写', 'UPDATE': '写', 'DELETE': '删'}.get(op['operation'], op['operation'])
            lines.append(f"| {op_label} | {op['table']} | {op['dao']}.{op.get('method', '')} | {op['description']} |")
        lines.append("")

    return '\n'.join(lines), all_nodes, db_ops


def main():
    args = parse_args()
    entries_data = _load_json(args.entries)
    if not entries_data:
        print("ERROR: Cannot load entries.json", file=__import__('sys').stderr)
        __import__('sys').exit(1)

    all_entries = entries_data.get("entries", []) if isinstance(entries_data, dict) else entries_data
    now = datetime.now().strftime("%Y-%m-%d")
    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    # Load bridges index
    bridges_data = _load_json(os.path.join(args.cache_dir, "phase4", "bridges.json")) or {}

    summary_flows = []
    detail_flows = []
    generated_count = 0

    for entry in all_entries:
        entry_id = entry.get("id")
        entry_type = entry.get("type", "unknown")

        flow_data = load_flow_data(args.cache_dir, entry_id)
        if not flow_data:
            print(f"  SKIP: {entry_id} - no flow data found")
            continue

        chain = flow_data.get("chain", [])
        if not chain:
            print(f"  SKIP: {entry_id} - empty chain")
            continue

        if flow_data.get("flowStatus") == "NO_ENDPOINT":
            print(f"  SKIP: {entry_id} - NO_ENDPOINT")
            continue

        md_content, all_nodes, db_ops = generate_flow_md(entry, flow_data, chain, cache_dir=args.cache_dir)
        if not md_content:
            continue

        # Write MD file
        entry_class = chain[0].get('class', 'unknown')
        entry_method = chain[0].get('method', 'unknown')
        out_dir = os.path.join(output_dir, "flows", entry_type, entry_class)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{entry_method}.md")
        with open(out_path, 'w') as f:
            f.write(md_content)
        generated_count += 1

        doc_path = f"flows/{entry_type}/{entry_class}/{entry_method}.md"
        entry_desc = chain[0].get('description', '')

        modules = list(dict.fromkeys(
            _get_module(n.get('filePath', '')) for n in all_nodes if _get_module(n.get('filePath', ''))
        ))

        rmb_bridge = flow_data.get('rmbBridge')
        bridge_count = 1 if rmb_bridge and rmb_bridge.get('matchingStatus') == 'MATCHED' else 0

        summary_flows.append({
            "id": entry_id, "type": entry_type,
            "name": f"{entry_class}.{entry_method}",
            "description": entry_desc, "entryClass": entry_class,
            "entryMethod": entry_method, "bridgeCount": bridge_count,
            "docPath": doc_path,
        })

        detail_flows.append({
            "entryId": entry_id, "entryType": entry_type,
            "flowCategory": flow_data.get("flowType", "STANDALONE_FLOW"),
            "modules": modules, "status": "COMPLETE",
            "entryClass": entry_class, "entryMethod": entry_method,
            "docPath": doc_path,
            "bridges": [rmb_bridge] if rmb_bridge else [],
            "dbOperations": db_ops,
        })

        print(f"  {entry_id}: {entry_class}.{entry_method} -> {doc_path}")

    # Summary files
    summary_by_type = defaultdict(int)
    for e in all_entries:
        summary_by_type[e.get('type', 'unknown')] += 1

    _save_json(os.path.join(output_dir, "flow-summary.json"), {
        "version": "3.0", "generator": "flow-trace-java", "generateDate": now,
        "totalFlows": len(summary_flows),
        "totalBridges": bridges_data.get("totalBridges", 0),
        "matchedBridges": bridges_data.get("matched", 0),
        "summaryByType": dict(summary_by_type), "flows": summary_flows,
    })

    _save_json(os.path.join(output_dir, "flow-detail.json"), {
        "version": "3.0", "generator": "flow-trace-java", "generateDate": now,
        "flows": detail_flows,
    })

    # Data lineage
    table_flows = defaultdict(lambda: {"readByFlows": [], "writtenByFlows": []})
    for df in detail_flows:
        eid = df['entryId']
        for op in df.get('dbOperations', []):
            table = op.get('table', '')
            operation = op.get('operation', '').upper()
            if table and table != '—':
                if operation == 'SELECT':
                    if eid not in table_flows[table]["readByFlows"]:
                        table_flows[table]["readByFlows"].append(eid)
                elif operation in ('INSERT', 'UPDATE', 'DELETE'):
                    if eid not in table_flows[table]["writtenByFlows"]:
                        table_flows[table]["writtenByFlows"].append(eid)

    tables_list = [{"name": n, "readByFlows": d["readByFlows"], "writtenByFlows": d["writtenByFlows"]}
                   for n, d in sorted(table_flows.items())]
    _save_json(os.path.join(output_dir, "flow-data-lineage.json"), {
        "version": "3.0", "generator": "flow-trace-java", "generateDate": now,
        "tables": tables_list,
    })

    print(f"\nPhase 6 Complete!")
    print(f"  Generated: {generated_count} flow documents")
    print(f"  Summary: {len(summary_flows)} entries")
    print(f"  Data lineage: {len(tables_list)} tables")


if __name__ == '__main__':
    main()
