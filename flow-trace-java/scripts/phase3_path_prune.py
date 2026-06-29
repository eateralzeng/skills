"""Phase 3: Path Pruning for flow-trace-java

Prunes call trees by removing branches that don't reach any terminal node.
Algorithm: find all terminal nodes, backtrack to root marking kept nodes,
move unmarked nodes to prunedNodes list.

Input:  phase2a/{entryId}-tree.json
Output: phase3/{entryId}-pruned.json
"""
import json, os, argparse, sys


def parse_args():
    p = argparse.ArgumentParser(description="Phase 3: Path Pruning")
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


def prune_tree(tree):
    """Prune a single tree, returning pruned result dict."""
    nodes = tree["nodes"]
    entry_id = tree["entryId"]

    # Find all terminal nodes
    terminal_ids = [nid for nid, n in nodes.items() if n.get("terminal")]

    if not terminal_ids:
        # No endpoints found
        all_nodes = list(nodes.values())
        return {
            "entryId": entry_id,
            "flowStatus": "NO_ENDPOINT",
            "chain": [],
            "prunedNodes": [_node_summary(n, "no_terminal_in_tree") for n in all_nodes],
            "summary": {
                "retained": 0,
                "pruned": len(all_nodes),
                "terminals": 0,
            },
        }

    # Backtrack from each terminal to root, collecting kept nodeIds
    kept = set()
    for tid in terminal_ids:
        current = tid
        while current:
            if current in kept:
                break
            kept.add(current)
            node = nodes.get(current)
            current = node.get("parentId") if node else None

    # Partition nodes
    chain = []
    pruned = []
    for nid in kept:
        n = nodes[nid]
        chain.append(_clean_node(n))

    for nid, n in nodes.items():
        if nid not in kept:
            pruned.append(_node_summary(n, "not_on_terminal_path"))

    # Sort chain by layer then nodeId for deterministic output
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


def _clean_node(n):
    """Return a node dict suitable for the pruned output."""
    result = {
        "nodeId": n["nodeId"],
        "class": n["class"],
        "method": n["method"],
        "package": n.get("package", ""),
        "filePath": n.get("filePath", ""),
        "layer": n["layer"],
        "layerType": n["layerType"],
        "parentId": n.get("parentId"),
        "callType": n.get("callType", "DIRECT"),
        "terminal": n.get("terminal", False),
        "description": n.get("description", ""),
        "domainInteraction": n.get("domainInteraction"),
    }
    if n.get("endpointType"):
        result["endpointType"] = n["endpointType"]
    if n.get("patternRef"):
        result["patternRef"] = n["patternRef"]
    return result


def _node_summary(n, reason):
    """Return a minimal summary of a pruned node."""
    return {
        "nodeId": n["nodeId"],
        "class": n["class"],
        "method": n["method"],
        "layer": n["layer"],
        "layerType": n.get("layerType", ""),
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
