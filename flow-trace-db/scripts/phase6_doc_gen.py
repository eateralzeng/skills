"""Phase 6: Document Generation for flow-trace-db (v3 - with business overview)"""
import json, os, argparse
from datetime import datetime
from collections import defaultdict

try:
    import jinja2
except ImportError:
    jinja2 = None


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


def get_module(filepath):
    if not filepath:
        return ""
    return filepath.split('/')[0] if '/' in filepath else filepath


def extract_package(filepath):
    if not filepath:
        return ""
    marker = "src/main/java/"
    idx = filepath.find(marker)
    if idx < 0:
        return ""
    pkg = filepath[idx + len(marker):]
    pkg = pkg.rsplit('/', 1)[0] if '/' in pkg else pkg
    return pkg.replace('/', '.')


def di_marker(node):
    """Generate terminal marker: [读]/[写]/[删]/[RMB外调]"""
    di = node.get('domainInteraction', {})
    if not di:
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


def _e_get(e, key, default=''):
    alt = {'class': 'className', 'method': 'methodName', 'file': 'filePath'}
    if key in alt:
        return e.get(key, e.get(alt[key], default))
    return e.get(key, default)


def build_by_parent(chain):
    by_parent = defaultdict(list)
    for n in chain[1:]:
        pid = n.get('parentId', '')
        if pid:
            by_parent[pid].append(n)
        else:
            by_parent[n.get('parentLayer', 0)].append(n)
    return by_parent


def generate_business_overview(chain, bridges_info, cache_dir):
    """Generate business overview section from chain descriptions.

    Returns markdown string with:
    1. Business steps (aggregated from node descriptions)
    2. Data operations table
    3. External calls table
    """
    lines = []
    steps = []
    db_ops = []
    ext_calls = []

    for node in chain:
        desc = node.get('description', '')
        di = node.get('domainInteraction', {})

        if desc:
            steps.append(desc)

        if di and isinstance(di, dict):
            dtype = di.get('type', '').upper()
            if dtype == 'DATABASE':
                db_ops.append({
                    'operation': di.get('operation', ''),
                    'table': di.get('table', ''),
                    'description': desc or f"{di.get('operation', '')} {di.get('table', '')}",
                })
            elif dtype == 'EXTERNAL':
                ext_calls.append({
                    'method': node.get('method', ''),
                    'target': di.get('target', ''),
                    'description': desc or node.get('method', ''),
                })

    # Also collect DB ops from RMB receiver chains
    for b in bridges_info:
        merged_id = b.get('mergedFlowId')
        if merged_id:
            merged = load_json(os.path.join(cache_dir, "phase4", f"{merged_id}.json"))
            if merged:
                for n in merged.get('chain', []):
                    di = n.get('domainInteraction', {})
                    if di and isinstance(di, dict) and di.get('type', '').upper() == 'DATABASE':
                        db_ops.append({
                            'operation': di.get('operation', ''),
                            'table': di.get('table', ''),
                            'description': n.get('description', '') or f"{di.get('operation', '')} {di.get('table', '')}",
                        })

    # Business steps
    if steps:
        lines.append("主要步骤：")
        for i, step in enumerate(steps, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    # Data operations table
    if db_ops:
        lines.append("### 数据操作")
        lines.append("")
        lines.append("| 操作 | 表 | 说明 |")
        lines.append("|------|-----|------|")
        for op in db_ops:
            operation = op['operation']
            if operation == 'SELECT':
                op_label = '读'
            elif operation == 'DELETE':
                op_label = '删'
            else:
                op_label = '写'
            lines.append(f"| {op_label} | {op['table']} | {op['description']} |")
        lines.append("")

    # External calls table
    if ext_calls:
        lines.append("### 外部调用")
        lines.append("")
        lines.append("| 调用 | Topic | 说明 |")
        lines.append("|------|-------|------|")
        for ec in ext_calls:
            lines.append(f"| {ec['method']} | {ec['target']} | {ec['description']} |")
        lines.append("")

    return '\n'.join(lines)


def render_full_chain_tree(sender_chain, matched_bridges, cache_dir):
    lines = []
    counter = [0]
    all_nodes = []
    bridges_info = []

    by_parent = build_by_parent(sender_chain)

    bridge_lookup = {}
    for b in matched_bridges:
        sn = b.get('senderNode', {})
        key = f"{sn.get('class','')}.{sn.get('method','')}"
        recv_chain = []
        merged_id = b.get('mergedFlowId')
        if merged_id:
            merged = load_json(os.path.join(cache_dir, "phase4", f"{merged_id}.json"))
            if merged:
                full = merged.get('chain', [])
                found = False
                for n in full:
                    if n.get('layerType') == 'BRIDGE':
                        found = True
                        continue
                    if found:
                        recv_chain.append(n)
        bridge_lookup[key] = (b, recv_chain)

    def node_line(node, is_last=True):
        counter[0] += 1
        idx = counter[0]
        cls = node.get('class', '?')
        method = node.get('method', '?')
        desc = node.get('description', '')
        marker = di_marker(node)
        mod = get_module(node.get('file_path', ''))
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
            'file_path': node.get('file_path', ''), 'description': desc,
            'package': node.get('package', extract_package(node.get('file_path', ''))),
            'role': node.get('role', ''),
            'domainInteraction': di if di else None,
        }
        all_nodes.append(n_info)

        line = f"[{idx}] {cls}.{method}(){marker}{mod_tag}"
        return line, desc + db_extra

    def render_subtree(nodes, prefix, parent_id):
        for i, child in enumerate(nodes):
            is_last = (i == len(nodes) - 1)
            line, desc = node_line(child, is_last)
            lines.append(f"{prefix}{'└──' if is_last else '├──'} {line}")
            if desc:
                ext = "    " if is_last else "│   "
                lines.append(f"{prefix}{ext}└── {desc}")

            nid = child.get('nodeId', '')
            sender_key = f"{child.get('class','')}.{child.get('method','')}"
            bridge_data = bridge_lookup.get(sender_key) if child.get('layerType') == 'RMB_CLIENT' else None

            if bridge_data:
                b, recv_chain = bridge_data
                topic = b.get('topic', '')
                lines.append(f"{prefix}{'    ' if is_last else '│   '}│")
                recv_mod = get_module(recv_chain[0].get('file_path','')) if recv_chain else topic
                lines.append(f"{prefix}{'    ' if is_last else '│   '}│  ⟿ RMB [{topic}] → {recv_mod}")
                lines.append(f"{prefix}{'    ' if is_last else '│   '}│")

                if recv_chain:
                    recv_by_parent = build_by_parent(recv_chain)
                    render_receiver(recv_chain[0], recv_chain, recv_by_parent,
                                   prefix + ("    " if is_last else "│   "), b)

                recv_node = b.get('receiverNode', {})
                bridges_info.append({
                    'mergedFlowId': b.get('mergedFlowId', ''),
                    'protocol': 'RMB',
                    'mode': b.get('topicMode', 'SYNC'),
                    'matchBy': b.get('matchBy', 'topic'),
                    'topic': topic,
                    'senderModule': get_module(child.get('file_path', '')),
                    'senderNode': f"{child.get('class','')}.{child.get('method','')}()",
                    'receiverModule': get_module(
                        next((n.get('file_path','') for n in recv_chain), '')
                    ),
                    'receiverNode': f"{recv_node.get('class','?')}.doHandle()" if recv_node else '',
                    'status': b.get('matchingStatus', ''),
                    'description': f"{child.get('class','')}.{child.get('method','')} → {recv_node.get('class','?') if recv_node else '?'}",
                    'matchDescription': b.get('matchDescription', ''),
                })
            else:
                children = by_parent.get(nid, [])
                if children:
                    child_prefix = prefix + ("    " if is_last else "│   ")
                    render_subtree(children, child_prefix, nid)

    def render_receiver(root, recv_chain, recv_by_parent, prefix, bridge):
        line, desc = node_line(root)
        lines.append(f"{prefix}└── {line}")
        if desc:
            lines.append(f"{prefix}    └── {desc}")

        nid = root.get('nodeId', '')
        children = recv_by_parent.get(nid, [])
        for i, child in enumerate(children):
            is_last = (i == len(children) - 1)
            cline, cdesc = node_line(child, is_last)
            lines.append(f"{prefix}    {'└──' if is_last else '├──'} {cline}")
            if cdesc:
                lines.append(f"{prefix}    {'    ' if is_last else '│   '}└── {cdesc}")

            cnid = child.get('nodeId', '')
            cchildren = recv_by_parent.get(cnid, [])
            if cchildren:
                cp = prefix + "    " + ("    " if is_last else "│   ")
                render_receiver_children(cchildren, recv_by_parent, cp)

    def render_receiver_children(nodes, recv_by_parent, prefix):
        for i, child in enumerate(nodes):
            is_last = (i == len(nodes) - 1)
            line, desc = node_line(child, is_last)
            lines.append(f"{prefix}{'└──' if is_last else '├──'} {line}")
            if desc:
                lines.append(f"{prefix}{'    ' if is_last else '│   '}└── {desc}")

            nid = child.get('nodeId', '')
            children = recv_by_parent.get(nid, [])
            if children:
                cp = prefix + ("    " if is_last else "│   ")
                render_receiver_children(children, recv_by_parent, cp)

    entry = sender_chain[0]
    lines.append(f"[入口] {entry.get('class','?')}.{entry.get('method','?')}()")

    entry_nid = entry.get('nodeId', '')
    children = by_parent.get(entry_nid, [])
    render_subtree(children, "  ", entry_nid)

    return '\n'.join(lines), bridges_info, all_nodes


def generate_flow_md(entry, chain_data, matched_bridges, cache_dir, all_nodes, bridges_info):
    """Generate complete MD document for a flow."""
    sender_chain = chain_data.get('chain', [])
    if not sender_chain:
        return None

    entry_node = sender_chain[0]
    entry_class = entry_node.get('class', '?')
    entry_method = entry_node.get('method', '?')
    entry_description = entry_node.get('description', '')
    entry_type = _e_get(entry, 'type')

    lines = []
    lines.append(f"# {entry_class}.{entry_method}()")
    lines.append("")
    if entry_description:
        lines.append(f"> {entry_description}")
    lines.append("")

    # Section 1: Business overview
    lines.append("## 1. 流程业务概述")
    lines.append("")
    biz_overview = generate_business_overview(sender_chain, bridges_info, cache_dir)
    lines.append(biz_overview)

    # Section 2: Full call chain
    lines.append("## 2. 完整调用链路")
    lines.append("")
    lines.append("```")
    call_chain_tree, bridges_info, all_nodes = render_full_chain_tree(
        sender_chain, matched_bridges, cache_dir
    )
    lines.append(call_chain_tree)
    lines.append("```")
    lines.append("")

    # Section 3: RMB bridge
    if bridges_info:
        lines.append("## 3. RMB 桥接")
        lines.append("")
        for bi in bridges_info:
            lines.append(f"### {bi.get('topic', '')}")
            lines.append("")
            lines.append(f"- 发送端: {bi.get('senderNode', '')} [{bi.get('senderModule', '')}]")
            lines.append(f"- 接收端: {bi.get('receiverNode', '')} [{bi.get('receiverModule', '')}]")
            lines.append(f"- 协议: {bi.get('protocol', '')} ({bi.get('mode', '')})")
            lines.append(f"- 匹配方式: {bi.get('matchDescription', '')}")
            lines.append("")

    # Section 4: Data operations summary
    db_ops = []
    for n in all_nodes:
        di = n.get('domainInteraction')
        if di and isinstance(di, dict) and di.get('type', '').upper() == 'DATABASE':
            db_ops.append(n)

    if db_ops:
        lines.append("## 4. 数据操作汇总")
        lines.append("")
        lines.append("| 操作 | 表 | 类.方法 | 说明 |")
        lines.append("|------|-----|---------|------|")
        for n in db_ops:
            di = n['domainInteraction']
            op = di.get('operation', '')
            op_label = {'SELECT': '读', 'INSERT': '写', 'UPDATE': '写', 'DELETE': '删'}.get(op, op)
            lines.append(f"| {op_label} | {di.get('table', '')} | {n.get('class', '')}.{n.get('method', '')} | {n.get('description', '')} |")
        lines.append("")

    return '\n'.join(lines), bridges_info, all_nodes


def do_phase6():
    p = argparse.ArgumentParser(description="Phase 6: Document Generation")
    p.add_argument("cache_dir", help="Cache directory with chain JSON files")
    p.add_argument("output_dir", help="Output directory for generated docs")
    p.add_argument("entries_path", help="Path to entry list JSON")
    args = p.parse_args()
    cache_dir = args.cache_dir
    output_dir = args.output_dir
    entries_path = args.entries_path

    print("Phase 6: Document Generation")
    print("=" * 50)

    bridges_data = load_json(os.path.join(cache_dir, "phase4", "bridges.json")) or {}
    sender_bridge_index = bridges_data.get('senderBridgeIndex', {})

    with open(entries_path) as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "entries" in raw:
        all_entries = raw["entries"]
    elif isinstance(raw, list):
        all_entries = raw
    else:
        all_entries = raw.get("entries", [])

    now = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(output_dir, exist_ok=True)

    summary_flows = []
    detail_flows = []
    generated_count = 0

    for entry in all_entries:
        eid = _e_get(entry, 'id')
        chain_data = load_chain(cache_dir, eid)
        if not chain_data:
            continue
        sender_chain = chain_data.get('chain', [])
        if not sender_chain:
            continue

        entry_type = _e_get(entry, 'type')
        bridges_for_entry = sender_bridge_index.get(eid, [])
        matched_bridges = [b for b in bridges_for_entry if b.get('matchingStatus') == 'MATCHED']

        md_content, bridges_info, all_nodes = generate_flow_md(
            entry, chain_data, matched_bridges, cache_dir, [], []
        )
        if not md_content:
            continue

        # Re-render to get all_nodes and bridges_info
        _, bridges_info, all_nodes = render_full_chain_tree(sender_chain, matched_bridges, cache_dir)

        out_dir = os.path.join(output_dir, "flows", entry_type, sender_chain[0].get('class', 'unknown'))
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f"{sender_chain[0].get('method', 'unknown')}.md")
        with open(out_path, 'w') as f:
            f.write(md_content)
        generated_count += 1

        entry_node = sender_chain[0]
        entry_class = entry_node.get('class', '?')
        entry_method = entry_node.get('method', '?')
        entry_description = entry_node.get('description', '')
        doc_path = f"flows/{entry_type}/{entry_class}/{entry_method}.md"

        modules_list = []
        modules_seen = set()
        for n in all_nodes:
            mod = n.get('module', '')
            if mod and mod not in modules_seen:
                modules_list.append(mod)
                modules_seen.add(mod)
        for b in bridges_info:
            for m in [b.get('senderModule', ''), b.get('receiverModule', '')]:
                if m and m not in modules_seen:
                    modules_list.append(m)
                    modules_seen.add(m)
        modules = modules_list or [get_module(entry_node.get('file_path', ''))]

        # Class metadata (only Service/Mapper layers)
        class_meta = {}
        kept_roles = {'业务服务层', 'MyBatis Mapper', '数据仓库', '数据访问层', 'RMB 客户端', 'RMB 接收端入口', 'Controller 入口'}
        for n in all_nodes:
            cls = n.get('class', '')
            mod = n.get('module', '')
            role = n.get('role', '')
            if not cls or not mod or role not in kept_roles:
                continue
            if mod not in class_meta:
                class_meta[mod] = {}
            if cls not in class_meta[mod]:
                class_meta[mod][cls] = {'class': cls, 'package': n.get('package', ''), 'role': role}
        class_metadata_by_module = {mod: list(classes.values()) for mod, classes in sorted(class_meta.items())}

        # DB operations
        db_operations = []
        for n in all_nodes:
            di = n.get('domainInteraction')
            if di and isinstance(di, dict) and di.get('type', '').upper() == 'DATABASE':
                op = di.get('operation', '').upper()
                db_operations.append({
                    'module': n.get('module', ''),
                    'dao': n.get('class', '?'),
                    'operation': op,
                    'table': di.get('table', ''),
                    'description': n.get('description', ''),
                })
        if not db_operations:
            db_operations.append({'module': modules[0] if modules else '',
                                  'dao': '—', 'operation': '—', 'table': '—', 'description': '不直接操作数据库'})

        summary_flows.append({
            "id": eid, "type": entry_type, "name": f"{entry_class}.{entry_method}",
            "description": entry_description, "entryClass": entry_class,
            "entryMethod": entry_method, "bridgeCount": len(bridges_info), "docPath": doc_path,
        })
        detail_flows.append({
            "entryId": eid, "entryType": entry_type, "flowCategory": "CROSS_MODULE_FLOW" if bridges_info else "STANDALONE_FLOW",
            "modules": modules, "status": "COMPLETE", "entryClass": entry_class,
            "entryMethod": entry_method, "docPath": doc_path,
            "bridges": bridges_info, "classMetadata": class_metadata_by_module,
            "dbOperations": db_operations,
        })

    # Summary files
    print(f"\n[Step 2] Generating summary files...")
    summary_by_type = defaultdict(int)
    for e in all_entries:
        summary_by_type[_e_get(e, 'type')] += 1

    save_json(os.path.join(output_dir, "flow-summary.json"), {
        "version": "3.0", "generator": "flow-trace-db", "generateDate": now,
        "totalFlows": len(summary_flows),
        "totalBridges": bridges_data.get('totalBridges', 0),
        "matchedBridges": bridges_data.get('matchedBridges', 0),
        "summaryByType": dict(summary_by_type), "flows": summary_flows,
    })

    md = ["# 流程汇总\n", f"> 生成工具: flow-trace-db\n> 生成日期: {now}\n",
          "## 统计\n", "| 指标 | 数量 |\n|------|------|",
          f"| 总流程数 | {len(summary_flows)} |",
          f"| 总桥接数 | {bridges_data.get('totalBridges', 0)} |",
          f"| 已匹配桥接 | {bridges_data.get('matchedBridges', 0)} |"]
    for t, c in sorted(summary_by_type.items()):
        md.append(f"| {t} | {c} |")
    md.append("")
    for flow_type in ('controller', 'rmb', 'job'):
        type_flows = [f for f in summary_flows if f['type'] == flow_type]
        if type_flows:
            md.append(f"## {flow_type} 入口\n")
            md.append("| ID | 类名.方法 | 描述 | 桥接数 |")
            md.append("|----|----------|------|--------|")
            for f in type_flows:
                md.append(f"| {f['id']} | {f['name']} | {f['description']} | {f['bridgeCount']} |")
            md.append("")
    with open(os.path.join(output_dir, "flow-summary.md"), 'w') as f:
        f.write('\n'.join(md))

    save_json(os.path.join(output_dir, "flow-detail.json"), {
        "version": "3.0", "generator": "flow-trace-db", "generateDate": now,
        "flows": detail_flows,
    })

    # Data lineage
    print("[Step 3] Generating data lineage...")
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
    save_json(os.path.join(output_dir, "flow-data-lineage.json"),
              {"version": "3.0", "generator": "flow-trace-db", "generateDate": now, "tables": tables_list})
    lin_md = ["# 数据血缘分析\n", f"> 生成工具: flow-trace-db\n> 生成日期: {now}\n",
              "## 表与流程关系\n", "| 表名 | 读取流程 | 写入流程 |\n|------|---------|---------|"]
    for t in tables_list:
        lin_md.append(f"| `{t['name']}` | {', '.join(t['readByFlows']) or '—'} | {', '.join(t['writtenByFlows']) or '—'} |")
    with open(os.path.join(output_dir, "flow-data-lineage.md"), 'w') as f:
        f.write('\n'.join(lin_md))

    print(f"\nPhase 6 Complete!")
    print(f"  flows/**/*.md: {generated_count} files")
    print(f"  flow-data-lineage: {len(tables_list)} tables")


if __name__ == '__main__':
    do_phase6()
