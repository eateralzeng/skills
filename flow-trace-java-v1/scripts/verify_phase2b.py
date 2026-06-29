#!/usr/bin/env python3
"""phase2b 产物校验（独立实现，不 import skill 模块）。

校验对象：phase2b/dispatch-summary-{shortName}.json（分发点汇总）。
搭配设计：design/phase2b-design.md（schema 3.1-3.6 / 决策 / 已知问题）。
独立性：不 import phase2b_dispatch_prepare/normalize；pattern-index/db-lookup/summary
解析与覆盖度重算均在本脚本自实现，与 skill 自带 validate 交叉印证（能抓 skill bug）。

用法：
    python3 verify_phase2b.py <project_dir> <cache_dir>
退出码：0 = 无 error；1 = 有 error（warn 不影响）。
"""
import json
import os
import sys
import hashlib
import glob
import argparse

VALID_DISPATCH_TYPES = {"STRATEGY_DISPATCH", "STREAM_DISPATCH", "SWITCH_DISPATCH",
                        "MAP_DISPATCH", "ANNOTATION_DISPATCH", "UNKNOWN"}
VALID_ENDPOINT_TYPES = {"DATABASE", "RMB_EXTERNAL", "HTTP_EXTERNAL", "MQ_PUBLISH",
                        "FILE_WRITE", "UNKNOWN"}
VALID_OPERATIONS = {"SELECT", "INSERT", "UPDATE", "DELETE", "UNKNOWN"}


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": str(e)}


def _short(fqn):
    """全限定名取末段短名（独立实现，对应 design 的 interface.rsplit('.',1)[-1]）。"""
    return fqn.rsplit(".", 1)[-1] if fqn else ""


def _class_from_path(file_path):
    """从 src/main/java 之后的相对路径反推全限定类名（implFiles 回退用）。"""
    if not file_path:
        return ""
    marker = "src/main/java/"
    idx = file_path.find(marker)
    rel = file_path[idx + len(marker):] if idx >= 0 else file_path
    if rel.endswith(".java"):
        rel = rel[:-5]
    return rel.replace("/", ".")


def _expected_impls(pattern):
    """独立构造 pattern 期望实现类全限定名集合（implementations 优先，implFiles 回退）。"""
    impls = pattern.get("implementations")
    if impls:
        return {i.get("class", "") for i in impls if i.get("class")}
    return {_class_from_path(p) for p in pattern.get("implFiles", []) if p}


def verify_summary(filename, short_name, summary, pattern, lookup):
    """单个 dispatch-summary 校验。返回 (errors, warns, oks)。"""
    errors, warns, oks = [], [], []

    # ── A 顶层字段齐全（design 3.1，方案A 含 dispatchKey）──
    missing_top = [k for k in ("interface", "dispatchKey", "dispatchType", "results") if k not in summary]
    if missing_top:
        errors.append(f"[A] {short_name}: 顶层缺字段 {missing_top}")
        return errors, warns, oks  # 结构残缺，后续维度无意义
    oks.append(f"[A] {short_name}: 顶层字段齐全")

    # ── B dispatchKey/interface 与 pattern-index 一致（方案A：按 dispatchKey 关联）──
    if pattern is not None:
        if summary["dispatchKey"] != pattern.get("dispatchKey"):
            errors.append(f"[B] {short_name}: dispatchKey={summary['dispatchKey']!r} != pattern-index "
                          f"{pattern.get('dispatchKey')!r}")
        elif summary["interface"] != pattern.get("interface"):
            errors.append(f"[B] {short_name}: interface={summary['interface']!r} != pattern-index "
                          f"{pattern.get('interface')!r}")
        else:
            oks.append(f"[B] {short_name}: dispatchKey/interface 与 pattern-index 一致")

    # ── C 文件名 = dispatch-summary-{短名}-{md5(dispatchKey)[:8]}.json（方案A）──
    exp_hash = hashlib.md5(summary["dispatchKey"].encode()).hexdigest()[:8]
    exp_name = f"dispatch-summary-{_short(summary['interface'])}-{exp_hash}.json"
    if os.path.basename(filename) != exp_name:
        errors.append(f"[C] {short_name}: 文件名 {os.path.basename(filename)!r} != 期望 {exp_name!r}（短名/hash 不符）")
    else:
        oks.append(f"[C] {short_name}: 文件名短名+hash 正确")

    # ── D dispatchType 枚举（warn，下游未消费 issue1）──
    if summary["dispatchType"] not in VALID_DISPATCH_TYPES:
        warns.append(f"[D] {short_name}: dispatchType={summary['dispatchType']!r} 非法枚举")

    # ── E results 是 list ──
    results = summary["results"]
    if not isinstance(results, list):
        errors.append(f"[E] {short_name}: results 非 list（{type(results).__name__}）")
        return errors, warns, oks
    oks.append(f"[E] {short_name}: results 是 list（{len(results)} 条）")

    # ── F-O 逐 impl_result + endpoint ──
    f_bad, g_bad, h_bad, i_unknown = [], [], [], 0
    j_bad, k_bad, l_bad, m_bad, n_bad, o_pending = [], [], [], [], [], 0
    zero_ep = 0
    s_mismatch = []  # DATABASE table 与 lookup 不符
    for r in results:
        rid = r.get("shortName") or r.get("class", "?")
        miss = [k for k in ("class", "shortName", "condition", "endpoints") if k not in r]
        if miss:
            f_bad.append(f"{rid}:缺{miss}")
            continue
        # G class 全限定名
        if "." not in (r["class"] or ""):
            g_bad.append(rid)
        # H shortName 一致性
        if r["shortName"] != _short(r["class"]):
            h_bad.append(rid)
        # I condition
        cond = r["condition"]
        if not isinstance(cond, str) or not cond.strip():
            i_unknown += 1  # 空也按 unknown 统计
        elif cond.strip().lower() == "unknown":
            i_unknown += 1
        # J endpoints 是 list
        eps = r["endpoints"]
        if not isinstance(eps, list):
            j_bad.append(f"{rid}:endpoints非list")
            continue
        if not eps:
            zero_ep += 1
        for ep in eps:
            emiss = [k for k in ("class", "method", "filePath", "type") if k not in ep]
            if emiss:
                j_bad.append(f"{rid}:endpoint缺{emiss}")
                continue
            # K type 枚举
            if ep["type"] not in VALID_ENDPOINT_TYPES:
                k_bad.append(f"{rid}.{ep.get('method')}:{ep['type']}")
            # L endpoint.class 短名（不含 .）
            if "." in (ep.get("class") or ""):
                l_bad.append(f"{rid}:{ep['class']}")
            # M DATABASE 有 table+operation；非 DATABASE 为 null
            if ep["type"] == "DATABASE":
                if not ep.get("table") or not ep.get("operation"):
                    m_bad.append(f"{rid}.{ep.get('method')}:DB缺table/op")
                if ep.get("operation") and ep["operation"] not in VALID_OPERATIONS:
                    n_bad.append(f"{rid}.{ep.get('method')}:{ep['operation']}")
                if ep.get("table") == "[待确认]":
                    o_pending += 1
                # S 独立查 lookup 重算 table
                key = f"{ep.get('class')}.{ep.get('method')}"
                lk = lookup.get(key)
                if lk and lk.get("table") and ep.get("table") not in ("[待确认]", lk.get("table")):
                    s_mismatch.append(f"{key}: summary={ep.get('table')} lookup={lk.get('table')}")
            else:
                if ep.get("table") is not None or ep.get("operation") is not None:
                    m_bad.append(f"{rid}.{ep.get('method')}:非DB却有table/op")

    if f_bad:
        errors.append(f"[F] {short_name}: {len(f_bad)} 个 result 缺字段: {f_bad[:3]}")
    if g_bad:
        errors.append(f"[G] {short_name}: {len(g_bad)} 个 class 非全限定名: {g_bad[:3]}")
    if h_bad:
        warns.append(f"[H] {short_name}: {len(h_bad)} 个 shortName != class 末段: {h_bad[:3]}")
    if i_unknown:
        warns.append(f"[I] {short_name}: condition=unknown {i_unknown}/{len(results)}")
    if j_bad:
        errors.append(f"[J] {short_name}: {len(j_bad)} 个 endpoint 结构问题: {j_bad[:3]}")
    if k_bad:
        errors.append(f"[K] {short_name}: {len(k_bad)} 个 endpoint.type 非法枚举: {k_bad[:3]}")
    if l_bad:
        warns.append(f"[L] {short_name}: {len(l_bad)} 个 endpoint.class 非短名（含.）: {l_bad[:3]}")
    if m_bad:
        warns.append(f"[M] {short_name}: {len(m_bad)} 个 DATABASE/非DB table/operation 异常: {m_bad[:3]}")
    if n_bad:
        warns.append(f"[N] {short_name}: {len(n_bad)} 个 operation 非法枚举: {n_bad[:3]}")
    if o_pending:
        warns.append(f"[O] {short_name}: {o_pending} 个 DATABASE table=[待确认]（lookup 未命中）")
    if zero_ep:
        warns.append(f"[T] {short_name}: {zero_ep}/{len(results)} 个实现类 0 endpoints（疑似漏抽/无终点）")
    if s_mismatch:
        warns.append(f"[S] {short_name}: {len(s_mismatch)} 个 DATABASE table 与 db-lookup 不符: {s_mismatch[:3]}")

    # ── Q 实现类覆盖完整（核心，独立重算）──
    if pattern is not None:
        expected = _expected_impls(pattern)
        actual = {r.get("class") for r in results if isinstance(r, dict)}
        missing_impl = expected - actual
        if missing_impl:
            errors.append(f"[Q] {short_name}: 缺 {len(missing_impl)}/{len(expected)} 个实现类: "
                          f"{[_short(m) for m in list(missing_impl)[:5]]}")
        else:
            oks.append(f"[Q] {short_name}: 实现类全覆盖（{len(expected)}）")

    return errors, warns, oks


def main():
    ap = argparse.ArgumentParser(description="phase2b 产物校验（独立实现，不 import skill）")
    ap.add_argument("project_dir", help="Java 项目根目录")
    ap.add_argument("cache_dir", help=".trace-cache 目录")
    args = ap.parse_args()

    cache_dir = os.path.abspath(args.cache_dir)
    p2b = os.path.join(cache_dir, "phase2b")
    if not os.path.isdir(p2b):
        print(f"❌ 找不到 {p2b}（phase2b 是否已运行？）")
        sys.exit(1)

    # 加载 pattern-index（verified 集合，方案A：按 dispatchKey 关联）+ db-lookup
    pidx = _load_json(os.path.join(cache_dir, "phase1c", "pattern-index.json"))
    patterns = pidx.get("patterns", []) if isinstance(pidx, dict) else []
    dk_to_pattern = {}
    verified_dks = set()
    for p in patterns:
        if p.get("_verified") is False:
            continue
        dk = p.get("dispatchKey")
        if dk:
            dk_to_pattern[dk] = p
            verified_dks.add(dk)

    lookup = {}
    lk_data = _load_json(os.path.join(cache_dir, "phase1b", "db-schema-lookup.json"))
    if isinstance(lk_data, dict):
        lookup = lk_data.get("lookup", {})

    all_errors, all_warns, all_oks = [], [], []

    summary_files = glob.glob(os.path.join(p2b, "dispatch-summary-*.json"))
    file_dk = {}  # 每个 summary 文件的 dispatchKey
    for f in summary_files:
        s = _load_json(f)
        file_dk[f] = s.get("dispatchKey") if isinstance(s, dict) else None
    present_dks = {dk for dk in file_dk.values() if dk}

    # ── P 文件存在性：每个 verified pattern（按 dispatchKey）都有 summary ──
    missing_dks = verified_dks - present_dks
    if missing_dks:
        all_errors.append(f"[P] {len(missing_dks)} 个 verified 分发点缺 dispatch-summary: "
                          f"{[_short(m) for m in sorted(missing_dks)][:5]}")
    else:
        all_oks.append(f"[P] 全部 {len(verified_dks)} 个 verified 分发点都有 summary（按 dispatchKey）")

    # ── R 无多余 summary ──
    extra = present_dks - verified_dks
    if extra:
        all_warns.append(f"[R] {len(extra)} 个 summary dispatchKey 无对应 verified pattern: "
                         f"{[_short(e) for e in sorted(extra)][:5]}")
    else:
        all_oks.append(f"[R] 无多余 summary")

    # ── 逐 summary 校验 ──
    for f in sorted(summary_files):
        summary = _load_json(f)
        if isinstance(summary, dict) and "__error__" in summary:
            all_errors.append(f"[A] {os.path.basename(f)}: JSON 损坏 {summary['__error__']}")
            continue
        dk = summary.get("dispatchKey") if isinstance(summary, dict) else None
        sn = _short(summary.get("interface", "")) if isinstance(summary, dict) else os.path.basename(f)
        e, w, o = verify_summary(f, sn, summary, dk_to_pattern.get(dk), lookup)
        all_errors += e
        all_warns += w
        all_oks += o

    # ── U patternRef 对齐（方案A：复刻 phase4 真实匹配，按 dispatchKey 全等，非宽松 _short）──
    p2a = os.path.join(cache_dir, "phase2a")
    referenced = set()
    if os.path.isdir(p2a):
        for tf in glob.glob(os.path.join(p2a, "*-tree.json")):
            t = _load_json(tf)
            for n in (t.get("nodes", {}) or {}).values():
                if n.get("endpointType") == "DISPATCH" and n.get("patternRef"):
                    referenced.add(n["patternRef"])
    unmatched = referenced - present_dks  # patternRef(=dispatchKey) 须全等命中 summary.dispatchKey
    if unmatched:
        all_warns.append(f"[U] phase2a {len(unmatched)} 个 DISPATCH patternRef 无匹配 summary "
                         f"（phase4 挂载会漏）: {[_short(u) for u in sorted(unmatched)][:5]}")
    elif referenced:
        all_oks.append(f"[U] phase2a 引用的 {len(referenced)} 个 DISPATCH patternRef 全部命中 summary（dispatchKey 全等）")

    # ── 报告 ──
    for o in all_oks:
        print(f"  ✅ {o}")
    for w in all_warns:
        print(f"  ⚠️  {w}")
    for e in all_errors:
        print(f"  ❌ {e}")
    print("=" * 64)
    print(f"【汇总】分发点 {len(summary_files)} | ✅ {len(all_oks)} ok  "
          f"⚠️  {len(all_warns)} warn  ❌ {len(all_errors)} error")
    print("=" * 64)
    sys.exit(1 if all_errors else 0)


if __name__ == "__main__":
    main()
