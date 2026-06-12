"""Phase 4: MQ Bridge Matching for flow-trace-java

Matches MQ sender nodes (MQ_PUBLISH endpoints from pruned trees) with
MQ listener entries by Topic name, then merges them into unified flows.

Input:  phase3/{entryId}-pruned.json, phase1a/entries.json
Output: phase4/mq-bridges.json, phase4/merged-mq-{senderId}-{receiverId}.json
"""
import json, os, argparse, sys, copy, subprocess, re


def parse_args():
    p = argparse.ArgumentParser(description="Phase 4: MQ Bridge Matching")
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


def _extract_mq_senders(chain):
    """Find nodes with domainInteraction type=MQ (MQ_PUBLISH endpoints)."""
    senders = []
    for node in chain:
        di = node.get("domainInteraction")
        if di and di.get("type") == "MQ" and di.get("direction") == "OUT":
            topic = di.get("target", "")
            if topic:
                senders.append({
                    "nodeId": node["nodeId"],
                    "topic": topic,
                    "node": node,
                })
    return senders


def _find_mq_listeners(project_dir):
    """Search project source for @KafkaListener/@JmsListener annotations.

    Returns a dict: topic -> list of {method, filePath, class, topic}
    """
    index = {}
    java_dirs = []
    for root, dirs, files in os.walk(project_dir):
        # Only walk src/main/java
        if "src/main/java" in root:
            java_dirs.append(root)

    if not java_dirs:
        return index

    # Grep for listener annotations
    for java_dir in java_dirs:
        try:
            result = subprocess.run(
                ["grep", "-rn", "-E", "@(KafkaListener|JmsListener|RabbitListener|RocketMQMessageListener)", java_dir],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                continue

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue
                # Format: file:lineNum:content
                parts = line.split(":", 2)
                if len(parts) < 3:
                    continue
                file_path, _, content = parts

                # Extract topics from annotation
                topics = _extract_topics_from_annotation(content)
                for topic in topics:
                    if topic not in index:
                        index[topic] = []
                    index[topic].append({
                        "topic": topic,
                        "filePath": file_path,
                        "line": content.strip(),
                    })
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    return index


def _extract_topics_from_annotation(content):
    """Extract topic names from listener annotation line."""
    topics = []

    # @KafkaListener(topics = "xxx") or @KafkaListener(topics = {"xxx", "yyy"})
    m = re.search(r'topics\s*=\s*"([^"]+)"', content)
    if m:
        topics.append(m.group(1))
        return topics

    m = re.search(r'topics\s*=\s*\{([^}]+)\}', content)
    if m:
        for topic_match in re.finditer(r'"([^"]+)"', m.group(1)):
            topics.append(topic_match.group(1))
        return topics

    # @JmsListener(destination = "xxx")
    m = re.search(r'destination\s*=\s*"([^"]+)"', content)
    if m:
        topics.append(m.group(1))
        return topics

    # @RabbitListener(queues = "xxx")
    m = re.search(r'queues\s*=\s*"([^"]+)"', content)
    if m:
        topics.append(m.group(1))
        return topics

    return topics


def _create_bridge_node(sender_node, listener_info, layer_offset):
    """Create a virtual BRIDGE node connecting MQ sender and listener."""
    topic = ""
    if isinstance(listener_info, dict):
        topic = listener_info.get("topic", "")
    return {
        "nodeId": f"BRIDGE:MQ:{sender_node['nodeId']}->{topic}",
        "class": "BRIDGE",
        "method": "mq-bridge",
        "package": "",
        "filePath": "",
        "layer": layer_offset,
        "layerType": "BRIDGE",
        "parentId": sender_node["nodeId"],
        "callType": "BRIDGE",
        "terminal": False,
        "description": f"MQ bridge: {topic}",
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


def do_mq_bridge(args):
    entries = _load_json(args.entries)

    # Build MQ listener index from project source
    listener_index = _find_mq_listeners(args.project_dir)

    bridges = []
    merged_flows = []

    for entry in entries.get("entries", []):
        entry_id = entry["id"]
        # Read phase5 output (RMB bridge may have already processed this)
        phase5_path = os.path.join(args.cache_dir, "phase4", f"{entry_id}.json")
        pruned_path = os.path.join(args.cache_dir, "phase3", f"{entry_id}-pruned.json")

        # Prefer phase5 output (post RMB bridge), fallback to phase4
        data_path = phase5_path if os.path.exists(phase5_path) else pruned_path
        if not os.path.exists(data_path):
            print(f"  SKIP: {entry_id} - no data file", file=sys.stderr)
            continue

        data = _load_json(data_path)
        chain = data.get("chain", [])
        mq_senders = _extract_mq_senders(chain)

        if not mq_senders:
            print(f"  {entry_id}: no MQ senders")
            continue

        # Process each MQ sender
        for sender_info in mq_senders:
            topic = sender_info["topic"]
            sender_node = sender_info["node"]

            matching_listeners = listener_index.get(topic, [])

            if not matching_listeners:
                bridge_info = {
                    "type": "MQ",
                    "topic": topic,
                    "matchingStatus": "UNMATCHED",
                    "senderHandlerId": entry_id,
                    "receiverHandlerId": None,
                    "isExternal": True,
                }
                bridges.append(bridge_info)
                print(f"  {entry_id}: UNMATCHED MQ -> {topic}")
                continue

            for listener in matching_listeners:
                bridge_info = {
                    "type": "MQ",
                    "topic": topic,
                    "matchingStatus": "MATCHED",
                    "senderHandlerId": entry_id,
                    "receiverHandlerId": listener.get("filePath", ""),
                    "isExternal": False,
                }

                # Find max layer in sender chain
                sender_max_layer = max(n["layer"] for n in chain)

                # Create bridge node
                bridge_node = _create_bridge_node(sender_node, listener, sender_max_layer + 1)

                # Build receiver stub from listener info
                receiver_stub = [{
                    "nodeId": f"MQ_LISTENER:{listener.get('filePath', '')}",
                    "class": "MQ_LISTENER",
                    "method": listener.get("line", ""),
                    "package": "",
                    "filePath": listener.get("filePath", ""),
                    "layer": sender_max_layer + 2,
                    "layerType": "ENTRY",
                    "parentId": bridge_node["nodeId"],
                    "callType": "BRIDGE",
                    "terminal": False,
                    "description": "",
                    "domainInteraction": None,
                }]

                # Combine: sender chain + bridge + receiver stub
                merged_chain = list(chain) + [bridge_node] + receiver_stub

                merged = {
                    "entryId": f"{entry_id}-mq-{topic}",
                    "flowStatus": "VALID",
                    "flowType": "MERGED_MQ_FLOW",
                    "chain": merged_chain,
                    "mqBridge": bridge_info,
                    "summary": {
                        "retained": len(merged_chain),
                        "pruned": 0,
                        "terminals": sum(1 for n in merged_chain if n.get("terminal")),
                    },
                }

                safe_topic = re.sub(r'[^a-zA-Z0-9_-]', '_', topic)
                merged_path = os.path.join(
                    args.cache_dir, "phase4", f"merged-mq-{entry_id}-{safe_topic}.json"
                )
                _save_json(merged_path, merged)
                bridges.append(bridge_info)
                merged_flows.append(merged_path)
                print(f"  {entry_id}: MERGED MQ via topic={topic}")

    # Save bridges index
    bridges_path = os.path.join(args.cache_dir, "phase4", "mq-bridges.json")
    _save_json(bridges_path, {
        "version": "2.0",
        "generator": "flow-trace-java",
        "bridgeType": "MQ",
        "totalBridges": len(bridges),
        "matched": sum(1 for b in bridges if b["matchingStatus"] == "MATCHED"),
        "unmatched": sum(1 for b in bridges if b["matchingStatus"] == "UNMATCHED"),
        "bridges": bridges,
    })

    print(f"\nMQ Bridge Complete!")
    print(f"  Bridges: {len(bridges)}")
    print(f"  Matched: {sum(1 for b in bridges if b['matchingStatus'] == 'MATCHED')}")
    print(f"  Unmatched: {sum(1 for b in bridges if b['matchingStatus'] == 'UNMATCHED')}")
    print(f"  Merged flows: {len(merged_flows)}")


if __name__ == '__main__':
    do_mq_bridge(parse_args())
