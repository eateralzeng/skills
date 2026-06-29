#!/usr/bin/env python3
"""phase6 产物校验（独立实现，不 import skill 模块）。

校验对象：flows/**/*.md（每入口文档）+ flow-summary.json / flow-detail.json /
flow-data-lineage.json（三份索引）。
搭配设计：phase6-design.md（输出 2.2 / 三 JSON schema）。
独立性：不 import phase6_doc_gen；文档存在性、计数自洽、数据血缘独立重算自实现。
边界：.md 正文是渲染产物（含 LLM 描述），verify **不校验正文文字**，只校验
文档存在性 + 索引 JSON 自洽 + 血缘独立重算 + DISPATCH 路由表存在（防方案A 回归）。

用法：
    python3 verify_phase6.py <project_dir> <cache_dir> [--docs-dir <dir>]
退出码：0 = 无 error；1 = 有 error（warn 不影响）。
"""
import json
import os
import sys
import glob
import argparse


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}


def _flow_status(cache_dir, entry_id):
    """入口的 flowStatus（优先 phase5，降级 phase4/phase3，同 phase6 load_flow_data）。"""
    for p, suf in (("phase5", "-semantics.json"), ("phase4", ".json"), ("phase3", "-pruned.json")):
        fp = os.path.join(cache_dir, p, f"{entry_id}{suf}")
        if os.path.exists(fp):
            return _load_json(fp).get("flowStatus")
    return None


def _semantics_db_tables(cache_dir):
    """独立从 phase5 semantics 收集所有 DATABASE 终点的表名（血缘重算基准）。"""
    tables = set()
    for f in glob.glob(os.path.join(cache_dir, "phase5", "*-semantics.json")):
        for n in _load_json(f).get("chain", []):
            di = n.get("domainInteraction") or {}
            if isinstance(di, dict) and di.get("type") == "DATABASE" and di.get("table"):
                t = di["table"]
                if t and t != "[待确认]":
                    tables.add(t)
    return tables


def main():
    ap = argparse.ArgumentParser(description="phase6 产物校验（独立实现，不 import skill）")
    ap.add_argument("project_dir")
    ap.add_argument("cache_dir")
    ap.add_argument("--docs-dir", help="phase6 输出目录（默认 cache 同级 docs）")
    args = ap.parse_args()

    cache_dir = os.path.abspath(args.cache_dir)
    docs = os.path.abspath(args.docs_dir) if args.docs_dir else os.path.join(os.path.dirname(cache_dir), "docs")
    if not os.path.isdir(docs):
        print(f"❌ 找不到文档目录 {docs}（phase6 是否已运行？用 --docs-dir 指定）")
        sys.exit(1)

    errors, warns, oks = [], [], []

    # 入口列表 + VALID/NO_ENDPOINT
    entries = _load_json(os.path.join(cache_dir, "phase1a", "entries.json")).get("entries", [])
    entry_ids = {e.get("id") for e in entries}
    valid_ids = {e["id"] for e in entries if _flow_status(cache_dir, e["id"]) == "VALID"}
    noend_ids = {e["id"] for e in entries if _flow_status(cache_dir, e["id"]) == "NO_ENDPOINT"}

    # ── A 文档数 == VALID 入口数 ──
    # matchedReceivers：RMB 桥接合并进发送端的接收端入口，不再单独出文档（4b in-place）
    bridges = _load_json(os.path.join(cache_dir, "phase4", "bridges.json"))
    matched_recv = set(bridges.get("matchedReceivers", [])) if isinstance(bridges, dict) else set()
    expected_doc_ids = valid_ids - matched_recv  # 期望文档 = VALID - 被合并接收端
    n_exp = len(expected_doc_ids)

    md_files = glob.glob(os.path.join(docs, "flows", "**", "*.md"), recursive=True)
    if len(md_files) != n_exp:
        warns.append(f"[A] 文档数 {len(md_files)} != 期望 {n_exp}（VALID {len(valid_ids)} - 合并接收端 {len(matched_recv)}，NO_ENDPOINT {len(noend_ids)} 跳过）")
    else:
        oks.append(f"[A] 文档数 == VALID-合并接收端（{n_exp}；NO_ENDPOINT {len(noend_ids)}、matchedReceivers {len(matched_recv)} 跳过）")

    # ── flow-summary.json ──
    summ = _load_json(os.path.join(docs, "flow-summary.json"))
    if "__error__" in summ:
        errors.append(f"[B] flow-summary.json 缺失/损坏")
    else:
        flows = summ.get("flows", [])
        # B 计数自洽
        if summ.get("totalFlows") != len(flows):
            errors.append(f"[B] summary.totalFlows={summ.get('totalFlows')} != len(flows)={len(flows)}")
        elif len(flows) != n_exp:
            errors.append(f"[B] summary flows {len(flows)} != 期望 {n_exp}（VALID-合并接收端）")
        else:
            oks.append(f"[B] summary totalFlows 自洽（{len(flows)}，已扣除合并接收端 {len(matched_recv)}）")
        # C summaryByType 求和 == 入口总数（phase6 按全部 all_entries 计 type，非仅文档化 flows）
        sbt = summ.get("summaryByType", {})
        if isinstance(sbt, dict) and sum(sbt.values()) != len(entry_ids):
            errors.append(f"[C] summaryByType 求和 {sum(sbt.values())} != 入口总数 {len(entry_ids)}")
        else:
            oks.append(f"[C] summaryByType 求和 == 入口总数（{len(entry_ids)}）")
        # D 每个 flow.docPath 存在
        d_bad = [f.get("id") for f in flows if not os.path.exists(os.path.join(docs, f.get("docPath", "")))]
        if d_bad:
            errors.append(f"[D] {len(d_bad)} 个 flow.docPath 指向不存在的文档: {d_bad[:5]}")
        else:
            oks.append(f"[D] 全部 {len(flows)} 个 docPath 存在")
        # E flow.id ∈ entries
        e_bad = [f.get("id") for f in flows if f.get("id") not in entry_ids]
        if e_bad:
            errors.append(f"[E] {len(e_bad)} 个 flow.id 非 entry: {e_bad[:3]}")

    # ── flow-detail.json ──
    det = _load_json(os.path.join(docs, "flow-detail.json"))
    if "__error__" in det:
        errors.append(f"[F] flow-detail.json 缺失/损坏")
    else:
        dflows = det.get("flows", [])
        if "__error__" not in summ and len(dflows) != len(summ.get("flows", [])):
            errors.append(f"[F] detail flows {len(dflows)} != summary flows {len(summ.get('flows', []))}")
        else:
            oks.append(f"[F] flow-detail 计数与 summary 一致（{len(dflows)}）")

    # ── flow-data-lineage.json：结构 + 独立重算 ──
    lin = _load_json(os.path.join(docs, "flow-data-lineage.json"))
    if "__error__" in lin:
        errors.append(f"[G] flow-data-lineage.json 缺失/损坏")
    else:
        ltables = lin.get("tables", [])
        g_bad = [t for t in ltables if not t.get("name")
                 or not isinstance(t.get("readByFlows"), list) or not isinstance(t.get("writtenByFlows"), list)]
        if g_bad:
            errors.append(f"[G] {len(g_bad)} 个 lineage table 结构问题")
        else:
            oks.append(f"[G] lineage {len(ltables)} 表结构合法")
        # H 独立重算：lineage 表名集 ⊆ semantics DATABASE 表名集（血缘不应凭空多表）
        sem_tables = _semantics_db_tables(cache_dir)
        lin_names = {t.get("name") for t in ltables}
        extra = lin_names - sem_tables
        if extra:
            warns.append(f"[H] lineage 有 {len(extra)} 个表不在 semantics DATABASE 终点中: {sorted(extra)[:5]}")
        else:
            oks.append(f"[H] lineage 表全部源自 semantics DATABASE 终点（{len(lin_names)}/{len(sem_tables)}）")

    # ── I DISPATCH 路由表存在（防方案A patternRef→summary 回归）──
    # 有 DISPATCH 且 patternRef 能匹配 summary 的入口，其文档应含「分发路由：」
    summ_dks = set()
    for sf in glob.glob(os.path.join(cache_dir, "phase2b", "dispatch-summary-*.json")):
        dk = _load_json(sf).get("dispatchKey")
        if dk:
            summ_dks.add(dk)
    i_bad = []
    for f in glob.glob(os.path.join(cache_dir, "phase5", "*-semantics.json")):
        eid = os.path.basename(f)[:-len("-semantics.json")]
        sem = _load_json(f)
        disp = [n for n in sem.get("chain", []) if n.get("endpointType") == "DISPATCH" and n.get("patternRef")]
        if not disp:
            continue
        # 该入口文档路径（从 summary.flows 查）
        doc_rel = None
        if "__error__" not in summ:
            for fl in summ.get("flows", []):
                if fl.get("id") == eid:
                    doc_rel = fl.get("docPath")
                    break
        if not doc_rel:
            continue
        try:
            content = open(os.path.join(docs, doc_rel)).read()
        except Exception:
            continue
        if "分发路由：" not in content:
            i_bad.append(eid)
    if i_bad:
        errors.append(f"[I] {len(i_bad)} 个含 DISPATCH 的入口文档缺分发路由表（方案A patternRef→summary 回归？）: {i_bad[:5]}")
    else:
        oks.append(f"[I] 含 DISPATCH 的入口文档均渲染了分发路由表")

    for o in oks:
        print(f"  ✅ {o}")
    for w in warns:
        print(f"  ⚠️  {w}")
    for e in errors:
        print(f"  ❌ {e}")
    print("=" * 64)
    print(f"【phase6 产物校验】文档 {len(md_files)} | ✅ {len(oks)} ok  ⚠️  {len(warns)} warn  ❌ {len(errors)} error")
    print("=" * 64)
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
