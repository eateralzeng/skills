#!/usr/bin/env python3
"""phase5 产物校验（独立实现，不 import skill 模块）。

校验对象：phase5/{entryId}-semantics.json（chain 节点回填 description）。
搭配设计：phase5-design.md（semantics schema 2.2 / 节点分类 / merge 回写）。
独立性：不 import phase5_describe；结构/覆盖/跨阶段比对在脚本内自实现。
边界：description 由 LLM 子代理非确定性生成，verify **不校验描述语义是否正确**（九章原则），
仅校验可验证不变量——描述覆盖率、节点不增不减、非描述字段保留。

用法：
    python3 verify_phase5.py <project_dir> <cache_dir>
退出码：0 = 无 error；1 = 有 error（warn 不影响）。
"""
import json
import os
import sys
import glob
import argparse

# 非描述字段（phase5 只填 description，其余须与 phase4 一致）
PRESERVE_FIELDS = ("nodeId", "class", "method", "layer", "callType", "parentId",
                   "terminal", "endpointType", "patternRef")


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}


def _has_cjk(s):
    return any('一' <= c <= '鿿' for c in (s or ""))


def verify_entry(entry_id, sem, p4):
    errors, warns, oks = [], [], []
    tag = entry_id

    # ── A 顶层结构 + entryId ──
    miss = [k for k in ("entryId", "flowStatus", "chain", "prunedNodes", "summary") if k not in sem]
    if miss:
        errors.append(f"[A] {tag}: 顶层缺字段 {miss}")
        return errors, warns, oks
    if sem["entryId"] != entry_id:
        errors.append(f"[A] {tag}: entryId={sem['entryId']!r} != 文件名")
    oks.append(f"[A] {tag}: 顶层字段齐全")

    chain = sem["chain"]
    sem_ids = {n.get("nodeId") for n in chain}

    # ── B/C 跨阶段：非描述字段签名多重集 == phase4（正确处理重复 nodeId）──
    def _sig(n):
        return (n.get("nodeId"), n.get("class"), n.get("method"), n.get("layer"),
                n.get("callType"), tuple(n.get("parentId") or []), n.get("terminal"),
                n.get("endpointType"), n.get("patternRef"))

    if p4 and "__error__" not in p4:
        from collections import Counter
        p4_chain = p4.get("chain", [])
        sem_sigs = Counter(_sig(n) for n in chain)
        p4_sigs = Counter(_sig(n) for n in p4_chain)
        if sem_sigs != p4_sigs:
            # 差异：phase5 只该改 description，非描述签名应与 phase4 完全一致（含重复）
            extra = list((sem_sigs - p4_sigs).elements())
            lost = list((p4_sigs - sem_sigs).elements())
            errors.append(f"[B/C] {tag}: 非描述字段签名 != phase4（phase5多{len(extra)}/少{len(lost)}）: "
                          f"{[s[0].split(':')[-1] for s in (extra + lost)[:3]]}")
        else:
            oks.append(f"[B/C] {tag}: 节点集+非描述字段 == phase4（{len(chain)}，只改 description）")
        # F 透传一致（顶层）
        for f in ("flowStatus", "flowType", "summary", "prunedNodes"):
            if f in p4 and sem.get(f) != p4.get(f):
                errors.append(f"[F] {tag}: 顶层 {f} 与 phase4 不一致")
                break

    # ── D 描述覆盖（核心）──
    empty = [n.get("nodeId", "?").split(":")[-1] for n in chain if not (n.get("description") or "").strip()]
    if empty:
        errors.append(f"[D] {tag}: {len(empty)}/{len(chain)} 个节点无描述: {empty[:5]}")
    elif chain:
        oks.append(f"[D] {tag}: 全部 {len(chain)} 节点有描述（覆盖 100%）")

    # ── E 描述质量（warn）──
    e_bad = []
    for n in chain:
        d = (n.get("description") or "").strip()
        if not d:
            continue
        if not _has_cjk(d):
            e_bad.append(f"{n.get('nodeId','?').split(':')[-1]}:无中文")
        elif len(d) > 200:
            e_bad.append(f"{n.get('nodeId','?').split(':')[-1]}:超长{len(d)}")
    if e_bad:
        warns.append(f"[E] {tag}: {len(e_bad)} 个描述质量可疑（无中文/超长）: {e_bad[:3]}")

    return errors, warns, oks


def main():
    ap = argparse.ArgumentParser(description="phase5 产物校验（独立实现，不 import skill）")
    ap.add_argument("project_dir")
    ap.add_argument("cache_dir")
    args = ap.parse_args()

    cache_dir = os.path.abspath(args.cache_dir)
    p5 = os.path.join(cache_dir, "phase5")
    p4 = os.path.join(cache_dir, "phase4")
    if not os.path.isdir(p5):
        print(f"❌ 找不到 {p5}（phase5 是否已运行？）")
        sys.exit(1)

    files = sorted(glob.glob(os.path.join(p5, "*-semantics.json")))
    all_errs, t_err, t_warn, t_ok = [], 0, 0, 0
    for f in files:
        entry_id = os.path.basename(f)[:-len("-semantics.json")]
        sem = _load_json(f)
        if "__error__" in sem:
            all_errs.append(f"[A] {entry_id}: JSON 损坏"); t_err += 1; continue
        p4d = _load_json(os.path.join(p4, f"{entry_id}.json"))
        errs, warns, oks = verify_entry(entry_id, sem, p4d)
        all_errs += errs; t_err += len(errs); t_warn += len(warns); t_ok += len(oks)

    for e in all_errs[:40]:
        print(f"  ❌ {e}")
    print("=" * 64)
    print(f"【phase5 产物校验】入口 {len(files)} | ✅ {t_ok} ok  ⚠️  {t_warn} warn  ❌ {t_err} error")
    print("=" * 64)
    sys.exit(1 if t_err else 0)


if __name__ == "__main__":
    main()
