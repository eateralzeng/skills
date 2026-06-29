"""Phase 2a: Call Tree Expansion Management for flow-trace-java

Manages the BFS call-tree expansion process with modes:
- init: Create tree root + progress from an entry
- next-batch: Return the next batch of pending nodes for subagent processing
- merge: Merge subagent discovery results into the tree
- backfill: Fill domainInteraction from Phase 2 lookup
- llm-backfill-prepare: Collect nodes missing domainInteraction for LLM subagent
- llm-backfill-apply: Apply LLM subagent results back to trees
- reconcile-prepare: Scan all trees for shared-node inconsistencies
- reconcile-apply: Apply re-analyzed results to fix inconsistencies

 nodeId = "模块名:包名.类名:方法名" plain string (no hash).
"""
import json, os, argparse, sys


def parse_args():
    p = argparse.ArgumentParser(description="Phase 2a: Call Tree Expansion")
    p.add_argument("--mode", required=True,
                   choices=["init", "next-batch", "merge", "backfill",
                            "llm-backfill-prepare", "llm-backfill-apply",
                            "reconcile-prepare", "reconcile-apply"],
                   help="Operation mode")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    p.add_argument("--entry-id", help="Entry ID (e.g. controller-001)")
    p.add_argument("--entry", help="Path to entries.json (for init mode)")
    p.add_argument("--batch-size", type=int, default=15, help="Nodes per batch")
    p.add_argument("--results", help="Path to subagent output JSON (for merge/llm-backfill-apply mode), or path to directory containing _reconcile-result-*.json (for reconcile-apply)")
    p.add_argument("--report", help="Path to _reconcile-report.json (for reconcile-apply mode)")
    p.add_argument("--project-dir", help="Project source root (for llm-backfill-prepare filePath resolution)")
    p.add_argument("--max-depth", type=int, default=20, help="Max BFS depth")
    p.add_argument("--max-nodes", type=int, default=500, help="Max nodes per tree")
    p.add_argument("--max-fanout", type=int, default=10, help="Max children per node")
    return p.parse_args()


# ── File paths ──────────────────────────────────────────────────────

def _tree_path(cache_dir, entry_id):
    return os.path.join(cache_dir, "phase2a", f"{entry_id}-tree.json")


def _progress_path(cache_dir, entry_id):
    return os.path.join(cache_dir, "phase2a", f"{entry_id}-progress.json")


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Init mode ──────────────────────────────────────────────────────

def do_init(args):
    entries = _load_json(args.entry)
    entry = None
    for e in entries["entries"]:
        if e["id"] == args.entry_id:
            entry = e
            break
    if not entry:
        print(f"ERROR: entry '{args.entry_id}' not found in entries.json", file=sys.stderr)
        sys.exit(1)

    root = {
        "nodeId": entry["nodeId"],
        "class": entry["className"],
        "method": entry["methodName"],
        "package": "",
        "filePath": entry.get("filePath", ""),
        "layer": 0,
        "layerType": "ENTRY",
        "parentId": None,
        "callType": "DIRECT",
        "terminal": False,
        "description": "",
        "domainInteraction": None,
        "children": [],
    }

    tree = {
        "entryId": entry["id"],
        "entryType": entry.get("type", "unknown"),
        "rootNodeId": root["nodeId"],
        "nodes": {root["nodeId"]: root},
    }

    progress = {
        "entryId": entry["id"],
        "expandedNodes": [root["nodeId"]],
        "pendingNodes": [root["nodeId"]],
        "totalNodes": 1,
        "maxDepth": args.max_depth,
        "maxNodes": args.max_nodes,
        "maxFanout": args.max_fanout,
    }

    tp = _tree_path(args.cache_dir, args.entry_id)
    pp = _progress_path(args.cache_dir, args.entry_id)

    if os.path.exists(tp) or os.path.exists(pp):
        print(f"ERROR: '{args.entry_id}' already initialized. Delete {tp} and {pp} to reinitialize.", file=sys.stderr)
        sys.exit(1)

    _save_json(tp, tree)
    _save_json(pp, progress)

    print(json.dumps({
        "status": "initialized",
        "entryId": args.entry_id,
        "rootNodeId": root["nodeId"],
        "treePath": tp,
        "progressPath": pp,
    }, indent=2))


# ── Next-batch mode ────────────────────────────────────────────────

def do_next_batch(args):
    pp = _progress_path(args.cache_dir, args.entry_id)
    tp = _tree_path(args.cache_dir, args.entry_id)

    progress = _load_json(pp)
    tree = _load_json(tp)

    pending = progress["pendingNodes"]
    batch = pending[:args.batch_size]
    remaining = pending[args.batch_size:]

    # Build batch with node details
    batch_nodes = []
    for nid in batch:
        node = tree["nodes"].get(nid)
        if node:
            batch_nodes.append({
                "nodeId": node["nodeId"],
                "class": node["class"],
                "method": node["method"],
                "filePath": node.get("filePath", ""),
                "layer": node["layer"],
            })

    result = {
        "entryId": args.entry_id,
        "batch": batch_nodes,
        "hasMore": len(remaining) > 0,
        "remainingCount": len(remaining),
    }

    print(json.dumps(result, indent=2))


# ── nodeId Construction (same logic as Phase 1) ────────────────────

def build_node_id(fpath, method_name):
    """Build nodeId from file path and method name.

    Format: 模块名:包名.类名:方法名
    Identical to phase1a_entry_scan.build_node_id to ensure consistency.
    """
    fpath = fpath.replace('\\', '/')
    parts = fpath.split('/')
    module = parts[0] if parts else ''
    marker = 'src/main/java/'
    marker_idx = fpath.find(marker)
    if marker_idx >= 0:
        pkg_path = fpath[marker_idx + len(marker):]
        pkg_path = pkg_path.rsplit('/', 1)[0]
        pkg = pkg_path.replace('/', '.')
    else:
        pkg = ''
    cls = os.path.basename(fpath).replace('.java', '')
    full_class = f'{pkg}.{cls}' if pkg else cls
    return f'{module}:{full_class}:{method_name}'


_RESOLVE_FALLBACK_WARNED = set()


def _resolve_call_id(call):
    """Build child nodeId from a normalized call dict.

    Priority: targetNodeId > build_node_id from path > fallback from class.
    """
    if call.get('targetNodeId'):
        return call['targetNodeId']
    target_path = call.get('targetFilePath') or ''
    target_method = call.get('targetMethod', '')
    if target_path and 'src/main/java/' in target_path:
        return build_node_id(target_path, target_method)
    target_class = call.get('targetClass', '')
    module = target_path.split('/')[0] if '/' in target_path else ''
    if module and target_class:
        return f"{module}:{target_class}:{target_method}"
    # ISSUE-2a-24: 2-segment fallback (module missing). May cause dedup mismatch
    # or false inconsistency in reconcile. Dedup warning per (class, method).
    key = (target_class, target_method)
    if key not in _RESOLVE_FALLBACK_WARNED:
        _RESOLVE_FALLBACK_WARNED.add(key)
        print(f"WARNING: 2-segment nodeId for {target_class}:{target_method} (no module info from targetFilePath). May cause dedup mismatch or false inconsistency.", file=sys.stderr)
    return f"{target_class}:{target_method}"


def _create_child_node(call, parent_node, *, max_depth=None):
    """Create a child node dict from a normalized call.

    Centralizes child node creation logic shared by merge and reconcile-apply.
    Handles DISPATCH semantics (patternRef), field defaults, DI normalization,
    and "no source file → terminal" fallback.
    """
    child_id = _resolve_call_id(call)

    # Field normalization: safe .get() everywhere (fixes ISSUE-2a-17)
    target_method = call.get("targetMethod", "")

    # Derive full class name from nodeId
    # 3-segment: "module:pkg.ClassName:method" → parts[1]
    # 2-segment (fallback): "pkg.ClassName:method" → parts[0]
    parts = child_id.split(':')
    if len(parts) >= 3:
        full_class = parts[1]
    elif len(parts) == 2:
        full_class = parts[0]
    else:
        full_class = call.get("targetClass", "")

    # DI normalization: infer DATABASE type from table/operation
    di = call.get("domainInteraction")
    if di and isinstance(di, dict) and not di.get("type") and (di.get("table") or di.get("operation")):
        di = dict(di, type="DATABASE")

    is_endpoint = call.get("isEndpoint", False)

    child_node = {
        "nodeId": child_id,
        "class": full_class,
        "method": target_method,
        "package": call.get("targetPackage", ""),
        "filePath": call.get("targetFilePath", ""),
        "layer": parent_node["layer"] + 1,
        "layerType": "TERMINAL" if is_endpoint else "INTERNAL",
        "parentId": parent_node["nodeId"],
        "callType": call.get("callType", "DIRECT"),
        "terminal": is_endpoint,
        "description": "",
        "domainInteraction": di,
        "children": [],
    }

    # DISPATCH semantics (fixes ISSUE-2a-15)
    if call.get("endpointType") == "DISPATCH" or call.get("patternRef"):
        child_node["terminal"] = True
        child_node["layerType"] = "TERMINAL"
        child_node["patternRef"] = call.get("patternRef", "")
    # No source file → terminal (unifies prior implicit logic in merge)
    elif not child_node["filePath"]:
        child_node["terminal"] = True
        child_node["layerType"] = "TERMINAL"

    # ISSUE-2a-22: maxDepth 强制检查（编排器失误兜底）
    if max_depth is not None and child_node["layer"] >= max_depth and not child_node["terminal"]:
        child_node["terminal"] = True
        child_node["layerType"] = "TERMINAL"

    return child_node


# ── Format normalization ────────────────────────────────────────────

NOISE_CATEGORIES = frozenset({
    "DATA_CONTAINER", "JDK", "FRAMEWORK", "LOGGER", "GETTER_SETTER",
    "LOMBOK", "CONSTRUCTOR", "EXTERNAL_DEP",
})

ENDPOINT_CATEGORIES = frozenset({
    "ENDPOINT_MAPPER", "ENDPOINT_EXTERNAL", "ENDPOINT_MQ",
    "RMB_EXTERNAL", "HTTP_EXTERNAL", "DATABASE", "MQ_PUBLISH", "FILE_WRITE",
    "DISPATCH",
})

ENDPOINT_TYPE_MAP = {
    "ENDPOINT_MAPPER": "DATABASE",
    "ENDPOINT_EXTERNAL": "EXTERNAL",
    "ENDPOINT_MQ": "MQ_PUBLISH",
    "RMB_EXTERNAL": "RMB_EXTERNAL",
    "HTTP_EXTERNAL": "HTTP_EXTERNAL",
    "DATABASE": "DATABASE",
    "MQ_PUBLISH": "MQ_PUBLISH",
    "FILE_WRITE": "FILE_WRITE",
}


def _normalize_results(raw):
    """Normalize subagent output to canonical format.

    Handles known format variations from LLM subagents:
    - Field name variants (methodCalls/calledClass/calledMethod)
    - category-based classification instead of isEndpoint
    - Missing fields with safe defaults
    """
    # Wrap bare array
    if isinstance(raw, list):
        raw = {"results": raw}

    results = raw.get("results", [])
    normalized = []

    for item in results:
        calls = item.get("calls") or item.get("methodCalls") or []
        norm_calls = []

        for c in calls:
            category = c.get("category", "")

            # Filter noise: skip calls classified as noise by subagent
            if category in NOISE_CATEGORIES:
                continue

            nc = {}

            # Field name mapping
            nc["targetMethod"] = c.get("targetMethod") or c.get("calledMethod", "")
            nc["targetClass"] = c.get("targetClass") or c.get("calledClass", "")
            nc["targetFilePath"] = c.get("targetFilePath") or ""
            nc["targetPackage"] = c.get("targetPackage", "")

            # isEndpoint: explicit > category > default false
            if "isEndpoint" in c:
                nc["isEndpoint"] = bool(c["isEndpoint"])
            elif category in ENDPOINT_CATEGORIES:
                nc["isEndpoint"] = True
            elif c.get("endpointType") == "DISPATCH":
                nc["isEndpoint"] = True
            else:
                nc["isEndpoint"] = False

            # endpointType: explicit > category heuristic
            nc["endpointType"] = c.get("endpointType")
            if nc["isEndpoint"] and not nc["endpointType"]:
                nc["endpointType"] = ENDPOINT_TYPE_MAP.get(category) or category

            # patternRef: for DISPATCH nodes
            nc["patternRef"] = c.get("patternRef")

            # callType: explicit > default
            nc["callType"] = c.get("callType", "DIRECT")

            # domainInteraction
            nc["domainInteraction"] = c.get("domainInteraction")

            # Preserve targetNodeId if provided (avoids nodeId degradation)
            if c.get("targetNodeId"):
                nc["targetNodeId"] = c["targetNodeId"]

            norm_calls.append(nc)

        norm_item = {
            "nodeId": item.get("nodeId", ""),
            "calls": norm_calls,
        }
        for opt_key in ("class", "method", "filePath"):
            if opt_key in item:
                norm_item[opt_key] = item[opt_key]

        normalized.append(norm_item)

    return {"results": normalized}


# ── Merge mode ─────────────────────────────────────────────────────

def _count_children(nodes, parent_id):
    return sum(1 for n in nodes.values() if n.get("parentId") == parent_id)


def _build_child_index(nodes):
    """Build parentId -> [childIds] index from flat nodes dict."""
    idx = {}
    for nid, node in nodes.items():
        pid = node.get("parentId")
        if pid:
            idx.setdefault(pid, []).append(nid)
    return idx


def _build_file_index(project_dir):
    """Build shortClassName.java -> [relative_paths] index, once."""
    if not project_dir:
        return {}
    index = {}
    skip_dirs = {'.git', 'node_modules', 'target', 'build'}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            if f.endswith('.java'):
                rel = os.path.relpath(os.path.join(root, f), project_dir).replace('\\', '/')
                index.setdefault(f, []).append(rel)
    return index


def do_merge(args):
    tp = _tree_path(args.cache_dir, args.entry_id)
    pp = _progress_path(args.cache_dir, args.entry_id)

    tree = _load_json(tp)
    progress = _load_json(pp)
    results = _load_json(args.results)
    results = _normalize_results(results)

    added = 0
    skipped_dup = 0
    skipped_fanout = 0
    skipped_max = 0

    for parent_result in results.get("results", []):
        parent_id = parent_result["nodeId"]
        parent_node = tree["nodes"].get(parent_id)
        if not parent_node:
            continue

        parent_fanout = _count_children(tree["nodes"], parent_id)

        for call in parent_result.get("calls", []):
            child_id = _resolve_call_id(call)

            # Dedup: same nodeId already in this tree
            if child_id in tree["nodes"]:
                skipped_dup += 1
                continue

            # Max fanout check
            if parent_fanout >= progress["maxFanout"]:
                skipped_fanout += 1
                continue

            # Max nodes check
            if progress["totalNodes"] >= progress["maxNodes"]:
                skipped_max += 1
                continue

            child_node = _create_child_node(call, parent_node, max_depth=progress["maxDepth"])

            tree["nodes"][child_id] = child_node
            progress["totalNodes"] += 1
            parent_fanout += 1
            added += 1

            # Non-terminal nodes with source files need further expansion
            if not child_node["terminal"] and child_node["filePath"]:
                progress["pendingNodes"].append(child_id)

    # Batch remove processed parents from pending (O(n) total vs O(n*m) with list.remove)
    completed = {r["nodeId"] for r in results.get("results", [])}
    progress["pendingNodes"] = [n for n in progress["pendingNodes"] if n not in completed]
    # Sync progress state with tree (avoids drift from reruns/reconcile-apply)
    # Every tree node that is not currently pending has been expanded
    all_nodes = set(tree["nodes"].keys())
    pending = set(progress["pendingNodes"])
    progress["expandedNodes"] = sorted(all_nodes - pending)
    progress["totalNodes"] = len(tree["nodes"])

    _save_json(tp, tree)
    _save_json(pp, progress)

    # Backfill domainInteraction from Phase 2 lookup
    filled = _backfill_domain_interaction(args.cache_dir, args.entry_id)

    summary = {
        "status": "merged",
        "entryId": args.entry_id,
        "added": added,
        "skippedDup": skipped_dup,
        "skippedFanout": skipped_fanout,
        "skippedMax": skipped_max,
        "totalNodes": progress["totalNodes"],
        "pendingNodes": len(progress["pendingNodes"]),
        "backfilledDI": filled,
    }
    print(json.dumps(summary, indent=2))


# ── Backfill domainInteraction ─────────────────────────────────────

def _backfill_domain_interaction(cache_dir, entry_id):
    """Fill missing domainInteraction from Phase 2 db-schema-lookup."""
    tp = _tree_path(cache_dir, entry_id)
    lookup_path = os.path.join(cache_dir, "phase1b", "db-schema-lookup.json")

    if not os.path.exists(lookup_path):
        return 0

    tree = _load_json(tp)
    lookup = _load_json(lookup_path).get("lookup", {})

    filled = 0
    for nid, node in tree["nodes"].items():
        if not node.get("terminal"):
            continue

        di = node.get("domainInteraction")
        # Fix incomplete DI: has fields but missing type
        if di and isinstance(di, dict) and not di.get("type"):
            if di.get("table") or di.get("operation"):
                di["type"] = "DATABASE"
                filled += 1
                continue

        if di:
            continue

        cls_short = node.get("class", "").split(".")[-1]
        method = node.get("method", "")
        lookup_key = f"{cls_short}.{method}"

        match = lookup.get(lookup_key)
        if match:
            node["domainInteraction"] = {
                "type": "DATABASE",
                "operation": match["operation"],
                "table": match["table"],
            }
            filled += 1

    if filled > 0:
        _save_json(tp, tree)

    return filled


def do_backfill(args):
    """Standalone backfill mode: fill domainInteraction for existing trees."""
    tp = _tree_path(args.cache_dir, args.entry_id)
    if not os.path.exists(tp):
        print(f"ERROR: tree not found: {tp}", file=sys.stderr)
        sys.exit(1)

    filled = _backfill_domain_interaction(args.cache_dir, args.entry_id)
    print(json.dumps({
        "status": "backfilled",
        "entryId": args.entry_id,
        "filled": filled,
    }, indent=2))


# ── LLM Backfill modes ────────────────────────────────────────────

def _resolve_file_path(class_name, project_dir, file_index=None):
    """Try to find .java file for a class. Uses pre-built index if available."""
    if not class_name:
        return ""
    short_name = class_name.rsplit('.', 1)[-1] + '.java'
    if file_index is not None:
        paths = file_index.get(short_name, [])
        return paths[0] if paths else ""
    if not project_dir:
        return ""
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', 'target', 'build')]
        if short_name in files:
            full = os.path.join(root, short_name)
            return os.path.relpath(full, project_dir).replace('\\', '/')
    return ""


def do_llm_backfill_prepare(args):
    """Scan all trees, collect nodes missing domainInteraction, build context for LLM."""
    phase2a_dir = os.path.join(args.cache_dir, "phase2a")
    project_dir = args.project_dir or ""

    # Build file index once for all lookups (avoids repeated os.walk)
    file_index = _build_file_index(project_dir) if project_dir else {}

    unique_missing = {}  # nodeId -> context

    for fname in sorted(os.listdir(phase2a_dir)):
        if not fname.endswith('-tree.json') or fname.startswith('_'):
            continue
        tree = _load_json(os.path.join(phase2a_dir, fname))
        entry_id = fname.replace('-tree.json', '')

        for nid, node in tree["nodes"].items():
            if not node.get("terminal"):
                continue
            if node.get("domainInteraction"):
                continue

            if nid not in unique_missing:
                parent_id = node.get("parentId", "")
                parent_node = tree["nodes"].get(parent_id, {})

                # Try to resolve missing filePath from class name
                file_path = node.get("filePath", "")
                if not file_path and file_index:
                    file_path = _resolve_file_path(node.get("class", ""), "", file_index)

                parent_file_path = parent_node.get("filePath", "") if parent_node else ""
                if not parent_file_path and file_index and parent_node:
                    parent_file_path = _resolve_file_path(
                        parent_node.get("class", ""), "", file_index)

                unique_missing[nid] = {
                    "nodeId": nid,
                    "class": node.get("class", ""),
                    "method": node.get("method", ""),
                    "filePath": file_path,
                    "parent_class": parent_node.get("class", "") if parent_node else "",
                    "parent_method": parent_node.get("method", "") if parent_node else "",
                    "parent_filePath": parent_file_path,
                    "affectedEntries": [entry_id],
                }
            else:
                unique_missing[nid]["affectedEntries"].append(entry_id)

    nodes = list(unique_missing.values())
    context = {
        "totalNodes": len(nodes),
        "nodes": nodes,
    }

    output_path = os.path.join(phase2a_dir, "tmp", "_llm-backfill-context.json")
    _save_json(output_path, context)

    print(json.dumps({
        "status": "prepared",
        "missingNodes": len(nodes),
        "contextPath": output_path,
    }, indent=2))


def do_llm_backfill_apply(args):
    """Read LLM subagent results and apply domainInteraction to all affected trees."""
    results = _load_json(args.results)
    phase2a_dir = os.path.join(args.cache_dir, "phase2a")

    # Load context to know which entries are affected
    context_path = os.path.join(phase2a_dir, "tmp", "_llm-backfill-context.json")
    if not os.path.exists(context_path):
        print("ERROR: context file not found. Run llm-backfill-prepare first.", file=sys.stderr)
        sys.exit(1)
    context = _load_json(context_path)

    # Build nodeId -> domainInteraction map from subagent results
    di_map = {}
    for r in results.get("results", []):
        nid = r.get("nodeId", "")
        di = r.get("domainInteraction")
        if nid and di:
            di_map[nid] = di

    # Group updates by entry
    entry_updates = {}  # entryId -> {nodeId: di}
    for node_ctx in context.get("nodes", []):
        nid = node_ctx["nodeId"]
        if nid in di_map:
            for entry_id in node_ctx["affectedEntries"]:
                entry_updates.setdefault(entry_id, {})[nid] = di_map[nid]

    # Apply to each tree
    total_applied = 0
    for entry_id, updates in entry_updates.items():
        tp = _tree_path(args.cache_dir, entry_id)
        if not os.path.exists(tp):
            continue
        tree = _load_json(tp)
        for nid, di in updates.items():
            if nid in tree["nodes"] and not tree["nodes"][nid].get("domainInteraction"):
                tree["nodes"][nid]["domainInteraction"] = di
                total_applied += 1
        _save_json(tp, tree)

    print(json.dumps({
        "status": "applied",
        "totalApplied": total_applied,
        "affectedTrees": len(entry_updates),
    }, indent=2))


# ── Reconcile modes ────────────────────────────────────────────────

# Suspicious method name patterns for zero-call heuristic
_SUSPICIOUS_PATTERNS = ("process", "handle", "execute", "valid", "check", "query", "search", "send", "write", "create", "save", "update", "delete")
_SAFE_PATTERNS = ("build", "convert", "populate", "transform", "format", "tostring", "hashcode", "equals", "get", "set", "is", "sm4", "encrypt", "decrypt")


def _classify_zero_call(node):
    """Classify zero-call suspicion level: HIGH/MEDIUM/LOW/SKIP."""
    method = node.get("method", "").lower()
    for p in _SAFE_PATTERNS:
        if method.startswith(p):
            return "LOW"
    for p in _SUSPICIOUS_PATTERNS:
        if method.startswith(p) or p in method:
            return "MEDIUM"
    return "SKIP"


def do_reconcile_prepare(args):
    """Scan all trees for shared-node inconsistencies and zero-call suspicious nodes."""
    phase2a_dir = os.path.join(args.cache_dir, "phase2a")

    # Load all trees once and cache them
    trees = {}  # entryId -> tree dict
    for fname in sorted(os.listdir(phase2a_dir)):
        if not fname.endswith('-tree.json') or fname.startswith('_'):
            continue
        tree = _load_json(os.path.join(phase2a_dir, fname))
        entry_id = tree.get("entryId", fname.replace('-tree.json', ''))
        trees[entry_id] = tree

    # Build child index for each tree: parentId -> set of child nodeIds
    child_index = {}  # entryId -> {parentId: set(childIds)}
    for entry_id, tree in trees.items():
        idx = {}
        for nid, node in tree["nodes"].items():
            pid = node.get("parentId")
            if pid:
                idx.setdefault(pid, set()).add(nid)
        child_index[entry_id] = idx

    # Collect all nodes across all trees, grouped by nodeId
    node_map = {}  # nodeId -> [{entryId, node}]
    for entry_id, tree in trees.items():
        for nid, node in tree["nodes"].items():
            node_map.setdefault(nid, []).append({
                "entryId": entry_id,
                "node": node,
            })

    inconsistencies = []
    zero_call_suspicious = []
    type_counts = {
        "TERMINAL_MISMATCH": 0,
        "CHILDREN_COUNT_MISMATCH": 0,
        "CHILDREN_SET_MISMATCH": 0,
    }

    for nid, occurrences in node_map.items():
        if len(occurrences) < 2:
            continue

        # Gather per-entry expansion info using cached child index
        expansions = {}
        for occ in occurrences:
            entry_id = occ["entryId"]
            node = occ["node"]
            children_ids = child_index.get(entry_id, {}).get(nid, set())
            expansions[entry_id] = {
                "terminal": node.get("terminal", False),
                "childCount": len(children_ids),
                "children": sorted(children_ids),
            }

        # Check for inconsistency
        values = list(expansions.values())

        # Type A: terminal mismatch
        terminals = set(v["terminal"] for v in values)
        if len(terminals) > 1:
            inc_type = "TERMINAL_MISMATCH"
        # Type B: children count mismatch
        elif len(set(v["childCount"] for v in values)) > 1:
            inc_type = "CHILDREN_COUNT_MISMATCH"
        # Type C: children set mismatch (same count but different ids)
        elif values[0]["childCount"] > 0 and len(set(tuple(v["children"]) for v in values)) > 1:
            inc_type = "CHILDREN_SET_MISMATCH"
        else:
            # Consistent, skip
            continue

        type_counts[inc_type] += 1

        # Find the "best" expansion (most children)
        best_entry = max(expansions, key=lambda e: expansions[e]["childCount"])

        inconsistencies.append({
            "nodeId": nid,
            "type": inc_type,
            "details": {
                "expansions": expansions,
                "bestEntry": best_entry,
            },
            "filePath": occurrences[0]["node"].get("filePath", ""),
            "class": occurrences[0]["node"].get("class", ""),
            "method": occurrences[0]["node"].get("method", ""),
            "needReAnalysis": True,
        })

    # Zero-call suspicious nodes (non-terminal, has filePath, 0 children)
    for nid, occurrences in node_map.items():
        for occ in occurrences:
            node = occ["node"]
            if node.get("terminal"):
                continue
            if not node.get("filePath"):
                continue
            entry_id = occ["entryId"]
            children = child_index.get(entry_id, {}).get(nid, set())
            if children:
                continue

            suspicion = _classify_zero_call(node)
            if suspicion == "SKIP":
                continue

            # Deduplicate: only report once per nodeId
            if any(z["nodeId"] == nid for z in zero_call_suspicious):
                continue

            zero_call_suspicious.append({
                "nodeId": nid,
                "filePath": node.get("filePath", ""),
                "class": node.get("class", ""),
                "method": node.get("method", ""),
                "suspicion": suspicion,
                "affectedEntries": [occ["entryId"]],
            })

    # Merge affected entries for duplicate zero-call nodes
    for i, zc in enumerate(zero_call_suspicious):
        for occ in node_map.get(zc["nodeId"], []):
            entry_id = occ["entryId"]
            if entry_id not in zc["affectedEntries"]:
                zc["affectedEntries"].append(entry_id)

    report = {
        "version": "1.0",
        "totalSharedNodes": sum(1 for v in node_map.values() if len(v) >= 2),
        "inconsistentNodes": len(inconsistencies),
        "inconsistencyByType": type_counts,
        "zeroCallSuspiciousCount": len(zero_call_suspicious),
        "inconsistencies": inconsistencies,
        "zeroCallSuspicious": zero_call_suspicious,
    }

    output_path = os.path.join(phase2a_dir, "tmp", "_reconcile-report.json")
    _save_json(output_path, report)

    print(json.dumps({
        "status": "prepared",
        "totalSharedNodes": report["totalSharedNodes"],
        "inconsistentNodes": len(inconsistencies),
        "inconsistencyByType": type_counts,
        "zeroCallSuspiciousCount": len(zero_call_suspicious),
        "reportPath": output_path,
    }, indent=2))


def _collect_subtree(tree, node_id, child_index=None):
    """Collect all descendant nodeIds of a given node (not including itself)."""
    if child_index is None:
        child_index = _build_child_index(tree["nodes"])
    descendants = []
    queue = [node_id]
    while queue:
        current = queue.pop(0)
        for child_id in child_index.get(current, []):
            if child_id not in descendants:
                descendants.append(child_id)
                queue.append(child_id)
    return descendants


def do_reconcile_apply(args):
    """Apply re-analyzed results to fix inconsistencies and zero-call suspicious nodes."""
    phase2a_dir = os.path.join(args.cache_dir, "phase2a")

    # Load report
    report_path = args.report or os.path.join(phase2a_dir, "tmp", "_reconcile-report.json")
    if not os.path.exists(report_path):
        print("ERROR: _reconcile-report.json not found. Run reconcile-prepare first.", file=sys.stderr)
        sys.exit(1)
    report = _load_json(report_path)

    # Load all re-analysis results from directory
    results_dir = args.results or os.path.join(phase2a_dir, "tmp")
    re_analysis = {}  # nodeId -> normalized calls
    if os.path.isdir(results_dir):
        for fname in sorted(os.listdir(results_dir)):
            if not fname.startswith("_reconcile-result-") or not fname.endswith(".json"):
                continue
            data = _load_json(os.path.join(results_dir, fname))
            data = _normalize_results(data)
            for item in data.get("results", []):
                re_analysis[item["nodeId"]] = item.get("calls", [])
    elif os.path.isfile(results_dir):
        data = _load_json(results_dir)
        data = _normalize_results(data)
        for item in data.get("results", []):
            re_analysis[item["nodeId"]] = item.get("calls", [])

    # ISSUE-2a-21: 兜底从 bestEntry 复制子节点，避免不必要的 LLM 重分析
    for inc in report.get("inconsistencies", []):
        nid = inc["nodeId"]
        if nid in re_analysis:
            continue  # LLM 重分析结果优先
        best_entry_id = inc.get("details", {}).get("bestEntry")
        if not best_entry_id:
            continue
        best_tree_path = os.path.join(phase2a_dir, f"{best_entry_id}-tree.json")
        if not os.path.exists(best_tree_path):
            continue
        best_tree = _load_json(best_tree_path)
        calls = []
        for child_node in best_tree["nodes"].values():
            if child_node.get("parentId") == nid:
                calls.append({
                    "targetClass": child_node.get("class", ""),
                    "targetMethod": child_node.get("method", ""),
                    "targetFilePath": child_node.get("filePath", ""),
                    "callType": child_node.get("callType", "DIRECT"),
                    "isEndpoint": child_node.get("terminal", False),
                    "endpointType": "DISPATCH" if child_node.get("patternRef") else None,
                    "patternRef": child_node.get("patternRef"),
                    "domainInteraction": child_node.get("domainInteraction"),
                    "targetPackage": child_node.get("package", ""),
                })
        if calls:
            re_analysis[nid] = calls

    if not re_analysis:
        print(json.dumps({
            "status": "no-results",
            "message": "No re-analysis results found. Nothing to apply.",
        }, indent=2))
        return

    # Collect all nodeIds that need fixing (inconsistencies + zero-call suspicious)
    nodes_to_fix = set()
    for inc in report.get("inconsistencies", []):
        nodes_to_fix.add(inc["nodeId"])
    for zc in report.get("zeroCallSuspicious", []):
        if zc.get("suspicion") == "MEDIUM":
            nodes_to_fix.add(zc["nodeId"])

    # Load all trees once with child index
    trees = {}
    for fname in sorted(os.listdir(phase2a_dir)):
        if not fname.endswith('-tree.json') or fname.startswith('_'):
            continue
        tp = os.path.join(phase2a_dir, fname)
        entry_id = fname.replace('-tree.json', '')
        tree_data = _load_json(tp)
        trees[entry_id] = {
            "path": tp, "tree": tree_data, "dirty": False,
            "child_index": _build_child_index(tree_data["nodes"]),
        }

    affected_trees = {}  # entryId -> set of fixed nodeIds
    total_fixed = 0
    total_removed = 0
    total_added = 0

    for nid in nodes_to_fix:
        if nid not in re_analysis:
            continue

        authoritative_calls = re_analysis[nid]

        # Build expected child nodeIds
        expected_child_ids = set()
        for call in authoritative_calls:
            expected_child_ids.add(_resolve_call_id(call))

        for entry_id, td in trees.items():
            tree = td["tree"]
            if nid not in tree["nodes"]:
                continue

            node = tree["nodes"][nid]
            old_children = td["child_index"].get(nid, [])
            old_children_set = set(old_children)

            # Skip if already matches authoritative result
            if old_children_set == expected_child_ids:
                continue

            # Remove old subtree
            descendants = _collect_subtree(tree, nid, td["child_index"])
            for did in descendants:
                if did in tree["nodes"]:
                    del tree["nodes"][did]
                    total_removed += 1
            for cid in old_children:
                if cid in tree["nodes"]:
                    del tree["nodes"][cid]
                    total_removed += 1

            # Fix parent node (preserve DISPATCH semantics)
            if not node.get("patternRef"):
                node["terminal"] = False
                node["layerType"] = "INTERNAL"

            # Re-create children from authoritative calls
            pp_path = os.path.join(phase2a_dir, f"{entry_id}-progress.json")
            if not os.path.exists(pp_path):
                print(f"WARNING: progress not found for {entry_id}, skipping pending update", file=sys.stderr)
                progress = None
            else:
                progress = _load_json(pp_path)

            parent_fanout = 0
            max_fanout = progress.get("maxFanout", 10) if progress else 10
            for call in authoritative_calls:
                # Fanout check (fixes ISSUE-2a-33)
                if parent_fanout >= max_fanout:
                    break

                max_depth = progress.get("maxDepth") if progress else None
                child_node = _create_child_node(call, node, max_depth=max_depth)

                # Dedup within tree
                if child_node["nodeId"] in tree["nodes"]:
                    continue

                tree["nodes"][child_node["nodeId"]] = child_node
                total_added += 1
                parent_fanout += 1

                # Non-terminal nodes with source files need further expansion
                if progress and not child_node["terminal"]:
                    if child_node["nodeId"] not in progress.get("pendingNodes", []):
                        progress["pendingNodes"].append(child_node["nodeId"])

            td["child_index"] = _build_child_index(tree["nodes"])
            td["dirty"] = True
            affected_trees.setdefault(entry_id, set()).add(nid)
            total_fixed += 1

            if progress:
                # Sync progress state with tree (same logic as merge mode)
                all_nodes = set(td["tree"]["nodes"].keys())
                pending = set(progress["pendingNodes"])
                progress["expandedNodes"] = sorted(all_nodes - pending)
                progress["totalNodes"] = len(td["tree"]["nodes"])
                progress["pendingNodes"] = [n for n in progress["pendingNodes"] if n in all_nodes]
                _save_json(pp_path, progress)

    # Save all dirty trees and re-run backfill
    for entry_id, td in trees.items():
        if td["dirty"]:
            _save_json(td["path"], td["tree"])
            _backfill_domain_interaction(args.cache_dir, entry_id)

    print(json.dumps({
        "status": "reconciled",
        "fixedNodes": total_fixed,
        "affectedTrees": {eid: len(nids) for eid, nids in affected_trees.items()},
        "removedNodes": total_removed,
        "addedNodes": total_added,
    }, indent=2))


# ── Main ────────────────────────────────────────────────────────────

MODES = {
    "init": do_init,
    "next-batch": do_next_batch,
    "merge": do_merge,
    "backfill": do_backfill,
    "llm-backfill-prepare": do_llm_backfill_prepare,
    "llm-backfill-apply": do_llm_backfill_apply,
    "reconcile-prepare": do_reconcile_prepare,
    "reconcile-apply": do_reconcile_apply,
}


def main():
    args = parse_args()
    args.cache_dir = os.path.abspath(args.cache_dir)
    if hasattr(args, 'project_dir') and args.project_dir:
        args.project_dir = os.path.abspath(args.project_dir)
    fn = MODES.get(args.mode)
    if fn:
        fn(args)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
