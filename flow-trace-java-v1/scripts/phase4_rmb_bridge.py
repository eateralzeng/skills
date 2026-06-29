"""Phase 4: RMB Bridge — in-place 补全（决策 10）

从入口（controller/job/孤立 rmb）DFS 展开 RMB 链路：sender → BRIDGE → receiver，
receiver chain 递归展开其下游 RMB（多级），in-place 拼回 sender 入口的 chain。
matched receiver 不再独立（bridges.json.matchedReceivers，phase5/6 跳过）。

Input:  phase4/{entryId}.json（含 4a DISPATCH 挂载）/ phase3/{entryId}-pruned.json,
        phase1a/entries.json
Output: phase4/{entryId}.json（in-place 含完整跨进程链路）, phase4/bridges.json
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


def _load_flow_data(cache_dir, entry_id):
    """Load flow data, preferring phase4 (post-dispatch-merge) over phase3.

    Phase 4a (dispatch_merge) writes phase4/{entryId}.json with DISPATCH
    children already mounted. Reading phase4 first preserves those mounts
    instead of overwriting them with the raw phase3 pruned tree.
    """
    for phase, suffix in (("phase4", f"{entry_id}.json"),
                          ("phase3", f"{entry_id}-pruned.json")):
        path = os.path.join(cache_dir, phase, suffix)
        if os.path.exists(path):
            return _load_json(path), path
    return None, None


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
        if not topic and "rmbTopic" in entry:
            topic = entry["rmbTopic"]
        if topic:
            if topic not in index:
                index[topic] = []
            index[topic].append(entry)
    return index


def _find_receiver_chain(cache_dir, receiver_entry_id):
    """Load the receiver chain, preferring phase4 (post-dispatch-merge)."""
    data, _ = _load_flow_data(cache_dir, receiver_entry_id)
    if data is None:
        return None
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
        "parentId": [sender_node["nodeId"]],
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


def _expand_chain_rmb(chain, receiver_index, visited, cache_dir, sender_entry_id):
    """决策 10：DFS 递归展开 chain 里的 RMB sender，in-place 拼 receiver chain。

    多级链路（controller→rmb-018→rmb-999）：递归展开 receiver chain 的下游 RMB。
    防环：visited 记录已拼接的 receiver entryId，命中即跳过。
    返回 (expanded_chain, bridges)，bridges 含本次展开产生的 bridge_info。
    """
    bridges = []
    tail = []
    base_max = max((n.get("layer", 0) for n in chain), default=0)
    for sender_info in _extract_rmb_nodes(chain):
        sender_node = sender_info["node"]
        topic = sender_info["topic"]
        sender_trans = sender_info.get("transCode")

        matching = receiver_index.get(topic, [])
        if sender_trans is not None:
            matching = [r for r in matching
                        if r.get("transCode") is None or r.get("transCode") == sender_trans]

        if not matching:
            bridges.append({
                "topic": topic, "topicMode": "SYNC", "transCode": None,
                "matchingStatus": "UNMATCHED", "senderHandlerId": sender_entry_id,
                "receiverHandlerId": None, "isExternal": True,
            })
            continue

        for recv_entry in matching:
            recv_id = recv_entry["id"]
            if recv_id in visited:
                continue  # 防环
            visited.add(recv_id)

            recv_chain = _find_receiver_chain(cache_dir, recv_id)
            if not recv_chain:
                bridges.append({
                    "topic": topic, "topicMode": recv_entry.get("rmbTopicMode") or "SYNC",
                    "transCode": None, "matchingStatus": "UNMATCHED",
                    "senderHandlerId": sender_entry_id, "receiverHandlerId": recv_id,
                    "isExternal": False,
                })
                continue

            # 递归展开 receiver chain 的下游 RMB（多级）
            recv_expanded, recv_bridges = _expand_chain_rmb(
                recv_chain, receiver_index, visited, cache_dir, recv_id)
            bridges.extend(recv_bridges)

            bridge_node = _create_bridge_node(
                sender_node,
                recv_expanded[0] if recv_expanded else {"nodeId": "unknown"},
                base_max + 1)
            recv_remapped = _remap_layers(recv_expanded, base_max + 2)
            if recv_remapped:
                recv_remapped[0]["parentId"] = [bridge_node["nodeId"]]

            tail.append(bridge_node)
            tail.extend(recv_remapped)
            bridges.append({
                "topic": topic, "topicMode": recv_entry.get("rmbTopicMode") or "SYNC",
                "transCode": None, "matchingStatus": "MATCHED",
                "senderHandlerId": sender_entry_id, "receiverHandlerId": recv_id,
                "isExternal": False,
            })

    return chain + tail, bridges


def do_bridge(args):
    """决策 10：RMB 桥接 in-place 补全。

    两遍：
      Pass 1：遍历所有 entry 找 RMB sender，匹配 receiver → matched_receivers 集合
              （遍历所有 entry 即覆盖多级，因 rmb-N 作为 entry 也会被找 sender）
      Pass 2：对非 matched 的入口 entry DFS in-place 拼 receiver chain，写回
              phase4/{entry}.json
    matched receivers 记到 bridges.json.matchedReceivers（phase5/6 跳过）。
    不再产 merged-rmb-*.json。
    """
    args.cache_dir = os.path.abspath(args.cache_dir)
    entries = _load_json(args.entries)
    receiver_index = _build_rmb_receiver_index(entries)

    # Pass 1: matched_receivers（被任何 sender 匹配的 receiver，遍历所有 entry 即覆盖多级）
    matched_receivers = set()
    for entry in entries.get("entries", []):
        data, _ = _load_flow_data(args.cache_dir, entry["id"])
        if not data or data.get("flowStatus") == "NO_ENDPOINT":
            continue
        for s in _extract_rmb_nodes(data.get("chain", [])):
            matching = receiver_index.get(s["topic"], [])
            if s.get("transCode") is not None:
                matching = [r for r in matching
                            if r.get("transCode") is None or r.get("transCode") == s["transCode"]]
            matched_receivers.update(r["id"] for r in matching)

    # Pass 2: 对入口（非 matched）DFS in-place 拼
    bridges = []
    for entry in entries.get("entries", []):
        entry_id = entry["id"]
        data, _ = _load_flow_data(args.cache_dir, entry_id)
        if data is None:
            print(f"  SKIP: {entry_id} - no flow data", file=sys.stderr)
            continue
        if entry_id in matched_receivers:
            print(f"  {entry_id}: MATCHED receiver (skipped, merged into sender)")
            continue
        if data.get("flowStatus") == "NO_ENDPOINT":
            data["rmbBridge"] = None
            _save_json(os.path.join(args.cache_dir, "phase4", f"{entry_id}.json"), data)
            print(f"  {entry_id}: NO_ENDPOINT (copied as-is)")
            continue
        visited = {entry_id}
        expanded, entry_bridges = _expand_chain_rmb(
            data.get("chain", []), receiver_index, visited, args.cache_dir, entry_id)
        bridges.extend(entry_bridges)
        data["chain"] = expanded
        has_matched = any(b["matchingStatus"] == "MATCHED" for b in entry_bridges)
        data["flowType"] = "MERGED_RMB_FLOW" if has_matched else "STANDALONE_FLOW"
        data["rmbBridge"] = {
            "topics": [s["topic"] for s in _extract_rmb_nodes(data.get("chain", []))],
            "matchingStatus": "MATCHED" if has_matched else "UNMATCHED",
        }
        _save_json(os.path.join(args.cache_dir, "phase4", f"{entry_id}.json"), data)
        print(f"  {entry_id}: {'MERGED' if has_matched else 'STANDALONE'} ({len(expanded)} nodes)")

    # 去重：共享场景下同一 sender→receiver→topic 关系会被多入口各展开一次，只记唯一关系
    seen = set()
    deduped = []
    for b in bridges:
        key = (b["senderHandlerId"], b["receiverHandlerId"], b["topic"], b["matchingStatus"])
        if key not in seen:
            seen.add(key)
            deduped.append(b)
    bridges = deduped

    matched_list = sorted(matched_receivers)
    _save_json(os.path.join(args.cache_dir, "phase4", "bridges.json"), {
        "version": "2.0",
        "generator": "flow-trace-java-v1",
        "matchedReceivers": matched_list,  # phase5/6 跳过这些 entry
        "totalBridges": len(bridges),
        "matched": sum(1 for b in bridges if b["matchingStatus"] == "MATCHED"),
        "unmatched": sum(1 for b in bridges if b["matchingStatus"] == "UNMATCHED"),
        "bridges": bridges,
    })

    print(f"\nPhase 4 Complete!")
    print(f"  Entrypoints: {len(entries.get('entries', [])) - len(matched_list)}, "
          f"matched receivers removed: {len(matched_list)}")
    print(f"  Bridges: {len(bridges)} "
          f"(matched {sum(1 for b in bridges if b['matchingStatus'] == 'MATCHED')}, "
          f"unmatched {sum(1 for b in bridges if b['matchingStatus'] == 'UNMATCHED')})")


if __name__ == '__main__':
    do_bridge(parse_args())
