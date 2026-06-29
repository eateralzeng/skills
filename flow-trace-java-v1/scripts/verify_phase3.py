#!/usr/bin/env python3
"""phase3 产物校验（独立实现，不 import skill 模块）。

校验对象：phase3/{entryId}-pruned.json（剪枝后线性 chain）。
搭配设计：phase3-design.md（schema 3.2-3.4 / 剪枝算法第 5 章）。
独立性：不 import phase3_path_prune/_tree_graph；剪枝逻辑（反向 BFS）在本脚本独立
重实现，与 phase3 产出比对——能抓 phase3 剪枝 bug（维度 M 核心）。

用法：
    python3 verify_phase3.py <project_dir> <cache_dir>
退出码：0 = 无 error；1 = 有 error（warn 不影响）。
"""
import json
import os
import sys
import glob
import argparse
from collections import deque

VALID_FLOW = {"VALID", "NO_ENDPOINT"}
VALID_LAYERTYPE = {"ENTRY", "INTERNAL", "TERMINAL"}
VALID_REASON = {"not_on_terminal_path", "no_terminal_in_tree"}
CHAIN_REQUIRED = ("nodeId", "class", "method", "package", "filePath", "layer",
                  "layerType", "parentId", "callType", "terminal", "description",
                  "domainInteraction")


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}


def independent_kept(tree):
    """独立重实现剪枝：从 terminal 沿入边反向 BFS（design 5.1）。返回 kept nodeId 集。"""
    nodes = tree.get("nodes", {})
    edges = tree.get("edges", [])
    terminals = [nid for nid, n in nodes.items() if n.get("terminal")]
    if not terminals:
        return set(), terminals
    incoming = {}
    for e in edges:
        incoming.setdefault(e.get("to"), []).append(e.get("from"))
    kept = set(terminals)
    queue = deque(terminals)
    while queue:
        nid = queue.popleft()
        for frm in incoming.get(nid, []):
            if frm not in kept:
                kept.add(frm)
                queue.append(frm)
    return kept, terminals


def verify_entry(entry_id, pruned, tree):
    """单入口校验。返回 (errors, warns, oks)。"""
    errors, warns, oks = [], [], []
    tag = entry_id

    # ── A 顶层字段齐全（design 3.2）──
    miss = [k for k in ("entryId", "flowStatus", "chain", "prunedNodes", "summary") if k not in pruned]
    if miss:
        errors.append(f"[A] {tag}: 顶层缺字段 {miss}")
        return errors, warns, oks
    oks.append(f"[A] {tag}: 顶层字段齐全")

    # ── B entryId 一致 ──
    if pruned["entryId"] != entry_id:
        errors.append(f"[B] {tag}: entryId={pruned['entryId']!r} != 文件名前缀")

    # ── C flowStatus 枚举 ──
    flow = pruned["flowStatus"]
    if flow not in VALID_FLOW:
        errors.append(f"[C] {tag}: flowStatus={flow!r} 非法枚举")

    chain = pruned["chain"]
    pruned_nodes = pruned["prunedNodes"]
    summary = pruned["summary"]
    if not isinstance(chain, list) or not isinstance(pruned_nodes, list):
        errors.append(f"[A] {tag}: chain/prunedNodes 非 list")
        return errors, warns, oks

    # ── D/E/F summary 自洽 ──
    if summary.get("retained") != len(chain):
        errors.append(f"[D] {tag}: summary.retained={summary.get('retained')} != len(chain)={len(chain)}")
    if summary.get("pruned") != len(pruned_nodes):
        errors.append(f"[E] {tag}: summary.pruned={summary.get('pruned')} != len(prunedNodes)={len(pruned_nodes)}")
    chain_terminals = sum(1 for n in chain if n.get("terminal"))

    # ── G flowStatus 一致性 ──
    if flow == "NO_ENDPOINT":
        if chain:
            errors.append(f"[G] {tag}: NO_ENDPOINT 但 chain 非空（{len(chain)}）")
        if summary.get("terminals") != 0:
            errors.append(f"[G] {tag}: NO_ENDPOINT 但 summary.terminals={summary.get('terminals')}")
    else:  # VALID
        if not chain:
            errors.append(f"[G] {tag}: VALID 但 chain 为空")
        if chain_terminals == 0:
            errors.append(f"[G] {tag}: VALID 但 chain 无 terminal 节点")

    # ── H/I/J/K chain_node 字段 ──
    chain_ids = {n.get("nodeId") for n in chain}
    h_bad, i_bad, j_bad, k_bad = [], [], [], []
    for n in chain:
        nm = [f for f in CHAIN_REQUIRED if f not in n]
        if nm:
            h_bad.append(f"{n.get('nodeId','?').split(':')[-1]}:缺{nm}")
            continue
        if not isinstance(n["layer"], int) or n["layer"] < 0:
            i_bad.append(f"{n['nodeId'].split(':')[-1]}:layer={n['layer']}")
        if n["layerType"] not in VALID_LAYERTYPE:
            i_bad.append(f"{n['nodeId'].split(':')[-1]}:layerType={n['layerType']}")
        # J parentId 是 list（方案B）+ 元素 ∈ chain
        pid = n["parentId"]
        if not isinstance(pid, list):
            j_bad.append(f"{n['nodeId'].split(':')[-1]}:parentId非list")
        else:
            for p in pid:
                if p not in chain_ids:
                    j_bad.append(f"{n['nodeId'].split(':')[-1]}:parent {p.split(':')[-1]} 不在chain")
        # K DISPATCH 有 patternRef
        if n.get("endpointType") == "DISPATCH" and not n.get("patternRef"):
            k_bad.append(n['nodeId'].split(':')[-1])
    if h_bad:
        errors.append(f"[H] {tag}: {len(h_bad)} 个 chain 节点缺字段: {h_bad[:3]}")
    if i_bad:
        errors.append(f"[I] {tag}: {len(i_bad)} 个 layer/layerType 异常: {i_bad[:3]}")
    if j_bad:
        errors.append(f"[J] {tag}: {len(j_bad)} 个 parentId 问题: {j_bad[:3]}")
    if k_bad:
        warns.append(f"[K] {tag}: {len(k_bad)} 个 DISPATCH 节点缺 patternRef: {k_bad[:3]}")

    # ── L chain 按 (layer, nodeId) 升序 ──
    keys = [(n.get("layer", 0), n.get("nodeId", "")) for n in chain]
    if keys != sorted(keys):
        errors.append(f"[L] {tag}: chain 未按 (layer, nodeId) 升序")
    elif chain:
        oks.append(f"[L] {tag}: chain 有序（{len(chain)} 节点）")

    # ── O prunedNodes 摘要字段 + reason 枚举 ──
    o_bad = []
    for p in pruned_nodes:
        pm = [f for f in ("nodeId", "class", "method", "layer", "layerType", "reason") if f not in p]
        if pm:
            o_bad.append(f"缺{pm}")
        elif p["reason"] not in VALID_REASON:
            o_bad.append(f"reason={p['reason']!r}")
    if o_bad:
        errors.append(f"[O] {tag}: {len(o_bad)} 个 prunedNode 摘要问题: {o_bad[:3]}")

    # ── M/P/Q 跨阶段：独立重剪 + 不增不减 + 终点不丢（核心）──
    if tree is None or "__error__" in tree:
        warns.append(f"[M/P/Q] {tag}: phase2a tree 缺失/损坏，跨阶段维度跳过")
        return errors, warns, oks
    tree_nodes = set(tree.get("nodes", {}).keys())
    tree_terminals = {nid for nid, n in tree.get("nodes", {}).items() if n.get("terminal")}
    kept, _ = independent_kept(tree)

    # M 独立重剪 == chain
    if kept != chain_ids:
        only_chain = chain_ids - kept
        only_kept = kept - chain_ids
        errors.append(f"[M] {tag}: 独立重剪与 chain 不一致 chain多{len(only_chain)}/重剪多{len(only_kept)}: "
                      f"{[x.split(':')[-1] for x in list(only_chain | only_kept)[:3]]}")
    else:
        oks.append(f"[M] {tag}: 独立重剪 == chain（{len(kept)} 节点，剪枝正确）")

    # P retained+pruned == tree 节点数（不增不减）
    if len(chain) + len(pruned_nodes) != len(tree_nodes):
        errors.append(f"[P] {tag}: retained+pruned={len(chain)+len(pruned_nodes)} != tree 节点数={len(tree_nodes)}")

    # Q tree 所有 terminal 都在 chain
    lost_term = tree_terminals - chain_ids
    if lost_term:
        errors.append(f"[Q] {tag}: {len(lost_term)} 个 terminal 被剪掉: {[x.split(':')[-1] for x in list(lost_term)[:3]]}")

    return errors, warns, oks


def main():
    ap = argparse.ArgumentParser(description="phase3 产物校验（独立实现，不 import skill）")
    ap.add_argument("project_dir")
    ap.add_argument("cache_dir")
    args = ap.parse_args()

    cache_dir = os.path.abspath(args.cache_dir)
    p3 = os.path.join(cache_dir, "phase3")
    p2a = os.path.join(cache_dir, "phase2a")
    if not os.path.isdir(p3):
        print(f"❌ 找不到 {p3}（phase3 是否已运行？）")
        sys.exit(1)

    files = sorted(glob.glob(os.path.join(p3, "*-pruned.json")))
    t_err = t_warn = t_ok = 0
    all_errs = []
    for f in files:
        entry_id = os.path.basename(f)[:-len("-pruned.json")]
        pruned = _load_json(f)
        if "__error__" in pruned:
            print(f"  ❌ [A] {entry_id}: JSON 损坏 {pruned['__error__']}")
            t_err += 1
            continue
        tree = _load_json(os.path.join(p2a, f"{entry_id}-tree.json"))
        errs, warns, oks = verify_entry(entry_id, pruned, tree)
        t_err += len(errs)
        t_warn += len(warns)
        t_ok += len(oks)
        all_errs += errs

    for e in all_errs[:40]:
        print(f"  ❌ {e}")
    print("=" * 64)
    print(f"【phase3 产物校验】入口 {len(files)} | ✅ {t_ok} ok  ⚠️  {t_warn} warn  ❌ {t_err} error")
    print("=" * 64)
    sys.exit(1 if t_err else 0)


if __name__ == "__main__":
    main()
