"""Phase 4: Spring Event Bridge Matching for flow-trace-java

Matches Spring Event publisher nodes with @EventListener methods by Event class name.

Input:  phase3/{entryId}-pruned.json or phase4/{entryId}.json
Output: phase4/event-bridges.json, phase4/merged-event-{senderId}-{eventClass}.json
"""
import json, os, argparse, sys, subprocess, re, copy


def parse_args():
    p = argparse.ArgumentParser(description="Phase 4: Spring Event Bridge Matching")
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


def _find_publish_event_calls(project_dir):
    """Search project source for publishEvent() calls.

    Returns list of {filePath, lineNumber, eventClass, fullMatch}
    """
    results = []
    java_root = os.path.join(project_dir, "src", "main", "java") if os.path.exists(
        os.path.join(project_dir, "src", "main", "java")) else project_dir

    try:
        result = subprocess.run(
            ["grep", "-rn", "publishEvent", java_root],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return results

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            file_path, _, content = parts

            # Extract event class from publishEvent call
            event_class = _extract_event_class(content)
            if event_class:
                results.append({
                    "filePath": file_path,
                    "eventClass": event_class,
                    "line": content.strip(),
                })
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return results


def _extract_event_class(content):
    """Extract Event class name from publishEvent() call.

    Patterns:
    - publishEvent(new XxxEvent(...)) -> XxxEvent
    - publishEvent(xxxEvent) -> XxxEvent (from variable name heuristic)
    """
    # new XxxEvent(...)
    m = re.search(r'publishEvent\s*\(\s*new\s+(\w+)', content)
    if m:
        return m.group(1)

    # Variable: publishEvent(xxxEvent) or publishEvent(this.xxxEvent)
    m = re.search(r'publishEvent\s*\(\s*(?:this\.)?(\w+[Ee]vent\w*)', content)
    if m:
        return m.group(1)

    # Variable: publishEvent(event)
    m = re.search(r'publishEvent\s*\(\s*(\w+)\s*\)', content)
    if m:
        var_name = m.group(1)
        # Heuristic: capitalize first letter and append Event if not already
        if not var_name.endswith("Event"):
            return var_name[0].upper() + var_name[1:] + "Event"
        return var_name

    return None


def _find_event_listeners(project_dir):
    """Search project source for @EventListener/@TransactionalEventListener.

    Returns dict: eventClass -> list of {filePath, method, eventClass}
    """
    index = {}
    java_root = os.path.join(project_dir, "src", "main", "java") if os.path.exists(
        os.path.join(project_dir, "src", "main", "java")) else project_dir

    try:
        # Find @EventListener annotated methods
        result = subprocess.run(
            ["grep", "-rn", "-B1", "-E", "@(EventListener|TransactionalEventListener)", java_root],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            return index

        lines = result.stdout.strip().split("\n")
        i = 0
        while i < len(lines):
            line = lines[i]
            # Look for the annotation line
            if "@EventListener" in line or "@TransactionalEventListener" in line:
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    file_path = parts[0]
                    # Next non-empty line should be the method signature
                    method_line = ""
                    j = i + 1
                    while j < len(lines):
                        next_parts = lines[j].split(":", 2)
                        if len(next_parts) >= 3 and next_parts[0] == file_path:
                            method_line = next_parts[2]
                            break
                        j += 1

                    # Extract parameter type (event class) from method signature
                    event_class = _extract_listener_event_class(method_line)
                    if event_class:
                        if event_class not in index:
                            index[event_class] = []
                        # Extract method name
                        method_match = re.search(r'(\w+)\s*\(', method_line)
                        method_name = method_match.group(1) if method_match else "unknown"
                        index[event_class].append({
                            "eventClass": event_class,
                            "filePath": file_path,
                            "method": method_name,
                        })
            i += 1
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return index


def _extract_listener_event_class(method_line):
    """Extract event class from listener method parameter."""
    # public void handleEvent(XxxEvent event)
    m = re.search(r'\(\s*(\w+[Ee]vent\w*)\s+\w+\s*\)', method_line)
    if m:
        return m.group(1)
    return None


def _extract_event_publishers_from_chain(chain, project_dir):
    """Find nodes in chain that call publishEvent.

    Uses grep on the node's source file to check for publishEvent calls.
    """
    publishers = []
    publish_calls = _find_publish_event_calls(project_dir)

    # Build filePath -> publish calls index
    file_index = {}
    for call in publish_calls:
        fp = call["filePath"]
        if fp not in file_index:
            file_index[fp] = []
        file_index[fp].append(call)

    for node in chain:
        if node.get("terminal"):
            continue
        file_path = node.get("filePath", "")
        if not file_path:
            continue

        # Check if this node's source file has publishEvent calls
        for source_path, calls in file_index.items():
            if file_path in source_path or source_path.endswith(file_path):
                for call in calls:
                    publishers.append({
                        "nodeId": node["nodeId"],
                        "node": node,
                        "eventClass": call["eventClass"],
                    })
                break

    return publishers


def _create_bridge_node(sender_node, event_class, layer_offset):
    """Create a virtual BRIDGE node for Event connection."""
    return {
        "nodeId": f"BRIDGE:EVENT:{sender_node['nodeId']}->{event_class}",
        "class": "BRIDGE",
        "method": "event-bridge",
        "package": "",
        "filePath": "",
        "layer": layer_offset,
        "layerType": "BRIDGE",
        "parentId": sender_node["nodeId"],
        "callType": "BRIDGE",
        "terminal": False,
        "description": f"Event bridge: {event_class}",
        "domainInteraction": None,
    }


def do_event_bridge(args):
    entries = _load_json(args.entries)

    # Build event listener index from project source
    listener_index = _find_event_listeners(args.project_dir)

    bridges = []
    merged_flows = []

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
        publishers = _extract_event_publishers_from_chain(chain, args.project_dir)

        if not publishers:
            print(f"  {entry_id}: no event publishers")
            continue

        for pub_info in publishers:
            event_class = pub_info["eventClass"]
            sender_node = pub_info["node"]

            matching_listeners = listener_index.get(event_class, [])

            if not matching_listeners:
                bridge_info = {
                    "type": "EVENT",
                    "eventClass": event_class,
                    "matchingStatus": "UNMATCHED",
                    "senderHandlerId": entry_id,
                    "receiverHandlerId": None,
                    "isExternal": True,
                }
                bridges.append(bridge_info)
                print(f"  {entry_id}: UNMATCHED Event -> {event_class}")
                continue

            for listener in matching_listeners:
                bridge_info = {
                    "type": "EVENT",
                    "eventClass": event_class,
                    "matchingStatus": "MATCHED",
                    "senderHandlerId": entry_id,
                    "receiverHandlerId": f"{listener.get('filePath', '')}:{listener.get('method', '')}",
                    "isExternal": False,
                }

                sender_max_layer = max(n["layer"] for n in chain)
                bridge_node = _create_bridge_node(sender_node, event_class, sender_max_layer + 1)

                # Build listener stub
                listener_stub = [{
                    "nodeId": f"EVENT_LISTENER:{listener.get('filePath', '')}:{listener.get('method', '')}",
                    "class": "EVENT_LISTENER",
                    "method": listener.get("method", ""),
                    "package": "",
                    "filePath": listener.get("filePath", ""),
                    "layer": sender_max_layer + 2,
                    "layerType": "ENTRY",
                    "parentId": bridge_node["nodeId"],
                    "callType": "BRIDGE",
                    "terminal": False,
                    "description": f"@EventListener for {event_class}",
                    "domainInteraction": None,
                }]

                merged_chain = list(chain) + [bridge_node] + listener_stub

                merged = {
                    "entryId": f"{entry_id}-event-{event_class}",
                    "flowStatus": "VALID",
                    "flowType": "MERGED_EVENT_FLOW",
                    "chain": merged_chain,
                    "eventBridge": bridge_info,
                    "summary": {
                        "retained": len(merged_chain),
                        "pruned": 0,
                        "terminals": sum(1 for n in merged_chain if n.get("terminal")),
                    },
                }

                safe_class = re.sub(r'[^a-zA-Z0-9_-]', '_', event_class)
                merged_path = os.path.join(
                    args.cache_dir, "phase4", f"merged-event-{entry_id}-{safe_class}.json"
                )
                _save_json(merged_path, merged)
                bridges.append(bridge_info)
                merged_flows.append(merged_path)
                print(f"  {entry_id}: MERGED Event via {event_class}")

    bridges_path = os.path.join(args.cache_dir, "phase4", "event-bridges.json")
    _save_json(bridges_path, {
        "version": "2.0",
        "generator": "flow-trace-java",
        "bridgeType": "EVENT",
        "totalBridges": len(bridges),
        "matched": sum(1 for b in bridges if b["matchingStatus"] == "MATCHED"),
        "unmatched": sum(1 for b in bridges if b["matchingStatus"] == "UNMATCHED"),
        "bridges": bridges,
    })

    print(f"\nEvent Bridge Complete!")
    print(f"  Bridges: {len(bridges)}")
    print(f"  Matched: {sum(1 for b in bridges if b['matchingStatus'] == 'MATCHED')}")
    print(f"  Unmatched: {sum(1 for b in bridges if b['matchingStatus'] == 'UNMATCHED')}")
    print(f"  Merged flows: {len(merged_flows)}")


if __name__ == '__main__':
    do_event_bridge(parse_args())
