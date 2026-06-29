"""Phase 3: Path Pruning for flow-trace-java (DAG 版，决策 12)

从 phase2a 的调用树（nodes + edges DAG）中，反向追溯到所有终点（terminal=true），
剪掉无法到达任何终点的分支，保留从入口到终点的完整业务链路；并把 DAG 降维成
节点列表（chain），供下游 phase4/5/6 消费。

决策 12 改造（design 8.1 A-G + 决策 8 方案 B）：
  - 反向回溯：parentId 单链 → TreeGraph.parents 反向 BFS（正确处理 DAG 多 parent）
  - layer：node 字段 → min(入边 layer)（最短路径深度，design 5.7）
  - parentId：单值 → list（方案 B：所有入边 from，支持 phase6 节点可重复，决策 11）
  - layerType：node 字段 → 推导（ENTRY/INTERNAL/TERMINAL，新格式 node 无此字段）
  - callType：node 字段 → 取最短路径入边的 callType

Input:  phase2a/{entryId}-tree.json（nodes + edges 新格式）
Output: phase3/{entryId}-pruned.json（chain + prunedNodes + summary）
"""
import json
import os
import argparse
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _tree_graph import TreeGraph


def parse_args():
    p = argparse.ArgumentParser(description="Phase 3: Path Pruning (DAG)")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    p.add_argument("--entries", required=True, help="Path to entries.json")
    return p.parse_args()


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _node_layer(tg, nid):
    """节点 layer = min(入边 layer)；root 无入边 → 0（design 5.7）。"""
    parents = tg.parents(nid)
    return min(e["layer"] for e in parents) if parents else 0


def _layer_type(tg, nid, root_id):
    """推导 layerType（新格式 node 无此字段）：root→ENTRY / terminal→TERMINAL / 其他→INTERNAL。"""
    if nid == root_id:
        return "ENTRY"
    if tg.get_node(nid).get("terminal"):
        return "TERMINAL"
    return "INTERNAL"


def prune_tree(tree):
    """剪枝：反向 BFS 从终点沿入边回溯到 root，保留可达终点的节点（design 5.1）。"""
    tg = TreeGraph(tree)
    entry_id = tree["entryId"]
    root_id = tree["rootNodeId"]

    terminal_ids = [nid for nid, n in tg.nodes.items() if n.get("terminal")]

    if not terminal_ids:
        return {
            "entryId": entry_id,
            "flowStatus": "NO_ENDPOINT",
            "chain": [],
            "prunedNodes": [_node_summary(tg, nid, root_id, "no_terminal_in_tree")
                            for nid in tg.nodes],
            "summary": {"retained": 0, "pruned": len(tg.nodes), "terminals": 0},
        }

    # 反向 BFS（edges）：从终点沿入边传播 kept（design 8.1 C，正确处理 DAG 多 parent）
    kept = set(terminal_ids)
    queue = deque(terminal_ids)
    while queue:
        nid = queue.popleft()
        for edge in tg.parents(nid):  # 入边
            frm = edge["from"]
            if frm not in kept:
                kept.add(frm)
                queue.append(frm)

    # 分区
    chain = [_clean_node(tg, nid, root_id) for nid in kept]
    pruned = [_node_summary(tg, nid, root_id, "not_on_terminal_path")
              for nid in tg.nodes if nid not in kept]

    # 确定性排序：(layer, nodeId)
    chain.sort(key=lambda n: (n["layer"], n["nodeId"]))

    return {
        "entryId": entry_id,
        "flowStatus": "VALID",
        "chain": chain,
        "prunedNodes": pruned,
        "summary": {
            "retained": len(chain),
            "pruned": len(pruned),
            "terminals": len(terminal_ids),
        },
    }


def _clean_node(tg, nid, root_id):
    """chain 节点投影：node 静态信息 + parentId(list) + layer(min) + layerType(推导) + callType(edge)。"""
    n = tg.get_node(nid)
    parents = tg.parents(nid)  # 入边 list
    min_edge = min(parents, key=lambda e: e["layer"]) if parents else None
    result = {
        "nodeId": nid,
        "class": n.get("class", ""),
        "method": n.get("method", ""),
        "package": n.get("package", ""),
        "filePath": n.get("filePath", ""),
        "layer": min_edge["layer"] if min_edge else 0,
        "layerType": _layer_type(tg, nid, root_id),
        "parentId": [e["from"] for e in parents],  # 方案 B：list，支持 phase6 节点可重复
        "callType": min_edge.get("callType", "DIRECT") if min_edge else "DIRECT",
        "terminal": n.get("terminal", False),
        "description": n.get("description", ""),
        "domainInteraction": n.get("domainInteraction"),
    }
    if n.get("endpointType"):
        result["endpointType"] = n["endpointType"]
    if n.get("patternRef"):
        result["patternRef"] = n["patternRef"]
    return result


def _node_summary(tg, nid, root_id, reason):
    """被剪节点的最小摘要。"""
    n = tg.get_node(nid)
    return {
        "nodeId": nid,
        "class": n.get("class", ""),
        "method": n.get("method", ""),
        "layer": _node_layer(tg, nid),
        "layerType": _layer_type(tg, nid, root_id),
        "reason": reason,
    }


def main():
    args = parse_args()
    args.cache_dir = os.path.abspath(args.cache_dir)
    entries = _load_json(args.entries)

    total_retained = 0
    total_pruned = 0
    total_terminals = 0
    results = []

    for entry in entries["entries"]:
        entry_id = entry["id"]
        tree_path = os.path.join(args.cache_dir, "phase2a", f"{entry_id}-tree.json")

        if not os.path.exists(tree_path):
            print(f"SKIP: {entry_id} - no tree file found", file=sys.stderr)
            continue

        tree = _load_json(tree_path)
        pruned = prune_tree(tree)

        out_path = os.path.join(args.cache_dir, "phase3", f"{entry_id}-pruned.json")
        _save_json(out_path, pruned)

        results.append({
            "entryId": entry_id,
            "flowStatus": pruned["flowStatus"],
            "summary": pruned["summary"],
        })

        total_retained += pruned["summary"]["retained"]
        total_pruned += pruned["summary"]["pruned"]
        total_terminals += pruned["summary"]["terminals"]

        print(f"  {entry_id}: {pruned['flowStatus']} "
              f"(kept={pruned['summary']['retained']}, "
              f"pruned={pruned['summary']['pruned']}, "
              f"terminals={pruned['summary']['terminals']})")

    print(f"\nPhase 3 Complete!")
    print(f"  Entries processed: {len(results)}")
    print(f"  Total retained: {total_retained}")
    print(f"  Total pruned: {total_pruned}")
    print(f"  Total terminals: {total_terminals}")


if __name__ == '__main__':
    main()
