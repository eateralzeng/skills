#!/usr/bin/env python3
"""phase4 产物校验（独立实现，不 import skill 模块）。

校验对象：phase4/{entryId}.json（4a DISPATCH 挂载 + 4b RMB 桥接）+ phase4/bridges.json。
搭配设计：phase4-design.md（chain 3.2 / bridges 3.3 / dispatch_merge 4a / rmb_bridge 4b）。
独立性：不 import phase4_dispatch_merge/rmb_bridge；patternRef 规范化 + dispatchKey 匹配 +
DISPATCH 子节点重算（维度 E）在脚本内独立重实现，与 4a 实挂比对——能抓挂载/匹配 bug。

用法：
    python3 verify_phase4.py <project_dir> <cache_dir>
退出码：0 = 无 error；1 = 有 error（warn 不影响）。
"""
import json
import os
import sys
import glob
import argparse

VALID_FLOW_STATUS = {"VALID", "NO_ENDPOINT"}
VALID_FLOW_TYPE = {"STANDALONE_FLOW", "MERGED_RMB_FLOW"}
VALID_MATCH = {"MATCHED", "UNMATCHED"}
_TYPE_PREFIX = {"STREAM_DISPATCH", "MAP_DISPATCH", "SWITCH_DISPATCH", "STRATEGY_DISPATCH",
                "ANNOTATION_DISPATCH", "RESPONSIBILITY_CHAIN", "UNKNOWN", "pattern-index"}


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}


def normalize_pattern_ref(pr):
    """方案A：patternRef → dispatchKey（独立复刻 phase4_dispatch_merge）。"""
    if not pr:
        return ""
    pr = pr.split('#', 1)[0]
    parts = [p for p in pr.split(':') if p not in _TYPE_PREFIX]
    fqns = [p for p in parts if '.' in p]
    if not fqns:
        return parts[0] if parts else ""
    fqn = fqns[-1]
    idx = parts.index(fqn)
    module = parts[idx - 1] if idx > 0 and '.' not in parts[idx - 1] else ""
    return f"{module}:{fqn}"


def load_summaries_by_dk(cache_dir):
    """独立加载 dispatch-summary，按 dispatchKey 索引。"""
    summaries = {}
    for f in glob.glob(os.path.join(cache_dir, "phase2b", "dispatch-summary-*.json")):
        d = _load_json(f)
        dk = d.get("dispatchKey") if isinstance(d, dict) else None
        if dk:
            summaries[dk] = d
    return summaries


def summary_endpoints(summary):
    """summary 的 (ep.class, ep.method) 去重集（独立复刻 phase4 endpoint 去重）。"""
    eps = set()
    for r in summary.get("results", []):
        for ep in r.get("endpoints", []):
            eps.add((ep.get("class", ""), ep.get("method", "")))
    return eps


def verify_entry(entry_id, p4, p3, summaries_by_dk):
    errors, warns, oks = [], [], []
    tag = entry_id

    # ── A 顶层字段齐全（flowType 仅 VALID 入口必有；NO_ENDPOINT 被 rmb_bridge 原样拷贝不加）──
    miss = [k for k in ("entryId", "flowStatus", "chain", "prunedNodes", "summary") if k not in p4]
    if miss:
        errors.append(f"[A] {tag}: 顶层缺字段 {miss}")
        return errors, warns, oks
    if p4["flowStatus"] == "VALID" and "flowType" not in p4:
        errors.append(f"[A] {tag}: VALID 入口缺 flowType")
    oks.append(f"[A] {tag}: 顶层字段齐全")

    # ── B 枚举/一致 ──
    if p4["entryId"] != entry_id:
        errors.append(f"[B] {tag}: entryId={p4['entryId']!r} != 文件名")
    if p4["flowStatus"] not in VALID_FLOW_STATUS:
        errors.append(f"[B] {tag}: flowStatus={p4['flowStatus']!r} 非法")
    if p4.get("flowType") and p4["flowType"] not in VALID_FLOW_TYPE:
        errors.append(f"[B] {tag}: flowType={p4['flowType']!r} 非法枚举")

    chain = p4["chain"]
    chain_ids = {n.get("nodeId") for n in chain}
    by_id = {n.get("nodeId"): n for n in chain}

    # DISPATCH 父节点 + DISPATCH_IMPL 子节点
    dispatch_nodes = [n for n in chain if n.get("endpointType") == "DISPATCH"]
    impl_children = [n for n in chain if n.get("callType") == "DISPATCH_IMPL"]

    # ── C DISPATCH 父节点 4a 后 terminal=false + 有子 ──
    c_bad, f_unmatched = [], []
    for dn in dispatch_nodes:
        if dn.get("terminal"):
            c_bad.append(f"{dn['nodeId'].split(':')[-1]}:terminal仍true")
        # F：patternRef 须匹配到 summary
        dk = normalize_pattern_ref(dn.get("patternRef", ""))
        if dk not in summaries_by_dk:
            f_unmatched.append(f"{dn['nodeId'].split(':')[-1]}:{dn.get('patternRef')!r}")
    if c_bad:
        errors.append(f"[C] {tag}: {len(c_bad)} 个 DISPATCH 父节点状态异常: {c_bad[:3]}")
    if f_unmatched:
        errors.append(f"[F] {tag}: {len(f_unmatched)} 个 DISPATCH patternRef 未匹配 summary（ISSUE-2b-17 退回）: {f_unmatched[:3]}")

    # ── D DISPATCH_IMPL 子节点字段 ──
    d_bad = []
    for c in impl_children:
        if not c.get("nodeId", "").startswith("DISPATCH:"):
            d_bad.append(f"{c.get('nodeId','?')}:nodeId格式")
        if "dispatchImpl" not in c or "dispatchCondition" not in c:
            d_bad.append(f"{c.get('nodeId','?').split(':')[-1]}:缺dispatchImpl/Condition")
        # parentId 指向 DISPATCH 节点
        pids = c.get("parentId", [])
        if isinstance(pids, list):
            if not any(by_id.get(p, {}).get("endpointType") == "DISPATCH" for p in pids):
                d_bad.append(f"{c.get('nodeId','?').split(':')[-1]}:parent非DISPATCH")
    if d_bad:
        errors.append(f"[D] {tag}: {len(d_bad)} 个 DISPATCH_IMPL 子节点问题: {d_bad[:3]}")

    # ── E 独立重挂：每个 DISPATCH 节点实挂子 (class,method) == summary endpoints 去重 ──
    e_bad = []
    for dn in dispatch_nodes:
        dk = normalize_pattern_ref(dn.get("patternRef", ""))
        summary = summaries_by_dk.get(dk)
        if not summary:
            continue  # 已由 F 报
        expected = summary_endpoints(summary)
        actual = {(c.get("class", ""), c.get("method", ""))
                  for c in impl_children if dn["nodeId"] in (c.get("parentId") or [])}
        if expected != actual:
            e_bad.append(f"{dn['nodeId'].split(':')[-1]}: 实挂{len(actual)}/应{len(expected)}")
    if e_bad:
        errors.append(f"[E] {tag}: {len(e_bad)} 个 DISPATCH 挂载与 summary 不符（独立重挂）: {e_bad[:3]}")
    elif dispatch_nodes:
        oks.append(f"[E] {tag}: {len(dispatch_nodes)} 个 DISPATCH 挂载 == summary（独立重挂正确）")

    # ── J 跨阶段：phase4 chain ⊇ phase3 chain（只增不丢）──
    if p3 and "__error__" not in p3:
        p3_ids = {n.get("nodeId") for n in p3.get("chain", [])}
        lost = p3_ids - chain_ids
        if lost:
            errors.append(f"[J] {tag}: phase4 丢了 {len(lost)} 个 phase3 chain 节点: {[x.split(':')[-1] for x in list(lost)[:3]]}")
        else:
            oks.append(f"[J] {tag}: phase3 chain 节点全保留（+{len(chain_ids)-len(p3_ids)} 挂载/桥接）")

    return errors, warns, oks


def verify_bridges(bridges, entry_ids, all_entries):
    errors, warns, oks = [], [], []
    miss = [k for k in ("totalBridges", "matched", "unmatched", "bridges") if k not in bridges]
    if miss:
        errors.append(f"[G] bridges.json 顶层缺字段 {miss}")
        return errors, warns, oks
    bl = bridges["bridges"]
    # ── G 计数自洽 ──
    if bridges["matched"] + bridges["unmatched"] != bridges["totalBridges"]:
        errors.append(f"[G] bridges: matched+unmatched={bridges['matched']+bridges['unmatched']} != totalBridges={bridges['totalBridges']}")
    elif bridges["totalBridges"] != len(bl):
        errors.append(f"[G] bridges: totalBridges={bridges['totalBridges']} != len(bridges)={len(bl)}")
    else:
        oks.append(f"[G] bridges 计数自洽（{bridges['totalBridges']}：matched {bridges['matched']}/unmatched {bridges['unmatched']}）")
    # ── H/I 逐 bridge ──
    h_bad, i_bad = [], []
    for b in bl:
        if not b.get("topic"):
            h_bad.append("topic空")
        if b.get("matchingStatus") not in VALID_MATCH:
            h_bad.append(f"status={b.get('matchingStatus')!r}")
            continue
        if b["matchingStatus"] == "MATCHED":
            if not b.get("receiverHandlerId"):
                h_bad.append(f"{b.get('topic')}:MATCHED无receiver")
            elif b["receiverHandlerId"] not in entry_ids:
                i_bad.append(f"{b.get('topic')}:receiver {b['receiverHandlerId']} 非entry")
        else:  # UNMATCHED
            if not b.get("isExternal"):
                h_bad.append(f"{b.get('topic')}:UNMATCHED但isExternal=false")
        if b.get("senderHandlerId") and b["senderHandlerId"] not in entry_ids:
            i_bad.append(f"{b.get('topic')}:sender {b['senderHandlerId']} 非entry")
    if h_bad:
        errors.append(f"[H] bridges: {len(h_bad)} 条 bridge 字段问题: {h_bad[:3]}")
    if i_bad:
        errors.append(f"[I] bridges: {len(i_bad)} 条 sender/receiver 非 entry: {i_bad[:3]}")
    if not h_bad and not i_bad:
        oks.append(f"[H/I] {len(bl)} 条 bridge 字段 + sender/receiver 引用全合法")
    # ── I2 桥接匹配率监控（RMB 桥接修复：matched==0 但存在内部 receiver → 发送端 topic 提取失效）──
    has_internal_receiver = any(e.get("type") == "rmb" and e.get("rmbTopic") for e in all_entries)
    if bridges["totalBridges"] > 0 and bridges.get("matched", 0) == 0 and has_internal_receiver:
        warns.append(f"[I2] {bridges['totalBridges']} 条 bridge 全 UNMATCHED 但存在内部 RMB receiver "
                     f"—— 疑发送端 routingKeys.topic 提取失效（跨进程后半部分集体丢失）")
    elif bridges["totalBridges"] > 0:
        oks.append(f"[I2] 桥接匹配率：matched {bridges.get('matched',0)}/{bridges['totalBridges']}")
    return errors, warns, oks


def main():
    ap = argparse.ArgumentParser(description="phase4 产物校验（独立实现，不 import skill）")
    ap.add_argument("project_dir")
    ap.add_argument("cache_dir")
    args = ap.parse_args()

    cache_dir = os.path.abspath(args.cache_dir)
    p4 = os.path.join(cache_dir, "phase4")
    p3 = os.path.join(cache_dir, "phase3")
    if not os.path.isdir(p4):
        print(f"❌ 找不到 {p4}（phase4 是否已运行？）")
        sys.exit(1)

    summaries_by_dk = load_summaries_by_dk(cache_dir)
    entry_ids = set()
    all_entries = []
    ej = os.path.join(cache_dir, "phase1a", "entries.json")
    if os.path.exists(ej):
        all_entries = _load_json(ej).get("entries", [])
        entry_ids = {e.get("id") for e in all_entries}

    all_errs, t_err, t_warn, t_ok = [], 0, 0, 0
    for f in sorted(glob.glob(os.path.join(p4, "*.json"))):
        base = os.path.basename(f)
        if base in ("bridges.json", "dispatch-merge-report.json") or base.startswith("merged-"):
            continue
        entry_id = base[:-len(".json")]
        data = _load_json(f)
        if "__error__" in data:
            all_errs.append(f"[A] {entry_id}: JSON 损坏"); t_err += 1; continue
        p3d = _load_json(os.path.join(p3, f"{entry_id}-pruned.json"))
        e, w, o = verify_entry(entry_id, data, p3d, summaries_by_dk)
        all_errs += e; t_err += len(e); t_warn += len(w); t_ok += len(o)

    # bridges.json
    bpath = os.path.join(p4, "bridges.json")
    if os.path.exists(bpath):
        bd = _load_json(bpath)
        if "__error__" not in bd:
            e, w, o = verify_bridges(bd, entry_ids, all_entries)
            all_errs += e; t_err += len(e); t_warn += len(w); t_ok += len(o)

    for e in all_errs[:40]:
        print(f"  ❌ {e}")
    print("=" * 64)
    print(f"【phase4 产物校验】| ✅ {t_ok} ok  ⚠️  {t_warn} warn  ❌ {t_err} error")
    print("=" * 64)
    sys.exit(1 if t_err else 0)


if __name__ == "__main__":
    main()
