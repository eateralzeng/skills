"""Phase 4: RMB Bridge Matching for flow-trace-db"""
import json, glob, os, re as regex, sqlite3, argparse


def parse_args():
    p = argparse.ArgumentParser(description="Phase 4: RMB Bridge Matching")
    p.add_argument("project_dir", help="Java project root directory")
    p.add_argument("cache_dir", help="Cache root directory (.trace-cache/)")
    p.add_argument("entries_path", help="Path to entry list JSON (entry-list-all.json)")
    return p.parse_args()


def build_sender_topic_map(project_dir):
    mapping = {}
    rmb_api_dir = f"{project_dir}/cbrc-rmb-meta/src/main/java/com/webank/cbrc/meta"
    constants = {}
    for api_file in glob.glob(f"{rmb_api_dir}/*RmbApi*.java"):
        cls_name = os.path.basename(api_file).replace('.java', '')
        with open(api_file) as f:
            content = f.read()
        for m in regex.finditer(r'public\s+static\s+final\s+String\s+(\w+)\s*=\s*"([^"]+)"', content):
            constants[f"{cls_name}.{m.group(1)}"] = m.group(2)

    print(f"  Found {len(constants)} RMB API constants")

    for client_file in glob.glob(f"{project_dir}/**/client/**/*.java", recursive=True):
        with open(client_file) as f:
            content = f.read()
        if '@RmbClient' not in content:
            continue
        cls_match = regex.search(r'(?:public\s+)?(?:interface|class)\s+(\w+)', content)
        if not cls_match:
            continue
        cls_name = cls_match.group(1)

        topic_pattern = regex.compile(
            r'@RmbTopic\s*\(\s*topic\s*=\s*(\w+\.\w+)'
            r'(?:[^)]*topicMode\s*=\s*(?:TopicMode\.)?(\w+))?'
            r'[^)]*\)\s*(?:@\w+(?:\([^)]*\))?\s*)*'
            r'\s*RmbResponse\s+(\w+)\s*\(',
            regex.DOTALL
        )

        for m in topic_pattern.finditer(content):
            topic_ref = m.group(1)
            topic_mode = m.group(2) or "SYNC"
            method_name = m.group(3)
            actual_topic = constants.get(topic_ref, topic_ref)

            key = f"{cls_name}.{method_name}"
            mapping[key] = {
                "topic": actual_topic,
                "topicMode": topic_mode,
                "topicRef": topic_ref,
            }

        # Handle @RmbTopics multi-route annotations
        topics_block_pattern = regex.compile(
            r'@RmbTopics\s*\(\s*rmbTopics\s*=\s*\{(.*?)\}\s*\)\s+'
            r'RmbResponse\s+(\w+)\s*\(',
            regex.DOTALL
        )
        inner_topic_pattern = regex.compile(r'@RmbTopic\s*\(([^)]+)\)')

        for block_m in topics_block_pattern.finditer(content):
            inner_content = block_m.group(1)
            method_name = block_m.group(2)

            routes = []
            seen_topics = set()
            for inner_m in inner_topic_pattern.finditer(inner_content):
                inner_text = inner_m.group(1)

                topic_match = regex.search(r'\btopic\s*=\s*(\w+\.\w+)', inner_text)
                if not topic_match:
                    continue
                topic_ref = topic_match.group(1)

                key_match = regex.search(r'\bkey\s*=\s*(\w+\.\w+)', inner_text)
                route_key = key_match.group(1) if key_match else ""

                mode_match = regex.search(r'\btopicMode\s*=\s*(?:TopicMode\.)?(\w+)', inner_text)
                topic_mode = mode_match.group(1) if mode_match else "SYNC"

                actual_topic = constants.get(topic_ref, topic_ref)

                # Deduplicate: same topic from different route keys only stored once
                if actual_topic in seen_topics:
                    continue
                seen_topics.add(actual_topic)

                routes.append({
                    "topic": actual_topic,
                    "topicMode": topic_mode,
                    "topicRef": topic_ref,
                    "routeKey": route_key,
                })

            if routes:
                key = f"{cls_name}.{method_name}"
                mapping[key] = {
                    "multiRoute": True,
                    "routes": routes,
                    "topic": routes[0]["topic"],
                    "topicMode": routes[0]["topicMode"],
                    "topicRef": routes[0]["topicRef"],
                }

    print(f"  Built sender topic map: {len(mapping)} methods")
    for k, v in sorted(mapping.items()):
        print(f"    {k} → topic={v['topic']} ({v['topicMode']})")
    return mapping, constants


def build_receiver_topic_map(project_dir, constants):
    mapping = {}
    for handler_file in glob.glob(f"{project_dir}/**/handler/**/*.java", recursive=True):
        with open(handler_file) as f:
            content = f.read()
        if '@RmbTopic' not in content:
            continue
        cls_match = regex.search(r'(?:public\s+)?(?:class|interface)\s+(\w+)', content)
        if not cls_match:
            continue
        cls_name = cls_match.group(1)

        for m in regex.finditer(r'@RmbTopic\s*\(\s*topic\s*=\s*(\w+\.\w+)', content):
            topic_ref = m.group(1)
            actual_topic = constants.get(topic_ref, topic_ref)
            if actual_topic not in mapping:
                mapping[actual_topic] = []
            mapping[actual_topic].append({
                "class": cls_name,
                "file": handler_file.replace(project_dir + "/", ""),
            })

    print(f"  Found {len(mapping)} unique receiver topics")
    for topic, recvs in sorted(mapping.items()):
        for r in recvs:
            print(f"    {r['class']} ← topic={topic}")
    return mapping


def do_phase4():
    args = parse_args()
    project_dir = args.project_dir
    cache_dir = args.cache_dir
    entries_path = args.entries_path

    print("Phase 4: RMB Bridge Matching")
    print("=" * 50)

    # Load entries
    with open(entries_path) as f:
        raw = json.load(f)
    if isinstance(raw, dict) and "entries" in raw:
        entries = raw["entries"]
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = raw.get("entries", [])

    # Normalize fields
    entry_by_class_type = {}
    for e in entries:
        cls = e.get("className", e.get("class", ""))
        typ = e.get("type", "")
        if cls and typ == "rmb":
            entry_by_class_type.setdefault(cls, []).append(e)

    print("\n[Step 1] Building sender topic map...")
    sender_map, constants = build_sender_topic_map(project_dir)

    print("\n[Step 2] Building receiver topic map...")
    receiver_by_topic = build_receiver_topic_map(project_dir, constants)

    print("\n[Step 3] Matching senders to receivers...")

    bridges = []
    merged_count = 0
    unmatched_senders = []

    chains_dir = os.path.join(cache_dir, "phase3")

    for entry in entries:
        eid = entry.get("id", "")
        chain_file = os.path.join(chains_dir, f"{eid}.json")
        if not os.path.exists(chain_file):
            continue

        with open(chain_file) as f:
            chain_data = json.load(f)

        chain = chain_data.get('chain', [])

        for node in chain:
            if node.get('layerType') != 'RMB_CLIENT':
                continue

            sender_cls = node['class']
            sender_method = node['method']
            sender_key = f"{sender_cls}.{sender_method}"

            di = node.get('domainInteraction', {})
            sender_info = sender_map.get(sender_key)

            # Build list of routes to try (multi-route or single)
            if sender_info and sender_info.get('multiRoute'):
                route_list = sender_info['routes']
            elif sender_info:
                route_list = [{'topic': sender_info['topic'], 'topicMode': sender_info.get('topicMode', 'SYNC')}]
            else:
                route_list = [{'topic': di.get('target', ''), 'topicMode': 'UNKNOWN'}]

            for route in route_list:
                actual_topic = route['topic']
                topic_mode = route.get('topicMode', 'SYNC')

                matched_receivers = receiver_by_topic.get(actual_topic, [])

                if matched_receivers:
                    for recv_info in matched_receivers:
                        # Find receiver chain file by class name
                        receiver_entry_id = None
                        receiver_chain_data = None
                        rmb_entries = entry_by_class_type.get(recv_info['class'], [])
                        for pe in rmb_entries:
                            rc_file = os.path.join(chains_dir, f"{pe['id']}.json")
                            if os.path.exists(rc_file):
                                receiver_entry_id = pe['id']
                                with open(rc_file) as rf:
                                    receiver_chain_data = json.load(rf)
                                break

                        if receiver_entry_id and receiver_chain_data:
                            merged_id = f"merged-rmb-{merged_count + 1:03d}"

                            sender_chain = [n for n in chain if n.get('layer', 0) <= node.get('layer', 0)]

                            bridge_layer = node.get('layer', 0) + 1
                            bridge_node = {
                                "layer": bridge_layer,
                                "layerType": "BRIDGE",
                                "class": "RMB_BRIDGE",
                                "method": actual_topic,
                                "description": f"RMB 桥接: {sender_cls}.{sender_method} → {recv_info['class']}",
                                "parentLayer": node.get('layer', 0),
                                "parentId": node.get("nodeId", ""),
                                "source": "bridge"
                            }

                            receiver_chain = receiver_chain_data.get('chain', [])
                            adjusted_receiver = []
                            for rn in receiver_chain:
                                adj = dict(rn)
                                orig_layer = rn.get('layer', 0)
                                adj['layer'] = bridge_layer + orig_layer
                                if orig_layer > 0:
                                    adj['parentLayer'] = bridge_layer + rn.get('parentLayer', 0)
                                adjusted_receiver.append(adj)

                            merged_chain = sender_chain + [bridge_node] + adjusted_receiver

                            bridge_record = {
                                "mergedFlowId": merged_id,
                                "senderEntryId": eid,
                                "receiverEntryId": receiver_entry_id,
                                "topic": actual_topic,
                                "topicMode": topic_mode,
                                "senderNode": {
                                    "class": sender_cls,
                                    "method": sender_method,
                                    "layer": node.get('layer', 0),
                                },
                                "receiverNode": {
                                    "class": recv_info['class'],
                                    "layer": bridge_layer + 1,
                                },
                                "matchingStatus": "MATCHED",
                                "isExternal": False,
                                "matchBy": "topic",
                                "matchDescription": f"从 @RmbClient 接口注解 @RmbTopic(topic=...) 提取 topic 常量，解析为实际值 {actual_topic}；在接收端扫描 @RmbController 类的 @RmbTopic 注解，按 topic 字符串精确匹配",
                            }

                            merged_flow = {
                                "entryId": merged_id,
                                "entryType": "MERGED_RMB_FLOW",
                                "bridgeInfo": bridge_record,
                                "status": "COMPLETE",
                                "chain": merged_chain,
                            }

                            phase4_dir = os.path.join(cache_dir, "phase4")
                            os.makedirs(phase4_dir, exist_ok=True)
                            with open(os.path.join(phase4_dir, f"{merged_id}.json"), 'w') as mf:
                                json.dump(merged_flow, mf, indent=2, ensure_ascii=False)

                            bridges.append(bridge_record)
                            merged_count += 1
                            print(f"  MATCHED: {eid} --[{actual_topic}]--> {receiver_entry_id} ({recv_info['class']})")
                        else:
                            print(f"  NO_CHAIN: topic={actual_topic} receiver={recv_info['class']}")
                            unmatched_senders.append({
                                "senderEntryId": eid,
                                "senderKey": sender_key,
                                "topic": actual_topic,
                                "reason": "RECEIVER_CHAIN_MISSING",
                            })
                else:
                    unmatched_senders.append({
                        "senderEntryId": eid,
                        "senderKey": sender_key,
                        "topic": actual_topic,
                    })
                    bridge_record = {
                        "mergedFlowId": None,
                        "senderEntryId": eid,
                        "receiverEntryId": None,
                        "topic": actual_topic,
                        "topicMode": topic_mode,
                        "senderNode": {
                            "class": sender_cls,
                            "method": sender_method,
                            "layer": node.get('layer', 0),
                        },
                        "matchingStatus": "UNMATCHED",
                        "isExternal": True,
                        "reason": f"No @RmbController for topic={actual_topic} in project",
                        "matchBy": "topic",
                        "matchDescription": f"发送端 topic={actual_topic}，在项目中未找到匹配的 @RmbController 接收端",
                    }
                    bridges.append(bridge_record)
                    print(f"  UNMATCHED: {eid} --[{actual_topic}]--> (external)")

    sender_bridge_index = {}
    receiver_bridge_index = {}
    for b in bridges:
        if b.get('senderEntryId'):
            sender_bridge_index.setdefault(b['senderEntryId'], []).append(b)
        if b.get('receiverEntryId'):
            receiver_bridge_index.setdefault(b['receiverEntryId'], []).append(b)

    bridges_output = {
        "totalBridges": len(bridges),
        "matchedBridges": merged_count,
        "unmatchedSenders": len(unmatched_senders),
        "bridges": bridges,
        "senderBridgeIndex": sender_bridge_index,
        "receiverBridgeIndex": receiver_bridge_index,
    }
    with open(os.path.join(cache_dir, "phase4", 'bridges.json'), 'w') as f:
        json.dump(bridges_output, f, indent=2, ensure_ascii=False)

    print(f"\nPhase 4 Complete:")
    print(f"  Total bridges: {len(bridges)}")
    print(f"  Matched: {merged_count}")
    print(f"  Unmatched senders: {len(unmatched_senders)}")


if __name__ == '__main__':
    do_phase4()
