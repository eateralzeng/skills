"""Phase 4: RMB Bridge Matching for flow-trace-java

Matches RMB sender nodes (from pruned trees) with RMB receiver entries
by Topic name, then merges them into unified flows.

Input:  phase3/{entryId}-pruned.json, phase1a/entries.json
Output: phase4/bridges.json, phase4/merged-rmb-{senderId}.json,
        phase4/{entryId}.json (standalone/non-RMB copies)
"""
import json, os, argparse, sys, re, copy


def parse_args():
    p = argparse.ArgumentParser(description="Phase 4: RMB Bridge Matching")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    p.add_argument("--entries", required=True, help="Path to entries.json")
    p.add_argument("--project-dir", help="Java project root (unused, for future)")
    p.add_argument("--bridge-rules", help="Path to bridge-rules.md (unused, rules are in data)")
    return p.parse_args()


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _extract_rmb_nodes(chain):
    """Find nodes with domainInteraction type=EXTERNAL and protocol=RMB.

    Extracts topic and transCode from routingKeys, with fallback to target
    for backward compatibility with data that lacks routingKeys.
    """
    senders = []
    for node in chain:
        di = node.get("domainInteraction")
        if di and di.get("type") == "EXTERNAL" and di.get("protocol") == "RMB":
            routing_keys = di.get("routingKeys") or {}
            topic = routing_keys.get("topic") or di.get("target", "")
            trans_code = routing_keys.get("transCode")
            if topic:
                senders.append({
                    "nodeId": node["nodeId"],
                    "topic": topic,
                    "transCode": trans_code,
                    "node": node,
                })
    return senders


def _build_rmb_receiver_index(entries):
    """Build topic -> list of RMB receiver entries from entries.json."""
    index = {}
    for entry in entries.get("entries", []):
        if entry.get("type") != "rmb":
            continue
        topic = entry.get("rmbTopic") or entry.get("httpMapping") or ""
        # Try to extract topic from various possible fields
        if not topic and "rmbTopic" in entry:
            topic = entry["rmbTopic"]
        if topic:
            if topic not in index:
                index[topic] = []
            index[topic].append(entry)
    return index


def _find_receiver_chain(cache_dir, receiver_entry_id):
    """Load the pruned chain for a receiver entry."""
    pruned_path = os.path.join(cache_dir, "phase3", f"{receiver_entry_id}-pruned.json")
    if not os.path.exists(pruned_path):
        return None
    data = _load_json(pruned_path)
    if data.get("flowStatus") == "NO_ENDPOINT":
        return None
    return data.get("chain", [])


def _create_bridge_node(sender_node, receiver_node, layer_offset):
    """Create a virtual BRIDGE node connecting sender and receiver."""
    return {
        "nodeId": f"BRIDGE:{sender_node['nodeId']}->{receiver_node['nodeId']}",
        "class": "BRIDGE",
        "method": "rmb-bridge",
        "package": "",
        "filePath": "",
        "layer": layer_offset,
        "layerType": "BRIDGE",
        "parentId": sender_node["nodeId"],
        "callType": "BRIDGE",
        "terminal": False,
        "description": f"RMB bridge: {sender_node.get('domainInteraction', {}).get('target', '')}",
        "domainInteraction": None,
    }


def _remap_layers(chain, start_layer):
    """Remap node layers starting from start_layer."""
    if not chain:
        return []
    min_layer = min(n["layer"] for n in chain)
    result = []
    for node in chain:
        n = copy.deepcopy(node)
        n["layer"] = n["layer"] - min_layer + start_layer
        result.append(n)
    return result


def do_bridge(args):
    args.cache_dir = os.path.abspath(args.cache_dir)
    entries = _load_json(args.entries)
    receiver_index = _build_rmb_receiver_index(entries)

    bridges = []
    merged_flows = []

    for entry in entries.get("entries", []):
        entry_id = entry["id"]
        pruned_path = os.path.join(args.cache_dir, "phase3", f"{entry_id}-pruned.json")

        if not os.path.exists(pruned_path):
            print(f"  SKIP: {entry_id} - no pruned file", file=sys.stderr)
            continue

        pruned = _load_json(pruned_path)
        if pruned.get("flowStatus") == "NO_ENDPOINT":
            # Copy as-is to phase4
            out_path = os.path.join(args.cache_dir, "phase4", f"{entry_id}.json")
            pruned["rmbBridge"] = None
            _save_json(out_path, pruned)
            print(f"  {entry_id}: NO_ENDPOINT (copied as-is)")
            continue

        chain = pruned.get("chain", [])
        rmb_senders = _extract_rmb_nodes(chain)

        if not rmb_senders:
            # No RMB calls - standalone flow
            out_path = os.path.join(args.cache_dir, "phase4", f"{entry_id}.json")
            pruned["flowType"] = "STANDALONE_FLOW"
            pruned["rmbBridge"] = None
            _save_json(out_path, pruned)
            print(f"  {entry_id}: STANDALONE_FLOW (no RMB)")
            continue

        # Process each RMB sender
        for sender_info in rmb_senders:
            topic = sender_info["topic"]
            sender_trans_code = sender_info.get("transCode")
            sender_node = sender_info["node"]

            matching_receivers = receiver_index.get(topic, [])

            # transCode 二次过滤：两边都有非 null 值时必须相等
            if sender_trans_code is not None:
                matching_receivers = [
                    r for r in matching_receivers
                    if r.get("transCode") is None or r.get("transCode") == sender_trans_code
                ]

            if not matching_receivers:
                # Unmatched RMB sender
                bridge_info = {
                    "topic": topic,
                    "topicMode": "SYNC",
                    "transCode": None,
                    "matchingStatus": "UNMATCHED",
                    "senderHandlerId": entry_id,
                    "receiverHandlerId": None,
                    "isExternal": True,
                }
                bridges.append(bridge_info)
                print(f"  {entry_id}: UNMATCHED RMB -> {topic}")
                continue

            for receiver_entry in matching_receivers:
                receiver_id = receiver_entry["id"]
                receiver_chain = _find_receiver_chain(args.cache_dir, receiver_id)

                if not receiver_chain:
                    bridge_info = {
                        "topic": topic,
                        "topicMode": "SYNC",
                        "transCode": None,
                        "matchingStatus": "UNMATCHED",
                        "senderHandlerId": entry_id,
                        "receiverHandlerId": receiver_id,
                        "isExternal": False,
                    }
                    bridges.append(bridge_info)
                    print(f"  {entry_id}: UNMATCHED (receiver {receiver_id} has no chain)")
                    continue

                # Build merged flow
                bridge_info = {
                    "topic": topic,
                    "topicMode": "SYNC",
                    "transCode": None,
                    "matchingStatus": "MATCHED",
                    "senderHandlerId": entry_id,
                    "receiverHandlerId": receiver_id,
                    "isExternal": False,
                }

                # Find max layer in sender chain
                sender_max_layer = max(n["layer"] for n in chain)

                # Create bridge node
                bridge_node = _create_bridge_node(
                    sender_node,
                    receiver_chain[0] if receiver_chain else {"nodeId": "unknown"},
                    sender_max_layer + 1,
                )

                # Remap receiver chain layers
                receiver_remapped = _remap_layers(receiver_chain, sender_max_layer + 2)
                # Fix receiver root parent to bridge node
                if receiver_remapped:
                    receiver_remapped[0]["parentId"] = bridge_node["nodeId"]

                # Combine: sender chain + bridge + receiver chain
                merged_chain = list(chain) + [bridge_node] + receiver_remapped

                merged = {
                    "entryId": f"{entry_id}-rmb-{receiver_id}",
                    "flowStatus": "VALID",
                    "flowType": "MERGED_RMB_FLOW",
                    "chain": merged_chain,
                    "prunedNodes": [],
                    "rmbBridge": bridge_info,
                    "summary": {
                        "retained": len(merged_chain),
                        "pruned": 0,
                        "terminals": sum(1 for n in merged_chain if n.get("terminal")),
                    },
                }

                merged_path = os.path.join(
                    args.cache_dir, "phase4", f"merged-rmb-{entry_id}-{receiver_id}.json"
                )
                _save_json(merged_path, merged)
                bridges.append(bridge_info)
                merged_flows.append(merged_path)
                print(f"  {entry_id}: MERGED with {receiver_id} via topic={topic}")

        # Also save standalone version for the sender entry
        out_path = os.path.join(args.cache_dir, "phase4", f"{entry_id}.json")
        pruned["flowType"] = "STANDALONE_FLOW"
        pruned["rmbBridge"] = {
            "topics": [s["topic"] for s in rmb_senders],
            "matchingStatus": "UNMATCHED" if not any(
                b["matchingStatus"] == "MATCHED" and b["senderHandlerId"] == entry_id
                for b in bridges
            ) else "MATCHED",
        }
        _save_json(out_path, pruned)

    # Save bridges index
    bridges_path = os.path.join(args.cache_dir, "phase4", "bridges.json")
    _save_json(bridges_path, {
        "version": "2.0",
        "generator": "flow-trace-java",
        "totalBridges": len(bridges),
        "matched": sum(1 for b in bridges if b["matchingStatus"] == "MATCHED"),
        "unmatched": sum(1 for b in bridges if b["matchingStatus"] == "UNMATCHED"),
        "bridges": bridges,
    })

    print(f"\nPhase 4 Complete!")
    print(f"  Bridges: {len(bridges)}")
    print(f"  Matched: {sum(1 for b in bridges if b['matchingStatus'] == 'MATCHED')}")
    print(f"  Unmatched: {sum(1 for b in bridges if b['matchingStatus'] == 'UNMATCHED')}")
    print(f"  Merged flows: {len(merged_flows)}")


if __name__ == '__main__':
    do_bridge(parse_args())
