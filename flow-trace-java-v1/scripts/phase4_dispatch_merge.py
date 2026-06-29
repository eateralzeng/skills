"""Phase 4a: DISPATCH Merge for flow-trace-java

Mounts dispatch-summary endpoints as children of DISPATCH terminal nodes
in pruned trees. Runs before other bridge scripts (RMB/MQ/Event/Async).

Input:  phase3/{entryId}-pruned.json (or phase4/{entryId}.json),
        phase2b/dispatch-summary-*.json,
        phase1c/pattern-index.json
Output: phase4/{entryId}.json (for entries with DISPATCH nodes),
        phase4/dispatch-merge-report.json
"""
import json, os, argparse, sys, glob, copy


def parse_args():
    p = argparse.ArgumentParser(description="Phase 4a: DISPATCH Merge")
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


_TYPE_PREFIX = {"STREAM_DISPATCH", "MAP_DISPATCH", "SWITCH_DISPATCH", "STRATEGY_DISPATCH",
                "ANNOTATION_DISPATCH", "RESPONSIBILITY_CHAIN", "UNKNOWN", "pattern-index"}


def normalize_pattern_ref(pr):
    """方案A：patternRef → dispatchKey（module:package.class）。
    剥 #method 后缀、type 前缀（STREAM_DISPATCH:/pattern-index: 等）、:method 后缀，
    取 module:fqn。A-2 后 patternRef 已是干净 dispatchKey，本函数主要为防御性兜底。"""
    if not pr:
        return ""
    pr = pr.split('#', 1)[0]
    parts = [p for p in pr.split(':') if p not in _TYPE_PREFIX]
    fqns = [p for p in parts if '.' in p]
    if not fqns:
        return parts[0] if parts else ""  # 只有短名（脏）→ 匹配会失败（unmatched），属正常
    fqn = fqns[-1]
    idx = parts.index(fqn)
    module = parts[idx - 1] if idx > 0 and '.' not in parts[idx - 1] else ""
    return f"{module}:{fqn}"


def load_dispatch_summaries(cache_dir):
    """Load all dispatch-summary files, indexed by dispatchKey（方案A）."""
    phase2b_dir = os.path.join(cache_dir, "phase2b")
    summaries = {}
    for fpath in sorted(glob.glob(os.path.join(phase2b_dir, "dispatch-summary-*.json"))):
        data = _load_json(fpath)
        dk = data.get("dispatchKey")
        if dk:
            summaries[dk] = data
    return summaries


def find_dispatch_nodes(chain):
    """Find DISPATCH terminal nodes in chain."""
    return [n for n in chain if n.get("endpointType") == "DISPATCH"]


def build_endpoint_children(summary, parent_layer):
    """Build child nodes from dispatch-summary results.

    Deduplicates endpoints: same (class, method) from different implementations
    are merged, with dispatchImpl listing all implementations.
    """
    # Group endpoints by (class, method) for dedup
    ep_map = {}
    for impl in summary.get("results", []):
        impl_name = impl.get("shortName", impl.get("class", "").rsplit(".", 1)[-1])
        condition = impl.get("condition", "unknown")
        for ep in impl.get("endpoints", []):
            ep_class = ep.get("class", "")
            ep_method = ep.get("method", "")
            key = (ep_class, ep_method)
            if key not in ep_map:
                ep_map[key] = {
                    "implementations": [],
                    "conditions": [],
                    "endpoint": ep,
                }
            ep_map[key]["implementations"].append(impl_name)
            ep_map[key]["conditions"].append(condition)

    children = []
    for key, info in ep_map.items():
        ep = info["endpoint"]
        ep_class = ep.get("class", key[0])
        ep_method = ep.get("method", key[1])
        ep_type = ep.get("type", "DATABASE")
        impl_list = list(dict.fromkeys(info["implementations"]))
        cond_list = list(dict.fromkeys(info["conditions"]))

        child = {
            "nodeId": f"DISPATCH:{ep_class}:{ep_method}",
            "class": ep_class,
            "method": ep_method,
            "filePath": ep.get("filePath", ""),
            "layer": parent_layer + 1,
            "layerType": "TERMINAL",
            "parentId": [],  # list（方案B），set later when mounted
            "callType": "DISPATCH_IMPL",
            "terminal": True,
            "endpointType": ep_type,
            "description": "",
            "domainInteraction": None,
            "dispatchImpl": ", ".join(impl_list),
            "dispatchCondition": ", ".join(cond_list) if len(cond_list) <= 3 else "多个实现类共用",
        }

        # Set domainInteraction based on endpoint type
        if ep_type == "DATABASE":
            child["domainInteraction"] = {
                "type": "DATABASE",
                "table": ep.get("table", "[待确认]"),
                "operation": ep.get("operation", ""),
            }
        elif ep_type in ("RMB_EXTERNAL", "HTTP_EXTERNAL"):
            child["domainInteraction"] = {
                "type": "EXTERNAL",
                "protocol": "RMB" if ep_type == "RMB_EXTERNAL" else "HTTP",
                "target": ep.get("class", ""),
            }
        elif ep_type == "MQ_PUBLISH":
            child["domainInteraction"] = {
                "type": "MQ",
                "target": ep.get("class", ""),
            }
        elif ep_type == "FILE_WRITE":
            child["domainInteraction"] = {
                "type": "FILE",
                "operation": "WRITE",
            }

        children.append(child)

    return children


def mount_dispatch_children(chain, dispatch_summaries):
    """Mount dispatch-summary endpoints as children of DISPATCH nodes."""
    # 幂等（ISSUE-4-14）：剔除上次挂载的 DISPATCH_IMPL 子节点，避免重跑翻倍
    # （DISPATCH_IMPL 均由本函数产生，重跑会按 summary 重建）
    chain = [n for n in chain if n.get("callType") != "DISPATCH_IMPL"]
    dispatch_nodes = find_dispatch_nodes(chain)
    if not dispatch_nodes:
        return chain, {"dispatchNodes": 0, "childrenMounted": 0}

    new_children = []
    stats = {"dispatchNodes": len(dispatch_nodes), "childrenMounted": 0}

    for dn in dispatch_nodes:
        pattern_ref = dn.get("patternRef", "")
        if not pattern_ref:
            continue

        # 方案A：规范化 patternRef → dispatchKey，与 summary.dispatchKey 全等匹配（根治短名冲突 + ISSUE-2b-17）
        dk = normalize_pattern_ref(pattern_ref)
        summary = dispatch_summaries.get(dk)
        if not summary:
            stats[f"unmatched_{pattern_ref}"] = True
            continue

        parent_layer = dn.get("layer", 0)
        children = build_endpoint_children(summary, parent_layer)
        for child in children:
            child["parentId"] = [dn["nodeId"]]
            new_children.append(child)

        stats["childrenMounted"] += len(children)

        # Mark parent as non-terminal now that it has children
        dn["terminal"] = False
        dn["layerType"] = "INTERMEDIATE"

    # Append new children to chain
    chain = chain + new_children
    return chain, stats


def main():
    args = parse_args()
    cache_dir = os.path.abspath(args.cache_dir)
    entries = _load_json(args.entries)

    # Load dispatch summaries
    summaries = load_dispatch_summaries(cache_dir)
    print(f"Loaded {len(summaries)} dispatch summaries")

    results = []
    total_dispatch = 0
    total_mounted = 0

    for entry in entries.get("entries", []):
        entry_id = entry.get("id")

        # Try phase4 first (previous script output), then phase3
        flow_data = None
        for phase in ("phase4", "phase3"):
            for suffix in (f"{entry_id}.json", f"{entry_id}-pruned.json"):
                path = os.path.join(cache_dir, phase, suffix)
                if os.path.exists(path):
                    flow_data = _load_json(path)
                    break
            if flow_data:
                break

        if not flow_data:
            print(f"  SKIP: {entry_id} - no flow data")
            continue

        chain = flow_data.get("chain", [])
        dispatch_nodes = find_dispatch_nodes(chain)

        if not dispatch_nodes:
            # No DISPATCH nodes, skip (other bridge scripts will handle)
            print(f"  {entry_id}: no DISPATCH nodes")
            continue

        # Mount children
        updated_chain, stats = mount_dispatch_children(chain, summaries)
        flow_data["chain"] = updated_chain

        # Update summary
        if "summary" in flow_data:
            old_terminals = flow_data["summary"].get("terminals", 0)
            flow_data["summary"]["terminals"] = old_terminals - stats["dispatchNodes"] + stats["childrenMounted"]

        # Save to phase4
        out_path = os.path.join(cache_dir, "phase4", f"{entry_id}.json")
        _save_json(out_path, flow_data)

        total_dispatch += stats["dispatchNodes"]
        total_mounted += stats["childrenMounted"]
        results.append({
            "entryId": entry_id,
            "dispatchNodes": stats["dispatchNodes"],
            "childrenMounted": stats["childrenMounted"],
        })
        print(f"  {entry_id}: {stats['dispatchNodes']} DISPATCH -> {stats['childrenMounted']} children")

    # Save report
    report = {
        "totalDispatchNodes": total_dispatch,
        "totalChildrenMounted": total_mounted,
        "entries": results,
    }
    _save_json(os.path.join(cache_dir, "phase4", "dispatch-merge-report.json"), report)

    print(f"\nPhase 4a DISPATCH Merge Complete!")
    print(f"  DISPATCH nodes: {total_dispatch}")
    print(f"  Children mounted: {total_mounted}")


if __name__ == "__main__":
    main()
