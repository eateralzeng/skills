"""Phase 4: @Async Bridge Detection for flow-trace-java

Detects @Async annotated methods in call chains and marks them as async bridges.
Does not merge flows across entries - only annotates async call relationships.

Input:  phase3/{entryId}-pruned.json or phase4/{entryId}.json
Output: phase4/async-bridges.json, updates phase4/{entryId}.json with async flags
"""
import json, os, argparse, sys, subprocess, re


def parse_args():
    p = argparse.ArgumentParser(description="Phase 4: @Async Bridge Detection")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    p.add_argument("--entries", required=True, help="Path to entries.json")
    p.add_argument("--project-dir", required=True, help="Java project root")
    return p.parse_args()


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _build_async_method_index(project_dir):
    """Search project source for @Async annotated methods.

    Returns a set of (filePath, methodName) tuples for methods annotated with @Async.
    """
    async_methods = set()
    java_root = os.path.join(project_dir, "src", "main", "java") if os.path.exists(
        os.path.join(project_dir, "src", "main", "java")) else project_dir

    try:
        # Find @Async annotations with context lines
        result = subprocess.run(
            ["grep", "-rn", "-B1", "-A1", "@Async", java_root],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return async_methods

        lines = result.stdout.strip().split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            if "@Async" not in line:
                i += 1
                continue

            parts = line.split(":", 2)
            if len(parts) < 3:
                i += 1
                continue

            file_path = parts[0]

            # Look at next lines for method declaration
            for j in range(i + 1, min(i + 4, len(lines))):
                next_parts = lines[j].split(":", 2)
                if len(next_parts) < 3 or next_parts[0] != file_path:
                    continue
                method_line = next_parts[2]
                # Extract method name: public ReturnType methodName(...)
                m = re.search(r'(?:public|private|protected)\s+\S+\s+(\w+)\s*\(', method_line)
                if m:
                    async_methods.add((file_path, m.group(1)))
                    break

            i += 1
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return async_methods


def _is_async_method(node, async_index):
    """Check if a chain node's target method is @Async annotated."""
    file_path = node.get("filePath", "")
    method = node.get("method", "")

    if not file_path or not method:
        return False

    # 用 basename 精确匹配文件名，避免子串误匹配
    # 旧版 file_path in async_path 会把 Service.java 误匹配到 MyService.java
    node_basename = os.path.basename(file_path)
    for async_path, async_method in async_index:
        if async_method != method:
            continue
        if os.path.basename(async_path) == node_basename:
            return True

    return False


def do_async_bridge(args):
    args.cache_dir = os.path.abspath(args.cache_dir)
    args.project_dir = os.path.abspath(args.project_dir)
    entries = _load_json(args.entries)

    # Build @Async method index from project source
    async_index = _build_async_method_index(args.project_dir)

    bridges = []
    total_flagged = 0

    for entry in entries.get("entries", []):
        entry_id = entry["id"]
        phase5_path = os.path.join(args.cache_dir, "phase4", f"{entry_id}.json")
        pruned_path = os.path.join(args.cache_dir, "phase3", f"{entry_id}-pruned.json")

        data_path = phase5_path if os.path.exists(phase5_path) else pruned_path
        if not os.path.exists(data_path):
            print(f"  SKIP: {entry_id} - no data file", file=sys.stderr)
            continue

        data = _load_json(data_path)
        chain = data.get("chain", [])

        flagged = 0
        for node in chain:
            if node.get("terminal"):
                continue
            if _is_async_method(node, async_index):
                node["async"] = True
                node["domainInteraction"] = node.get("domainInteraction") or {
                    "type": "ASYNC",
                    "direction": "OUT",
                    "target": f"{node.get('class', '')}.{node.get('method', '')}",
                }
                bridges.append({
                    "type": "ASYNC",
                    "matchingStatus": "DETECTED",
                    "handlerId": entry_id,
                    "nodeId": node["nodeId"],
                    "filePath": node.get("filePath", ""),
                    "method": node.get("method", ""),
                })
                flagged += 1

        if flagged > 0:
            # Save updated data back to phase4
            out_path = os.path.join(args.cache_dir, "phase4", f"{entry_id}.json")
            data["asyncBridges"] = [
                b for b in bridges if b["handlerId"] == entry_id
            ]
            _save_json(out_path, data)
            total_flagged += flagged

        print(f"  {entry_id}: {flagged} async methods detected")

    # Save bridges index
    bridges_path = os.path.join(args.cache_dir, "phase4", "async-bridges.json")
    _save_json(bridges_path, {
        "version": "2.0",
        "generator": "flow-trace-java",
        "bridgeType": "ASYNC",
        "totalBridges": len(bridges),
        "bridges": bridges,
    })

    print(f"\nAsync Bridge Complete!")
    print(f"  Total async methods detected: {total_flagged}")


if __name__ == '__main__':
    do_async_bridge(parse_args())
