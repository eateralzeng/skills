"""Phase 2a: 调用树展开（DAG 版，决策 12）

nodes + edges 分离的 DAG 调用树展开。LLM 子代理读源码发现方法调用（discover），
脚本管理 tree.json（nodes 单实例 + edges 多 parent）+ progress.json（BFS 进度）。

设计来源：phase2a-design.md（13 决策 + 8 mode 流程 + schema）。
本文件含 Commit 2a 的 3 个 mode：init / next-batch / merge。
reconcile-* / llm-backfill-* mode 见 Commit 2b。

依赖：scripts/_tree_graph.py（TreeGraph + gen_short_id）。
"""
import json
import os
import argparse
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tree_graph import TreeGraph, gen_short_id
from _rmb_topic import extract_rmb_topic_by_method  # 共享 RMB topic 提取（rmb-topic-backfill）


# ── nodeId 构造（与 phase1a 对齐，design 5.1）─────────────────────────

def build_node_id(fpath, method_name, target_class=None):
    """nodeId = 模块名:包名.类名:方法名。

    有 filePath 时与 phase1a_entry_scan.build_node_id 逻辑完全一致（保证跨阶段
    nodeId 对齐 —— 节点去重的前提）。无 filePath 时降级为 2 段（design 5.1 fallback）。

    e.g. cbrc-bs-jrp:com.webank.cbrc.jrp.handler.teller.SomeHandler:doJob
    """
    if fpath:
        parts = fpath.replace('\\', '/').split('/')
        module = parts[0] if parts else ''
        marker = 'src/main/java/'
        marker_idx = fpath.find(marker)
        if marker_idx >= 0:
            pkg_path = fpath[marker_idx + len(marker):]
            pkg_path = pkg_path.rsplit('/', 1)[0]  # 去文件名
            pkg = pkg_path.replace('/', '.')
        else:
            pkg = ''
        cls = os.path.basename(fpath).replace('.java', '')
        full_class = f'{pkg}.{cls}' if pkg else cls
        return f'{module}:{full_class}:{method_name}'
    # fallback：无 filePath（外部依赖节点），用 target_class 降级为 2 段
    if target_class:
        print(f"WARN: no filePath for {target_class}.{method_name}, "
              f"fallback to 2-segment nodeId", file=sys.stderr)
        return f"{target_class}:{method_name}"
    print(f"WARN: cannot build nodeId for {method_name}", file=sys.stderr)
    return f"unknown:{method_name}"


def _full_class_from_node_id(node_id):
    """nodeId = module:pkg.class:method → 返回 pkg.class（全限定类名）。"""
    parts = node_id.split(":")
    return parts[1] if len(parts) >= 2 else ""


def _package_from_node_id(node_id):
    """从 nodeId 解析包名。"""
    fc = _full_class_from_node_id(node_id)
    return fc.rsplit(".", 1)[0] if "." in fc else ""


# ── 文件路径与 IO helper ──────────────────────────────────────────────

def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: JSON 文件损坏 {path}: {e}", file=sys.stderr)
        raise


def _load_subagent_results(path):
    """加载子代理输出，兼容 {results:[...]} / 裸数组 [...] / 异常格式（ISSUE-2a-34）。

    子代理偶发不按 prompt 包 {results: [...]}（输出裸数组或非对象），此处统一容错：
    dict → 取 results；list → 当作 results；其他 → 告警 + 空。
    """
    data = _load_json(path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("results", [])
    print(f"WARN: 子代理输出格式异常（{type(data).__name__}），当作空 results: {path}", file=sys.stderr)
    return []


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _tree_path(cache_dir, entry_id):
    return os.path.join(cache_dir, "phase2a", f"{entry_id}-tree.json")


def _progress_path(cache_dir, entry_id):
    return os.path.join(cache_dir, "phase2a", f"{entry_id}-progress.json")


# ── BFS 算法 helper ───────────────────────────────────────────────────

def _node_layer(tg, node_id):
    """节点 layer = min(入边 layer)；root 无入边 → 0（design 5.7 / 4.3.2.1）。"""
    parents = tg.parents(node_id)
    if not parents:
        return 0
    return min(e["layer"] for e in parents)


def _normalize_calls_order(calls, source_code):
    """保证 calls 按源码顺序（决策 13，design 5.2）。

    有 sourceLine → 校验 sourceSnippet 一致性 + 按行号排序（不信任 LLM 原始顺序）；
    无 sourceLine → 降级用 LLM 顺序，记 WARN。
    """
    if not calls:
        return calls
    has_lines = all(c.get("sourceLine") for c in calls)
    if has_lines:
        source_lines = source_code.split('\n') if source_code else []
        for call in calls:
            line_no = call["sourceLine"]
            # VERIFY-02：snippet mismatch 校验移除——继承场景（方法体在父类、节点在
            # 子类）下 parent.filePath 行号对不上父类方法，大量误报。仅保留 sourceLine
            # 越界检查（明显错误仍告警）+ 排序。
            if not (0 < line_no <= len(source_lines)):
                print(f"WARN: sourceLine {line_no} 超出 parent 源码范围 (共 {len(source_lines)} 行)",
                      file=sys.stderr)
        return sorted(calls, key=lambda c: c["sourceLine"])
    print("WARN: calls missing sourceLine, falling back to LLM order", file=sys.stderr)
    return calls


def _read_source(project_dir, file_path):
    """按 parent.filePath 读项目源码（merge snippet 校验用，拍板点①）。读不到返回 ''。"""
    if not file_path or not project_dir:
        return ''
    full = os.path.join(project_dir, file_path)
    if not os.path.exists(full):
        print(f"WARN: source not found: {full}", file=sys.stderr)
        return ''
    with open(full) as f:
        return f.read()


def _create_node_from_call(call, existing_short_ids):
    """从子代理 call 构造 node 静态信息（含 gen_short_id）。"""
    target_class = call.get("targetClass", "")
    target_method = call.get("targetMethod", "")
    target_file = call.get("targetFilePath", "")
    node_id = build_node_id(target_file, target_method, target_class)

    short_id = gen_short_id(node_id, existing_short_ids)
    existing_short_ids.add(short_id)

    return {
        "shortId": short_id,
        "nodeId": node_id,
        "class": target_class,
        "method": target_method,
        "package": call.get("targetPackage", ""),
        "filePath": target_file,
        "terminal": bool(call.get("isEndpoint")),
        "description": "",
        "domainInteraction": call.get("domainInteraction"),
        "patternRef": call.get("patternRef"),
        "endpointType": call.get("endpointType"),
    }


def _backfill_di_via_lookup(tg, lookup):
    """DI lookup 兜底（design 4.3.2.4）：terminal 且 DI 为空的节点，用 短类名.方法名
    查 db-schema-lookup 补 DATABASE 类型 domainInteraction。返回补全数。"""
    if not lookup:
        return 0
    filled = 0
    for node_id, node in tg.nodes.items():
        if node.get("terminal") and not node.get("domainInteraction"):
            cls = (node.get("class") or "").split(".")[-1]  # 短类名
            method = node.get("method", "")
            hit = lookup.get(f"{cls}.{method}")
            if hit:
                node["domainInteraction"] = {
                    "type": "DATABASE",
                    "operation": hit.get("operation"),
                    "table": hit.get("table"),
                }
                filled += 1
    return filled


def _resolve_rmb_topic_constants(tg, constants):
    """解析 RMB 发送端 @RmbTopic 的常量引用（VERIFY-01）。

    phase2a-discover 子代理输出 routingKeys.topic 的原始表达式（常量引用如
    CbrcBsJrpRmbApi.SZ_COURT_REQUEST_RECEIVE，或字面值）。本函数对常量引用
    （含 '.' 的表达式）用 phase1a constants.json 解析为字面值，使发送端 topic
    与接收端 entry.rmbTopic（phase1a 已解析）一致 → phase4 桥接可匹配。
    """
    if not constants:
        return 0
    resolved = 0
    for node_id, node in tg.nodes.items():
        di = node.get("domainInteraction") or {}
        if di.get("type") == "EXTERNAL" and di.get("protocol") == "RMB":
            rk = di.get("routingKeys") or {}
            topic = rk.get("topic", "")
            # 常量引用格式：含 '.'（字面值如 sz-court-request-receive 无 '.'）
            if topic and "." in topic and not topic.startswith("["):
                name = topic.strip().split(".")[-1]
                if name in constants:
                    literal = constants[name]
                    rk["topic"] = literal
                    di["routingKeys"] = rk
                    di["target"] = literal  # 同步 target（phase4 fallback 用）
                    node["domainInteraction"] = di
                    resolved += 1
                else:
                    print(f"WARN: unresolved RMB topic constant: {topic}", file=sys.stderr)
    return resolved


# ── mode: init ───────────────────────────────────────────────────────

def do_init(args):
    cache_dir = os.path.abspath(args.cache_dir)
    entry_id = args.entry_id

    tree_p = _tree_path(cache_dir, entry_id)
    prog_p = _progress_path(cache_dir, entry_id)
    if os.path.exists(tree_p) or os.path.exists(prog_p):
        print(f"Error: phase2a data already exists for {entry_id} (rm tree/progress to re-init)",
              file=sys.stderr)
        sys.exit(1)

    entries = _load_json(args.entry)
    entry = next((e for e in entries["entries"] if e["id"] == entry_id), None)
    if not entry:
        print(f"Error: entry {entry_id} not found in {args.entry}", file=sys.stderr)
        sys.exit(1)

    root_node_id = entry["nodeId"]  # design 决策 7
    root_node = {
        "shortId": gen_short_id(root_node_id, set()),
        "nodeId": root_node_id,
        "class": _full_class_from_node_id(root_node_id),  # 全限定，对齐 node schema + child.class
        "method": entry.get("methodName", ""),
        "package": _package_from_node_id(root_node_id),
        "filePath": entry.get("filePath", ""),
        "terminal": False,
        "description": "",
        "domainInteraction": None,
        "patternRef": None,
        "endpointType": None,
    }

    tree = {
        "version": "2.0",
        "edgeKeyFormat": "nodeId",
        "entryId": entry_id,
        "entryType": entry.get("type"),
        "rootNodeId": root_node_id,
        "nodes": {root_node_id: root_node},
        "edges": [],
    }
    progress = {
        "entryId": entry_id,
        "expandedNodes": [],
        "pendingNodes": [root_node_id],
        "totalNodes": 1,
        "totalEdges": 0,
        "maxDepth": args.max_depth,
        "maxNodes": args.max_nodes,
        "maxFanout": args.max_fanout,
    }

    _save_json(tree_p, tree)
    _save_json(prog_p, progress)
    print(json.dumps({
        "status": "initialized",
        "entryId": entry_id,
        "rootNodeId": root_node_id,
        "tree": tree_p,
        "progress": prog_p,
    }, indent=2, ensure_ascii=False))


# ── mode: next-batch ─────────────────────────────────────────────────

def do_next_batch(args):
    cache_dir = os.path.abspath(args.cache_dir)
    entry_id = args.entry_id

    tree = _load_json(_tree_path(cache_dir, entry_id))
    progress = _load_json(_progress_path(cache_dir, entry_id))
    tg = TreeGraph(tree)

    pending = progress["pendingNodes"]
    batch_ids = pending[:args.batch_size]

    batch = []
    for nid in batch_ids:
        node = tg.get_node(nid) or {}
        batch.append({
            "nodeId": nid,
            "class": node.get("class", ""),
            "method": node.get("method", ""),
            "filePath": node.get("filePath", ""),
            "layer": _node_layer(tg, nid),
        })

    remaining = pending[args.batch_size:]
    print(json.dumps({
        "batch": batch,
        "hasMore": len(remaining) > 0,
        "remainingCount": len(remaining),
    }, indent=2, ensure_ascii=False))


# ── mode: merge ──────────────────────────────────────────────────────

def do_merge(args):
    cache_dir = os.path.abspath(args.cache_dir)
    entry_id = args.entry_id

    tree = _load_json(_tree_path(cache_dir, entry_id))
    progress = _load_json(_progress_path(cache_dir, entry_id))
    results = _load_subagent_results(args.results)

    # db-schema-lookup（可选，DI 兜底用）
    lookup = {}
    lookup_path = os.path.join(cache_dir, "phase1b", "db-schema-lookup.json")
    if os.path.exists(lookup_path):
        lookup = _load_json(lookup_path).get("lookup", {})

    # phase1a constants（VERIFY-01：解析 RMB 发送端 @RmbTopic 常量引用）
    constants = {}
    constants_path = os.path.join(cache_dir, "phase1a", "constants.json")
    if os.path.exists(constants_path):
        constants = _load_json(constants_path)

    tg = TreeGraph(tree)
    max_depth = progress["maxDepth"]
    max_nodes = progress["maxNodes"]
    max_fanout = progress["maxFanout"]

    existing_short_ids = {n.get("shortId") for n in tg.nodes.values() if n.get("shortId")}

    # results 已通过上方 _load_subagent_results 加载（ISSUE-2a-34 容错）
    merged_parents = 0
    new_nodes = 0
    new_edges = 0

    for parent_result in results:
        parent_id = parent_result.get("nodeId")
        if parent_id not in tg.nodes:
            print(f"WARN: parent {parent_id} not in tree, skip", file=sys.stderr)
            continue

        parent_node = tg.nodes[parent_id]
        parent_layer = _node_layer(tg, parent_id)
        source_code = _read_source(args.project_dir, parent_node.get("filePath"))

        # 决策 13 保序
        ordered_calls = _normalize_calls_order(parent_result.get("calls", []), source_code)

        for call in ordered_calls:
            child_id = build_node_id(
                call.get("targetFilePath", ""),
                call.get("targetMethod", ""),
                call.get("targetClass", ""),
            )
            child_layer = parent_layer + 1

            # 控制参数检查（design 5.5 / 4.3.2.4）
            if len(tg.children(parent_id)) >= max_fanout:
                print(f"WARN: maxFanout reached at {parent_id}, skip call to {child_id}",
                      file=sys.stderr)
                continue
            if child_id not in tg.nodes and len(tg.nodes) >= max_nodes:
                print(f"WARN: maxNodes reached, skip new node {child_id}", file=sys.stderr)
                continue

            # 创建 node（单实例去重，决策 2）
            if child_id not in tg.nodes:
                child_node = _create_node_from_call(call, existing_short_ids)
                if child_layer >= max_depth:           # maxDepth 强制 terminal（design 4.2/5.5）
                    child_node["terminal"] = True
                tg.nodes[child_id] = child_node
                new_nodes += 1
            else:
                child_node = tg.nodes[child_id]

            # 创建 edge（决策 2 核心：node 去重但 edge 照建，修复调用链丢失）
            edge = {
                "id": f"e{len(tg.edges)}",
                "from": parent_id,
                "to": child_id,
                "layer": child_layer,
                "callType": call.get("callType", "DIRECT"),
                "condition": call.get("condition", "始终执行"),
                "sourceLine": call.get("sourceLine"),
                "truncated": child_layer >= max_depth,  # maxDepth 截断标记（决策 4）
            }
            if tg.add_edge(edge):
                new_edges += 1

            # 非 terminal 且有 filePath → 加入 pending 继续展开
            if not child_node.get("terminal") and child_node.get("filePath"):
                if (child_id not in progress["pendingNodes"]
                        and child_id not in progress["expandedNodes"]):
                    progress["pendingNodes"].append(child_id)

        # parent 已处理
        if parent_id in progress["pendingNodes"]:
            progress["pendingNodes"].remove(parent_id)
        if parent_id not in progress["expandedNodes"]:
            progress["expandedNodes"].append(parent_id)
        merged_parents += 1

    # DI lookup 兜底（DB 类型补全）
    filled = _backfill_di_via_lookup(tg, lookup)

    # RMB topic 常量引用解析（VERIFY-01：发送端 topic 与接收端对齐）
    rmb_resolved = _resolve_rmb_topic_constants(tg, constants)

    # 同步 progress（design 4.3.2.4）
    progress["totalNodes"] = len(tg.nodes)
    progress["totalEdges"] = len(tg.edges)

    _save_json(_tree_path(cache_dir, entry_id), tree)
    _save_json(_progress_path(cache_dir, entry_id), progress)

    print(json.dumps({
        "status": "merged",
        "entryId": entry_id,
        "mergedParents": merged_parents,
        "newNodes": new_nodes,
        "newEdges": new_edges,
        "diBackfilled": filled,
        "totalNodes": len(tg.nodes),
        "totalEdges": len(tg.edges),
        "pendingRemaining": len(progress["pendingNodes"]),
    }, indent=2, ensure_ascii=False))


# ── reconcile / backfill helper（Commit 2b）──────────────────────────

_ZERO_CALL_MEDIUM_PREFIXES = ("process", "handle", "execute", "check", "query",
                               "do", "send", "save", "update", "create", "delete")
_ZERO_CALL_LOW_PREFIXES = ("build", "convert", "to", "from", "of", "parse", "format")


def _classify_zero_call(method_name):
    """零调用可疑节点启发式分类（design 4.3.3.1）。返回 MEDIUM / LOW / SKIP。"""
    m = (method_name or "").lower()
    if any(m.startswith(p) for p in _ZERO_CALL_MEDIUM_PREFIXES):
        return "MEDIUM"
    if any(m.startswith(p) for p in _ZERO_CALL_LOW_PREFIXES):
        return "LOW"
    return "SKIP"


def _remove_subtree_with_refcount(tg, node_id, _root=None, _visited=None):
    """引用计数删子树（design 4.3.3.3，DAG 单实例新复杂度）。

    删 node_id 所有出边；to 节点若还有其他入边（引用计数>0）→ 只删 edge、保留 node；
    引用计数=0 → 递归删其出边 + 删 node。防止误删被其他路径共享的节点。

    环保护（ISSUE-2a-41）：含环（自环/互调，ISSUE-2a-38）时引用计数沿环回到被修节点自身
    会误删它致悬空 from 边。`_root` = 被 reconcile 的节点，永不删除；`_visited` 防环重入。
    """
    if _root is None:
        _root = node_id
    if _visited is None:
        _visited = set()
    # 出边无条件全删（不能因 visited 提前 return，否则 pop 后残留悬空 from 边，ISSUE-2a-41）
    for edge in list(tg.children(node_id)):
        child_id = edge["to"]
        tg.edges.remove(edge)
        tg._outgoing[node_id] = [e for e in tg._outgoing.get(node_id, []) if e is not edge]
        tg._incoming[child_id] = [e for e in tg._incoming.get(child_id, []) if e is not edge]
        # 非被修节点 + 未访问 + 引用计数 0 → 递归删；visited 仅拦递归/pop（防环），不阻断删边
        if child_id != _root and child_id not in _visited and not tg.parents(child_id):
            _visited.add(child_id)
            _remove_subtree_with_refcount(tg, child_id, _root, _visited)
            tg.nodes.pop(child_id, None)


def _search_java_file(project_dir, full_class):
    """filePath 为空时，用 class 全限定名搜 src/main/java 下的 .java 反推（design 4.3.4.1）。"""
    if not project_dir or not full_class:
        return ""
    rel = "src/main/java/" + full_class.replace(".", "/") + ".java"
    for root, _, _ in os.walk(project_dir):
        candidate = os.path.join(root, rel)
        if os.path.exists(candidate):
            return os.path.relpath(candidate, project_dir)
    return ""


def _edges_to_calls(edges, tg):
    """从 edges 反推 calls（reconcile-apply 从 bestEntry 兜底复制用）。"""
    calls = []
    for e in edges:
        child = tg.get_node(e["to"]) or {}
        calls.append({
            "targetClass": child.get("class", ""), "targetMethod": child.get("method", ""),
            "targetFilePath": child.get("filePath", ""), "targetPackage": child.get("package", ""),
            "callType": e.get("callType", "DIRECT"), "condition": e.get("condition", "始终执行"),
            "sourceLine": e.get("sourceLine"), "isEndpoint": child.get("terminal", False),
            "endpointType": child.get("endpointType"), "domainInteraction": child.get("domainInteraction"),
            "patternRef": child.get("patternRef"),
        })
    return calls


# ── mode: reconcile-prepare（扫描跨 entry 不一致）────────────────────

def do_reconcile_prepare(args):
    cache_dir = os.path.abspath(args.cache_dir)
    phase2a_dir = os.path.join(cache_dir, "phase2a")

    # nodeId → [(entry_id, node, tg)]
    node_map = {}
    for fn in sorted(os.listdir(phase2a_dir)):
        if not fn.endswith("-tree.json"):
            continue
        entry_id = fn[:-len("-tree.json")]
        tg = TreeGraph(_load_json(os.path.join(phase2a_dir, fn)))
        for nid, node in tg.nodes.items():
            node_map.setdefault(nid, []).append((entry_id, node, tg))

    inconsistencies, zero_call = [], []

    for nid, occ in node_map.items():
        # 零调用可疑（每个 entry 都查）
        for entry_id, node, tg in occ:
            if (not node.get("terminal") and node.get("filePath")
                    and not tg.children(nid)):
                if _classify_zero_call(node.get("method", "")) == "MEDIUM":
                    zero_call.append({"nodeId": nid, "entryId": entry_id,
                                      "suspicion": "MEDIUM", "needReAnalysis": True})
        # 不一致（仅多 entry 节点）
        if len(occ) < 2:
            continue
        terminals = {o[1].get("terminal") for o in occ}
        cs = [(o[0], sorted(e["to"] for e in o[2].children(nid))) for o in occ]
        best_entry = max(cs, key=lambda x: len(x[1]))[0]
        if len(terminals) > 1:
            kind = "TERMINAL_MISMATCH"
        elif len({len(c[1]) for c in cs}) > 1:
            kind = "CHILDREN_COUNT_MISMATCH"
        elif len({tuple(c[1]) for c in cs}) > 1:
            kind = "CHILDREN_SET_MISMATCH"
        else:
            continue
        inconsistencies.append({"nodeId": nid, "type": kind,
                                "bestEntry": best_entry, "needReAnalysis": True})

    out = os.path.join(phase2a_dir, "tmp", "_reconcile-report.json")
    _save_json(out, {"inconsistencies": inconsistencies, "zeroCallSuspicious": zero_call})
    print(json.dumps({"status": "prepared", "report": out,
                      "inconsistencies": len(inconsistencies),
                      "zeroCallSuspicious": len(zero_call),
                      "totalReAnalysis": len(inconsistencies) + len(zero_call)},
                     indent=2, ensure_ascii=False))


# ── mode: reconcile-apply（引用计数删子树 + 按权威 calls 重建）───────

def do_reconcile_apply(args):
    cache_dir = os.path.abspath(args.cache_dir)
    phase2a_dir = os.path.join(cache_dir, "phase2a")
    report_path = args.report or os.path.join(phase2a_dir, "tmp", "_reconcile-report.json")
    report = _load_json(report_path)

    # 收集 LLM 重分析结果（_reconcile-result-*.json）
    re_analysis = {}
    tmp_dir = os.path.join(phase2a_dir, "tmp")
    if os.path.isdir(tmp_dir):
        for fn in sorted(os.listdir(tmp_dir)):
            if fn.startswith("_reconcile-result-") and fn.endswith(".json"):
                for r in _load_subagent_results(os.path.join(tmp_dir, fn)):
                    re_analysis[r["nodeId"]] = r.get("calls", [])

    nodes_to_fix = ({i["nodeId"] for i in report.get("inconsistencies", [])}
                    | {z["nodeId"] for z in report.get("zeroCallSuspicious", [])})
    best_entry = {i["nodeId"]: i.get("bestEntry") for i in report.get("inconsistencies", [])}

    trees = {}
    for fn in sorted(os.listdir(phase2a_dir)):
        if fn.endswith("-tree.json"):
            trees[fn[:-len("-tree.json")]] = _load_json(os.path.join(phase2a_dir, fn))

    fixed = 0
    for entry_id, tree in trees.items():
        tg = TreeGraph(tree)
        existing = {n.get("shortId") for n in tg.nodes.values() if n.get("shortId")}
        dirty = False
        new_pending = []  # ISSUE-2a-42：reconcile 新增的可穿透子节点，待回 BFS 展开

        for nid in list(tg.nodes.keys()):
            if nid not in nodes_to_fix:
                continue
            if nid not in tg.nodes:  # 已被本入口内其他节点子树删除（权威父不含它）→ 勿复活成悬空（ISSUE-2a-41）
                continue
            # 权威 calls：LLM 重分析优先，否则从 bestEntry 复制（兜底）
            if nid in re_analysis:
                auth_calls = re_analysis[nid]
            elif best_entry.get(nid) in trees:
                be_tg = TreeGraph(trees[best_entry[nid]])
                auth_calls = _edges_to_calls(be_tg.children(nid), be_tg)
            else:
                continue

            cur_to = sorted(e["to"] for e in tg.children(nid))
            auth_to = sorted(build_node_id(c.get("targetFilePath", ""), c.get("targetMethod", ""), c.get("targetClass", "")) for c in auth_calls)
            if cur_to == auth_to:
                continue  # 已一致

            _remove_subtree_with_refcount(tg, nid)  # 引用计数删旧子树
            parent_layer = _node_layer(tg, nid)
            for call in _normalize_calls_order(auth_calls, ""):
                child_id = build_node_id(call.get("targetFilePath", ""), call.get("targetMethod", ""), call.get("targetClass", ""))
                if child_id not in tg.nodes:
                    tg.nodes[child_id] = _create_node_from_call(call, existing)
                tg.add_edge({"id": f"e{len(tg.edges)}", "from": nid, "to": child_id,
                             "layer": parent_layer + 1, "callType": call.get("callType", "DIRECT"),
                             "condition": call.get("condition", "始终执行"),
                             "sourceLine": call.get("sourceLine"), "truncated": False})
                if not tg.nodes[child_id].get("terminal"):  # ISSUE-2a-42：可穿透子需回 BFS
                    new_pending.append(child_id)
            dirty = True
            fixed += 1

        if dirty:
            valid_edges = [ed for ed in tg.edges if ed["from"] in tg.nodes and ed["to"] in tg.nodes]
            if len(valid_edges) != len(tg.edges):  # ISSUE-2a-41 双保险：清理残留悬空边
                print(f"WARN: {entry_id} 清理残留悬空边 {len(tg.edges) - len(valid_edges)} 条"
                      f"（refcount 删除未尽，ISSUE-2a-41）", file=sys.stderr)
                tree["edges"] = valid_edges
                tg.edges = valid_edges
            for i, edge in enumerate(tg.edges):  # ISSUE-2a-40：删+建后 edge.id 重编号防撞号
                edge["id"] = f"e{i}"
            lookup = {}
            lp = os.path.join(cache_dir, "phase1b", "db-schema-lookup.json")
            if os.path.exists(lp):
                lookup = _load_json(lp).get("lookup", {})
            _backfill_di_via_lookup(tg, lookup)
            prog = _load_json(_progress_path(cache_dir, entry_id))
            expanded = set(prog.get("expandedNodes", []))
            pend = [n for n in prog.get("pendingNodes", []) if n in tg.nodes]  # 清悬空（ISSUE-2a-18）
            pend_set = set(pend)
            for cid in new_pending:  # 回灌可穿透子（ISSUE-2a-42）
                if cid not in expanded and cid not in pend_set and cid in tg.nodes:
                    pend.append(cid)
                    pend_set.add(cid)
            prog["pendingNodes"] = pend
            prog["totalNodes"] = len(tg.nodes)
            prog["totalEdges"] = len(tg.edges)
            _save_json(_tree_path(cache_dir, entry_id), tree)
            _save_json(_progress_path(cache_dir, entry_id), prog)

    print(json.dumps({"status": "applied", "fixedNodes": fixed,
                      "reAnalysisLoaded": len(re_analysis)}, indent=2, ensure_ascii=False))


# ── mode: llm-backfill-prepare（收集 DI 缺失节点）────────────────────

def do_llm_backfill_prepare(args):
    cache_dir = os.path.abspath(args.cache_dir)
    phase2a_dir = os.path.join(cache_dir, "phase2a")

    node_map, affected = {}, {}
    for fn in sorted(os.listdir(phase2a_dir)):
        if not fn.endswith("-tree.json"):
            continue
        entry_id = fn[:-len("-tree.json")]
        for nid, node in _load_json(os.path.join(phase2a_dir, fn))["nodes"].items():
            if node.get("terminal") and not node.get("domainInteraction"):
                node_map.setdefault(nid, node)
                affected.setdefault(nid, []).append(entry_id)

    nodes = []
    for nid, node in node_map.items():
        fp = node.get("filePath", "") or _search_java_file(args.project_dir, node.get("class", ""))
        nodes.append({"nodeId": nid, "class": node.get("class", ""), "method": node.get("method", ""),
                      "filePath": fp, "affectedEntries": affected[nid]})

    out = os.path.join(phase2a_dir, "tmp", "_llm-backfill-context.json")
    _save_json(out, {"totalNodes": len(nodes), "nodes": nodes})
    print(json.dumps({"status": "prepared", "out": out, "totalNodes": len(nodes)},
                     indent=2, ensure_ascii=False))


# ── mode: llm-backfill-apply（回写 domainInteraction，幂等）──────────

def do_llm_backfill_apply(args):
    cache_dir = os.path.abspath(args.cache_dir)
    phase2a_dir = os.path.join(cache_dir, "phase2a")
    di_map = {r["nodeId"]: r.get("domainInteraction") for r in _load_subagent_results(args.results)}

    ctx_path = os.path.join(phase2a_dir, "tmp", "_llm-backfill-context.json")
    affected = {n["nodeId"]: n.get("affectedEntries", []) for n in _load_json(ctx_path).get("nodes", [])}

    updated = 0
    for nid, di in di_map.items():
        if di is None:
            continue
        for entry_id in affected.get(nid, []):
            tp = _tree_path(cache_dir, entry_id)
            tree = _load_json(tp)
            n = tree["nodes"].get(nid)
            if n and not n.get("domainInteraction"):  # 幂等：只覆盖 null
                n["domainInteraction"] = di
                _save_json(tp, tree)
                updated += 1

    print(json.dumps({"status": "applied", "updated": updated,
                      "totalDi": len(di_map)}, indent=2, ensure_ascii=False))


# ── mode: rmb-topic-backfill（确定性补 RMB 发送端 routingKeys.topic，design 4.3.5）──

def do_rmb_topic_backfill(args):
    """扫 RMB 发送端节点（di: type=EXTERNAL+protocol=RMB，对齐 find_rmb_senders，决策14），
    读 @RmbTopic（共享 _rmb_topic）填 routingKeys.topic（幂等）。"""
    cache_dir = os.path.abspath(args.cache_dir)
    project_dir = os.path.abspath(args.project_dir) if args.project_dir else "."
    phase2a_dir = os.path.join(cache_dir, "phase2a")
    constants = _load_json(os.path.join(cache_dir, "phase1a", "constants.json"))
    if not isinstance(constants, dict):
        constants = {}

    topic_cache = {}  # filePath -> {method: {topic, topicMode, transCode}}
    filled, entries_touched = 0, 0
    for fn in sorted(os.listdir(phase2a_dir)):
        if not fn.endswith("-tree.json"):
            continue
        tp = os.path.join(phase2a_dir, fn)
        tree = _load_json(tp)
        dirty = False
        for node in tree.get("nodes", {}).values():
            di = node.get("domainInteraction")
            di = di if isinstance(di, dict) else {}
            # 判据与 phase4_rmb_bridge.find_rmb_senders 一致（决策14 阶段0）：用 di（EXTERNAL+RMB）
            # 而非 endpointType，避免 discover 标注发散（RMB/RMB_EXTERNAL）导致漏配 controller-004
            if not (di.get("type") == "EXTERNAL" and di.get("protocol") == "RMB"):
                continue
            rk = di.get("routingKeys") if isinstance(di.get("routingKeys"), dict) else {}
            if rk.get("topic"):
                continue  # 幂等：已有 topic
            cls = node.get("class", "")
            cache_key = cls or node.get("filePath")
            if not cache_key:
                continue
            if cache_key not in topic_cache:
                content = None
                fp = node.get("filePath")
                if fp:
                    try:
                        with open(os.path.join(project_dir, fp)) as f:
                            content = f.read()
                    except Exception:
                        content = None
                if content is None and cls:  # filePath 错/读不到 → 按类全限定名搜（ISSUE-2a-39 类）
                    alt = _search_java_file(project_dir, cls)
                    if alt:
                        try:
                            with open(os.path.join(project_dir, alt)) as f:
                                content = f.read()
                        except Exception:
                            content = None
                topic_cache[cache_key] = extract_rmb_topic_by_method(content, constants) if content else {}
            info = topic_cache[cache_key].get(node.get("method"))
            if not info or not info.get("topic"):
                continue
            rk = dict(rk)
            rk["topic"] = info["topic"]
            if info.get("topicMode"):
                rk["topicMode"] = info["topicMode"]
            di["routingKeys"] = rk
            di.setdefault("type", "EXTERNAL")
            di.setdefault("direction", "OUT")
            di.setdefault("protocol", "RMB")
            node["domainInteraction"] = di
            filled += 1
            dirty = True
        if dirty:
            _save_json(tp, tree)
            entries_touched += 1
    print(json.dumps({"status": "rmb-topic-backfilled", "filled": filled,
                      "entriesTouched": entries_touched}, indent=2, ensure_ascii=False))


# ── Argument parsing & main ──────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="Phase 2a: 调用树展开（DAG，决策 12）")
    p.add_argument("--mode", required=True,
                   choices=["init", "next-batch", "merge",
                            "reconcile-prepare", "reconcile-apply",
                            "llm-backfill-prepare", "llm-backfill-apply",
                            "rmb-topic-backfill"],
                   help="Operation mode")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    p.add_argument("--entry-id", help="Entry ID (e.g. controller-001)")
    p.add_argument("--entry", help="entries.json path (init mode)")
    p.add_argument("--batch-size", type=int, default=15, help="Nodes per batch (next-batch)")
    p.add_argument("--results", help="Subagent output JSON path (merge mode)")
    p.add_argument("--project-dir", help="Project source root (merge / llm-backfill-prepare)")
    p.add_argument("--report", help="reconcile-report path (reconcile-apply, default phase2a/tmp/_reconcile-report.json)")
    p.add_argument("--max-depth", type=int, default=20)
    p.add_argument("--max-nodes", type=int, default=500)
    p.add_argument("--max-fanout", type=int, default=10)
    return p.parse_args()


MODES = {
    "init": do_init,
    "next-batch": do_next_batch,
    "merge": do_merge,
    "reconcile-prepare": do_reconcile_prepare,
    "reconcile-apply": do_reconcile_apply,
    "llm-backfill-prepare": do_llm_backfill_prepare,
    "llm-backfill-apply": do_llm_backfill_apply,
    "rmb-topic-backfill": do_rmb_topic_backfill,
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
