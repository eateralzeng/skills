"""Phase 5: Source code verification + description filling (data preparation & merge tool)

This script does NOT call LLM directly. It provides two modes:
  --mode prepare: Generate verify-tasks.json from {entryId}.json files
  --mode merge:   Merge LLM verification results back into {entryId}.json

The LLM source code reading is orchestrated by prompt.md via sub-agents.
"""
import json
import os
import argparse
from collections import defaultdict


def parse_args():
    p = argparse.ArgumentParser(description="Phase 5: Source code verification + description filling")
    p.add_argument("cache_dir", help="Cache directory with {entryId}.json files")
    p.add_argument("project_dir", help="Java project root directory")
    p.add_argument("--mode", choices=["prepare", "merge"], required=True,
                   help="prepare: generate tasks; merge: apply results")
    p.add_argument("--verify-results", help="Path to verify-results JSON (for merge mode)")
    p.add_argument("--entries-path", help="Path to entries JSON (for file path lookup)")
    return p.parse_args()


def load_json(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        return json.load(f)


def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def find_source_file(project_dir, file_path):
    """Resolve a relative file path to an absolute path."""
    if not file_path:
        return None
    # Try direct path
    full = os.path.join(project_dir, file_path)
    if os.path.exists(full):
        return full
    # Try searching (file_path might be relative to src/main/java)
    for root, dirs, files in os.walk(project_dir):
        basename = os.path.basename(file_path)
        if basename in files:
            return os.path.join(root, basename)
    return None


def is_terminal_type(class_name):
    return any(kw in class_name for kw in ('Mapper', 'Dao', 'Repository', 'Client', 'Proxy'))


def do_prepare(cache_dir, project_dir, entries_path):
    """Generate verify-tasks.json for LLM sub-agents."""
    entries = load_json(entries_path) or []
    if isinstance(entries, dict) and "entries" in entries:
        entries = entries["entries"]

    # Build file path lookup from entries
    entry_file_map = {}
    for e in entries:
        cls = e.get("class", e.get("className", ""))
        mth = e.get("method", e.get("methodName", ""))
        fp = e.get("file", e.get("filePath", ""))
        entry_file_map[f"{cls}.{mth}"] = fp

    tasks = []

    phase3_dir = os.path.join(cache_dir, "phase3")
    if not os.path.isdir(phase3_dir):
        print(f"Error: phase3 directory not found: {phase3_dir}")
        return

    for filename in sorted(os.listdir(phase3_dir)):
        if not filename.endswith(".json"):
            continue

        filepath = os.path.join(phase3_dir, filename)
        chain_data = load_json(filepath)
        if not chain_data:
            continue

        entry_id = chain_data.get("entryId", "")
        chain = chain_data.get("chain", [])
        discarded = chain_data.get("discardedEdges", [])
        unexpanded = chain_data.get("unexpandedNodes", [])

        task = {
            "entryId": entry_id,
            "entryType": chain_data.get("entryType", ""),
            "status": chain_data.get("status", ""),
            "discardedEdgeTasks": [],
            "unexpandedNodeTasks": [],
            "descriptionTasks": [],
        }

        # Build chain node index for file path lookup
        chain_file_map = {}
        for node in chain:
            cls = node.get("class", "")
            mth = node.get("method", "")
            fp = node.get("file_path", "")
            chain_file_map[f"{cls}.{mth}"] = fp
            chain_file_map[node.get("nodeId", "")] = fp

        # Task 1: Verify discardedEdges
        for de in discarded:
            parent_file = chain_file_map.get(de.get("parent", ""), "")
            child_class = de.get("childClass", "")
            child_method = de.get("childMethod", "")
            child_file = chain_file_map.get(de.get("childId", ""), "")

            # Try to find child file from entry_file_map
            if not child_file:
                child_file = entry_file_map.get(f"{child_class}.{child_method}", "")

            task["discardedEdgeTasks"].append({
                "parent": de.get("parent", ""),
                "parentFile": find_source_file(project_dir, parent_file),
                "childClass": child_class,
                "childMethod": child_method,
                "childFile": find_source_file(project_dir, child_file),
                "childId": de.get("childId", ""),
                "confidence": de.get("confidence"),
                "reason": de.get("reason", ""),
                "isTerminalType": is_terminal_type(child_class),
            })

        # Task 2: Expand unexpandedNodes
        for un in unexpanded:
            fp = chain_file_map.get(un.get("nodeId", ""), "")
            task["unexpandedNodeTasks"].append({
                "nodeId": un.get("nodeId", ""),
                "class": un.get("class", ""),
                "method": un.get("method", ""),
                "layer": un.get("layer", 0),
                "file": find_source_file(project_dir, fp),
                "reason": un.get("reason", ""),
            })

        # Task 3: Generate descriptions for chain nodes
        for node in chain:
            if node.get("description"):
                continue
            fp = node.get("file_path", "")
            cls = node.get("class", "")
            mth = node.get("method", "")

            # Skip standard Mapper methods (no source reading needed)
            if node.get("terminal") and "Mapper" in cls:
                continue

            task["descriptionTasks"].append({
                "nodeId": node.get("nodeId", ""),
                "class": cls,
                "method": mth,
                "layer": node.get("layer", 0),
                "layerType": node.get("layerType", ""),
                "terminal": node.get("terminal", False),
                "file": find_source_file(project_dir, fp),
                "role": node.get("role", ""),
            })

        if task["discardedEdgeTasks"] or task["unexpandedNodeTasks"] or task["descriptionTasks"]:
            tasks.append(task)

    phase5_dir = os.path.join(cache_dir, "phase5")
    os.makedirs(phase5_dir, exist_ok=True)
    output_path = os.path.join(phase5_dir, "verify-tasks.json")
    save_json(output_path, {
        "totalTasks": len(tasks),
        "tasks": tasks,
    })
    print(f"Phase 5 (prepare): {len(tasks)} entries with verification tasks")
    print(f"  Output: {output_path}")


def do_merge(cache_dir, verify_results_path):
    """Merge LLM verification results back into {entryId}.json."""
    results = load_json(verify_results_path)
    if not results:
        print("No verify results found")
        return

    results_by_entry = {}
    for r in results.get("results", []):
        results_by_entry[r.get("entryId", "")] = r

    merged_count = 0

    for entry_id, result in results_by_entry.items():
        src_path = os.path.join(cache_dir, "phase3", f"{entry_id}.json")
        chain_data = load_json(src_path)
        if not chain_data:
            continue

        chain = chain_data.get("chain", [])
        discarded = chain_data.get("discardedEdges", [])
        unexpanded = chain_data.get("unexpandedNodes", [])

        # Build node index
        node_by_id = {n.get("nodeId"): n for n in chain}

        # 1. Apply descriptions
        for desc in result.get("descriptions", []):
            node_id = desc.get("nodeId", "")
            node = node_by_id.get(node_id)
            if node:
                node["description"] = desc.get("description", "")
                node["source"] = desc.get("source", node.get("source", "graph-db"))

        # 2. Apply child descriptions
        for cd in result.get("childDescriptions", []):
            parent_id = cd.get("parentNodeId", "")
            method_name = cd.get("method", "")
            # Find child node by parent and method
            for n in chain:
                if n.get("parentId") == parent_id and n.get("method") == method_name:
                    if not n.get("description"):
                        n["description"] = cd.get("description", "")
                        n["source"] = "inferred-from-parent"
                    break

        # 3. Restore verified discarded edges (nodes that should be in chain)
        for verified in result.get("restoredNodes", []):
            node = {
                "layer": verified.get("layer", 0),
                "layerType": verified.get("layerType", "HANDLER"),
                "class": verified.get("class", ""),
                "method": verified.get("method", ""),
                "description": verified.get("description", ""),
                "parentLayer": verified.get("parentLayer", 0),
                "parentId": verified.get("parentId", ""),
                "source": "source-code",
                "file_path": verified.get("file_path", ""),
                "nodeId": verified.get("nodeId", ""),
                "package": verified.get("package", ""),
                "role": verified.get("role", ""),
                "terminal": verified.get("terminal", False),
            }
            if verified.get("domainInteraction"):
                node["domainInteraction"] = verified["domainInteraction"]
            chain.append(node)

            # Remove from discardedEdges
            child_id = verified.get("nodeId", "")
            chain_data["discardedEdges"] = [
                de for de in chain_data.get("discardedEdges", [])
                if de.get("childId") != child_id
            ]

        # 4. Clear verified discarded edges (confirmed as correctly discarded)
        for verified_id in result.get("confirmedDiscarded", []):
            chain_data["discardedEdges"] = [
                de for de in chain_data.get("discardedEdges", [])
                if de.get("childId") != verified_id
            ]

        # 5. Clear expanded unexpanded nodes
        for expanded_id in result.get("expandedNodes", []):
            chain_data["unexpandedNodes"] = [
                un for un in chain_data.get("unexpandedNodes", [])
                if un.get("nodeId") != expanded_id
            ]

        # Reassign layers (in case restored nodes changed the tree)
        # Skip re-layering for simplicity - restored nodes should have correct layers

        save_path = os.path.join(cache_dir, "phase5", f"{entry_id}.json")
        save_json(save_path, chain_data)
        merged_count += 1

    print(f"Phase 5 (merge): {merged_count} entries updated")


def main():
    args = parse_args()
    if args.mode == "prepare":
        do_prepare(args.cache_dir, args.project_dir, args.entries_path)
    elif args.mode == "merge":
        if not args.verify_results:
            print("Error: --verify-results required for merge mode", )
            return
        do_merge(args.cache_dir, args.verify_results)


if __name__ == "__main__":
    main()
