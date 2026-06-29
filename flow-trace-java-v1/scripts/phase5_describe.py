"""Phase 5: Business Semantics Generation for flow-trace-java

Deterministic generation of business descriptions for orchestrator-handled
nodes (virtual nodes, Mapper methods, terminal nodes with domainInteraction,
DISPATCH nodes). Subagent-handled nodes are batched with connectivity
constraints for LLM processing.

Modes:
  prepare: Classify nodes, generate orchestrator descriptions, split subagent
           nodes into batches, output phase5/{entryId}-prepare.json.
  merge:   Apply orchestrator descriptions + subagent output to chain,
           write phase5/{entryId}-semantics.json.

Input:   phase4/{entryId}.json (fallback: phase3/{entryId}-pruned.json)
Output:  phase5/{entryId}-prepare.json (prepare mode)
         phase5/{entryId}-semantics.json (merge mode)
"""
import json, os, argparse, sys, glob


# ── Mapping Tables (deterministic description sources) ──────────────

MAPPER_VERB_MAP = {
    "select": "查询",
    "insert": "新增",
    "update": "更新",
    "delete": "删除",
    "query":  "查询",
    "find":   "查找",
    "get":    "获取",
    "save":   "保存",
    "count":  "统计",
    "exists": "判断是否存在",
}

DI_TEMPLATES = {
    ("DATABASE", "SELECT"):   "查询 {table} 表记录",
    ("DATABASE", "INSERT"):   "向 {table} 表新增记录",
    ("DATABASE", "UPDATE"):   "更新 {table} 表记录",
    ("DATABASE", "DELETE"):   "删除 {table} 表记录",
    ("FILE", "WRITE"):        "写入文件",
    ("FILE", "READ"):         "读取文件",
    ("FILE", "UPLOAD"):       "上传文件",
    ("FILE", "DOWNLOAD"):     "下载文件",
    ("EXTERNAL", "OUT"):      "通过 {protocol} 向 {topic} 发送消息",
    ("EXTERNAL", "IN"):       "通过 {protocol} 接收 {topic} 消息",
}

BRIDGE_TEMPLATES = {
    "RMB": "RMB 桥接：通过 {target} 触发接收端",
}
# MQ / Event / Async 虚拟节点模板已于 2026-06-22 移除（CR-04 选项B：仅保留 RMB 桥接）

DISPATCH_FALLBACK = "多态分发节点（实现细节未识别）"
DISPATCH_IMPL_FALLBACK = "分发实现节点（细节未识别）"

VIRTUAL_CLASSES = ("BRIDGE",)  # 仅 RMB 桥接（CR-04 选项B，2026-06-22）
MAPPER_CLASS_SUFFIXES = ("Mapper", "Repository")


# ── Argument parsing ────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Phase 5: Business Semantics Generation")
    p.add_argument("--mode", required=True, choices=["prepare", "merge"],
                   help="Operation mode")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    p.add_argument("--entry-id", required=True, help="Entry ID (e.g. controller-001)")
    p.add_argument("--batch-size", type=int, default=15, help="Nodes per subagent batch")
    p.add_argument("--subagent-output", help="Path to subagent output JSON (for merge mode)")
    p.add_argument("--prepare-output", help="Path to prepare JSON (for merge mode, defaults to phase5/{entryId}-prepare.json)")
    return p.parse_args()


# ── File path helpers ───────────────────────────────────────────────

def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _phase4_path(cache_dir, entry_id):
    return os.path.join(cache_dir, "phase4", f"{entry_id}.json")


def _phase3_path(cache_dir, entry_id):
    return os.path.join(cache_dir, "phase3", f"{entry_id}-pruned.json")


def _prepare_path(cache_dir, entry_id):
    return os.path.join(cache_dir, "phase5", f"{entry_id}-prepare.json")


def _semantics_path(cache_dir, entry_id):
    return os.path.join(cache_dir, "phase5", f"{entry_id}-semantics.json")


def _load_chain(cache_dir, entry_id):
    """Load chain data, preferring phase4 over phase3."""
    p4 = _phase4_path(cache_dir, entry_id)
    if os.path.exists(p4):
        return _load_json(p4), "phase4"
    p3 = _phase3_path(cache_dir, entry_id)
    if os.path.exists(p3):
        return _load_json(p3), "phase3"
    raise FileNotFoundError(f"Neither {p4} nor {p3} exists")


# ── Node Classification ─────────────────────────────────────────────

def classify_node(node):
    """Return one of: ORCHESTRATOR_VIRTUAL, ORCHESTRATOR_DISPATCH,
    ORCHESTRATOR_MAPPER, ORCHESTRATOR_DI, SUBAGENT."""
    class_name = node.get("class", "") or ""
    method_name = node.get("method", "") or ""

    # Layer 0: virtual nodes (by class constant)
    if class_name in VIRTUAL_CLASSES:
        return "ORCHESTRATOR_VIRTUAL"

    # DISPATCH parent node
    if node.get("endpointType") == "DISPATCH":
        return "ORCHESTRATOR_DISPATCH"

    # DISPATCH implementation child
    if node.get("callType") == "DISPATCH_IMPL":
        return "ORCHESTRATOR_DISPATCH"

    # Rule 1: standard Mapper method (terminal + class suffix + method prefix)
    if _is_mapper_method(node):
        return "ORCHESTRATOR_MAPPER"

    # Rule 2: terminal node with domainInteraction
    if node.get("terminal") and node.get("domainInteraction"):
        return "ORCHESTRATOR_DI"

    return "SUBAGENT"


def _is_mapper_method(node):
    """Rule 1: terminal=true + Mapper/Repository 类后缀 + 标准方法名前缀"""
    if not node.get("terminal"):
        return False
    class_name = node.get("class", "") or ""
    method_name = node.get("method", "") or ""
    has_class_suffix = any(s in class_name for s in MAPPER_CLASS_SUFFIXES)
    has_method_prefix = any(method_name.startswith(p) for p in MAPPER_VERB_MAP)
    return has_class_suffix and has_method_prefix


# ── Description Generators ──────────────────────────────────────────

def generate_description(node, category):
    """Dispatch to category-specific generator."""
    if category == "ORCHESTRATOR_VIRTUAL":
        return _generate_virtual_description(node), "inferred-virtual"
    if category == "ORCHESTRATOR_DISPATCH":
        return _generate_dispatch_description(node), "inferred-dispatch"
    if category == "ORCHESTRATOR_MAPPER":
        return _generate_mapper_description(node), "inferred-mapper-name"
    if category == "ORCHESTRATOR_DI":
        return _generate_di_description(node), "inferred-domain-interaction"
    return "", "subagent"


def _generate_mapper_description(node):
    """Rule 1: verb from method prefix + table from di (if available)."""
    method = node.get("method", "")
    di = node.get("domainInteraction") or {}
    table = di.get("table")

    for prefix, verb in MAPPER_VERB_MAP.items():
        if method.startswith(prefix):
            if table and table != "[待确认]":
                return f"{verb} {table} 表记录"
            return f"{verb}记录"
    return method


def _generate_di_description(node):
    """Rule 2: template lookup by (type, operation/direction).

    DATABASE/FILE 用 operation 查表；EXTERNAL 用 direction（OUT/IN）查表
    （VERIFY-04：EXTERNAL 的 operation 为 None，靠 direction 区分收发）。
    """
    di = node.get("domainInteraction") or {}
    di_type = di.get("type")

    if di_type == "EXTERNAL":
        template = DI_TEMPLATES.get(("EXTERNAL", di.get("direction")))
        if not template:
            return f"[未识别 di: EXTERNAL/{di.get('direction')}]"
        rk = di.get("routingKeys") or {}
        return template.format(
            protocol=di.get("protocol") or "",
            topic=rk.get("topic") or di.get("target") or "",  # HTTP 无 topic，fallback target
        )

    operation = di.get("operation")
    template = DI_TEMPLATES.get((di_type, operation))
    if not template:
        return f"[未识别 di: {di_type}/{operation}]"
    if di_type == "DATABASE":
        return template.format(table=di.get("table") or "[未识别]")
    return template


def _generate_virtual_description(node):
    """Virtual node templates by class + nodeId parsing."""
    class_name = node.get("class")
    node_id = node.get("nodeId", "")

    if class_name == "BRIDGE":
        return _generate_bridge_description(node_id)
    return f"虚拟节点 {class_name}"


def _generate_bridge_description(node_id):
    """Parse bridge type and parameters from nodeId suffix."""
    if node_id.startswith("BRIDGE:RMB:"):
        target = node_id[len("BRIDGE:RMB:"):]
        return BRIDGE_TEMPLATES["RMB"].format(target=target or "[未识别]")
    return "桥接节点（元数据缺失）"


# _extract_topic / _extract_event_class 已于 2026-06-22 移除（CR-04：MQ/Event 桥接删除后无引用）


def _generate_dispatch_description(node):
    """DISPATCH fallback when dispatch-summary is unavailable."""
    if node.get("callType") == "DISPATCH_IMPL":
        return DISPATCH_IMPL_FALLBACK
    return DISPATCH_FALLBACK


# ── Connectivity-Constrained Batching ───────────────────────────────

def split_into_batches(nodes, batch_size=15):
    """Split subagent nodes into batches (决策 10：位置无关，完全并行).

    决策 10 废除"传父上下文"后，方法语义与位置无关 → 移除 layer 排序 + 连通性约束
    （父子可同批），改用 nodeId 字典序稳定分批，保证重跑一致即可。
    （CR-06：旧逻辑 sorted(key=layer) + "父子不同批" 已失去意义）
    """
    sorted_ids = sorted(n.get("nodeId", "") for n in nodes)
    return [sorted_ids[i:i + batch_size] for i in range(0, len(sorted_ids), batch_size)]


# ── Prepare Mode ────────────────────────────────────────────────────

def do_prepare(args):
    cache_dir = os.path.abspath(args.cache_dir)
    entry_id = args.entry_id

    chain_data, source_phase = _load_chain(cache_dir, entry_id)
    chain = chain_data.get("chain", [])

    orchestrator_descs = []
    subagent_nodes = []

    for node in chain:
        category = classify_node(node)
        if category == "SUBAGENT":
            subagent_nodes.append(node)
            continue
        desc, source = generate_description(node, category)
        orchestrator_descs.append({
            "nodeId": node.get("nodeId"),
            "category": category,
            "description": desc,
            "source": source,
        })

    batches = split_into_batches(subagent_nodes, args.batch_size)

    output = {
        "entryId": entry_id,
        "source": f"{source_phase}/{entry_id}.json" if source_phase == "phase4" else f"{source_phase}/{entry_id}-pruned.json",
        "orchestratorDescriptions": orchestrator_descs,
        "subagentBatches": batches,
        "summary": {
            "totalNodes": len(chain),
            "orchestratorCount": len(orchestrator_descs),
            "subagentCount": len(subagent_nodes),
            "batchCount": len(batches),
        },
    }

    out_path = _prepare_path(cache_dir, entry_id)
    _save_json(out_path, output)
    print(json.dumps({
        "status": "prepared",
        "entryId": entry_id,
        "out": out_path,
        "summary": output["summary"],
    }, indent=2, ensure_ascii=False))


# ── Merge Mode ──────────────────────────────────────────────────────

def do_merge(args):
    cache_dir = os.path.abspath(args.cache_dir)
    entry_id = args.entry_id

    chain_data, _ = _load_chain(cache_dir, entry_id)
    chain = chain_data.get("chain", [])

    # Load prepare output (for orchestrator descriptions)
    prepare_path = args.prepare_output or _prepare_path(cache_dir, entry_id)
    if not os.path.exists(prepare_path):
        print(f"Error: prepare output not found: {prepare_path}", file=sys.stderr)
        sys.exit(1)
    prepare_data = _load_json(prepare_path)

    # Build nodeId → description map from orchestrator output
    desc_map = {}
    for d in prepare_data.get("orchestratorDescriptions", []):
        desc_map[d["nodeId"]] = {
            "description": d["description"],
            "source": d["source"],
        }

    # Overlay subagent descriptions (higher priority — explicit reasoning)
    subagent_path = args.subagent_output
    if subagent_path and os.path.exists(subagent_path):
        subagent_data = _load_json(subagent_path)
        for d in subagent_data.get("descriptions", []):
            desc_map[d["nodeId"]] = {
                "description": d.get("description", ""),
                "source": d.get("source", "source-code"),
            }

    # Apply descriptions to chain
    updated = 0
    for node in chain:
        node_id = node.get("nodeId")
        if node_id in desc_map:
            new_desc = desc_map[node_id]["description"]
            # Idempotent: only overwrite if description is empty or differs
            if not node.get("description") or node.get("description") != new_desc:
                node["description"] = new_desc
                updated += 1

    chain_data["chain"] = chain

    out_path = _semantics_path(cache_dir, entry_id)
    _save_json(out_path, chain_data)
    print(json.dumps({
        "status": "merged",
        "entryId": entry_id,
        "out": out_path,
        "chainNodes": len(chain),
        "descriptionsUpdated": updated,
        "descriptionsMissing": sum(1 for n in chain if not n.get("description")),
    }, indent=2, ensure_ascii=False))


# ── Main ────────────────────────────────────────────────────────────

MODES = {
    "prepare": do_prepare,
    "merge": do_merge,
}


def main():
    args = parse_args()
    fn = MODES.get(args.mode)
    if fn:
        fn(args)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
