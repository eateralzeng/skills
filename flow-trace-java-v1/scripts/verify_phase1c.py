#!/usr/bin/env python3
"""phase1c 产物校验（独立实现，不 import 任何 skill 模块）。

依据 skills-design/flow-trace-java-v1/design/phase1c-design.md 的产物 schema（第 3 章）
与扫描规则（第 4-5 章）作为校验基准。核心是「分发点关系源码回溯」——独立读源码验证
interface 真是 interface/abstract class、每个实现类真的 implements/extends 它。

用法:
    python3 verify_phase1c.py <project_dir> <cache_dir> [--rules <dispatch-rules.md>]

退出码: 0 = 无 error（warn 不影响）; 1 = 有 error
"""
import json, os, re, sys, argparse

VALID_TYPES = {'STREAM_DISPATCH', 'MAP_DISPATCH', 'SWITCH_DISPATCH', 'UNKNOWN'}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
DEFAULT_RULES = os.path.join(SKILL_DIR, 'rules', 'dispatch-rules.md')


# ── 独立解析函数（design 5.1 规则解析 / 5.3 抽象链，非 skill 代码）──

def load_min_implementations(rules_path):
    """design 5.1: 从 dispatch-rules.md 读 min-implementations（默认 2）。独立解析 md。"""
    if not rules_path or not os.path.exists(rules_path):
        return 2
    in_sec = False
    try:
        for line in open(rules_path):
            s = line.strip()
            if '<!--' in s:
                continue
            if s.startswith('## '):
                in_sec = (s[3:].strip() == 'min-implementations')
            elif in_sec and s.startswith('- '):
                try:
                    return int(s[2:].strip())
                except ValueError:
                    pass
    except Exception:
        pass
    return 2


def build_class_index(project_dir):
    """className -> 相对 filePath 索引（供 interface/impl 源文件查找）。"""
    idx = {}
    for root, _, files in os.walk(project_dir):
        for fn in files:
            if fn.endswith('.java') and fn[:-5] not in idx:
                idx[fn[:-5]] = os.path.relpath(os.path.join(root, fn), project_dir)
    return idx


def read_src(project_dir, rel_fp):
    try:
        with open(os.path.join(project_dir, rel_fp)) as f:
            return f.read()
    except Exception:
        return None


def is_interface_or_abstract(src, short):
    """design 4.2.1: interface 用 interface 关键字，extends 来源用 abstract class。"""
    if src is None:
        return None
    if re.search(rf'\binterface\s+{re.escape(short)}\b', src):
        return 'interface'
    if re.search(rf'\babstract\s+class\s+{re.escape(short)}\b', src):
        return 'abstract-class'
    if re.search(rf'\bclass\s+{re.escape(short)}\b', src):
        return 'concrete-class'
    return None


def impl_chain_relates(project_dir, cidx, impl_rel, iface_short, depth=0, seen=None):
    """design 4.2.1/5.3: 递归向上查 impl 继承链，任一类 implements iface 或 extends iface 则相关。
    不依赖 parentAbstract 字段（detect 的 read(2000) 截断可能导致 parentAbstract 不准）。"""
    if seen is None:
        seen = set()
    if depth > 8 or not impl_rel or impl_rel in seen:
        return False
    seen.add(impl_rel)
    src = read_src(project_dir, impl_rel)
    if src is None:
        return False
    if re.search(rf'\bimplements\s+[^{{]*\b{re.escape(iface_short)}\b', src):
        return True
    ext = re.search(r'\bextends\s+([\w.]+)', src)
    if ext:
        parent = ext.group(1).split('.')[-1]
        if parent == iface_short:
            return True
        parent_rel = cidx.get(parent)
        if parent_rel and impl_chain_relates(project_dir, cidx, parent_rel, iface_short, depth + 1, seen):
            return True
    return False


# ── 主校验 ──────────────────────────────────────────────────────────

def verify(project_dir, cache_dir, rules_path):
    errors, warns, oks = [], [], []
    p_path = os.path.join(cache_dir, 'phase1c', 'pattern-index.json')
    if not os.path.exists(p_path):
        print(f"❌ 找不到 {p_path}（phase1c detect 是否已运行？）")
        return 1
    data = json.load(open(p_path))
    patterns = data.get('patterns', [])
    min_impl = load_min_implementations(rules_path)
    cidx = build_class_index(project_dir)

    # A 结构
    if data.get('version') == '2.0':
        oks.append("[A] version=2.0")
    else:
        errors.append(f"[A] version 非 2.0: {data.get('version')!r}")
    if data.get('generator') == 'flow-trace-java-v1':
        oks.append("[A] generator=flow-trace-java-v1")
    else:
        warns.append(f"[A] generator={data.get('generator')!r}（非 v1，design issue 1：旧 cache 数据）")
    if 'patterns' in data:
        oks.append("[A] patterns 顶层结构齐全")
    else:
        errors.append("[A] 缺 patterns 顶层字段")

    # B 计数
    if data.get('totalPatterns') == len(patterns):
        oks.append(f"[B] totalPatterns={len(patterns)} 一致")
    else:
        errors.append(f"[B] totalPatterns={data.get('totalPatterns')} != 实际 {len(patterns)}")

    # C pattern 必填字段
    REQUIRED = ['interface', 'type', 'interfaceMethods', 'implementations', 'implementationCount']
    c_err = []
    for p in patterns:
        for f in REQUIRED:
            v = p.get(f)
            if v is None or v == '':
                c_err.append(f"{p.get('interface', '?')}: 缺 {f}")
    if c_err:
        errors.append(f"[C] 必填字段缺失 {len(c_err)}: {c_err[:5]}")
    else:
        oks.append(f"[C] 全部 pattern 必填字段齐全（{len(patterns)} 条）")

    # D interface FQN 格式 + 唯一
    d_err = []
    for p in patterns:
        iface = p.get('interface', '')
        if '.' not in iface:
            d_err.append(f"interface 非 FQN（无点）: {iface}")
    dups = [i for i, c in __import__('collections').Counter(p.get('interface') for p in patterns).items() if c > 1]
    if dups:
        d_err.append(f"interface 重复: {dups}")
    if d_err:
        errors.append(f"[D] interface 格式/唯一问题: {d_err[:5]}")
    else:
        oks.append(f"[D] interface 全为 FQN 且唯一（{len(patterns)} 条）")

    # E type 合法
    bad_type = [(p['interface'], p.get('type')) for p in patterns if p.get('type') not in VALID_TYPES]
    if bad_type:
        errors.append(f"[E] 非法 type {len(bad_type)}: {bad_type[:5]}")
    else:
        oks.append(f"[E] type 全合法（{len(patterns)} 条）")

    # F implementationCount == len + >= min_impl
    f_err = []
    for p in patterns:
        impls = p.get('implementations', [])
        if p.get('implementationCount') != len(impls):
            f_err.append(f"{p.get('interface')}: implementationCount={p.get('implementationCount')} != len={len(impls)}")
        if len(impls) < min_impl:
            f_err.append(f"{p.get('interface')}: 实现数 {len(impls)} < min_implementations={min_impl}")
    if f_err:
        errors.append(f"[F] implementationCount/阈值问题 {len(f_err)}: {f_err[:5]}")
    else:
        oks.append(f"[F] implementationCount 全一致 + 均 ≥ min_implementations({min_impl})")

    # G impl 元素字段 + class 唯一
    g_err = []
    for p in patterns:
        seen_cls = set()
        for impl in p.get('implementations', []):
            cls = impl.get('class', '')
            if '.' not in cls:
                g_err.append(f"{p.get('interface')}: impl class 非 FQN {cls}")
            if impl.get('filePath', '').startswith('/'):
                g_err.append(f"{p.get('interface')}: impl filePath 非相对")
            if not impl.get('module'):
                g_err.append(f"{p.get('interface')}: impl 缺 module")
            if cls in seen_cls:
                g_err.append(f"{p.get('interface')}: impl class 重复 {cls}")
            seen_cls.add(cls)
    if g_err:
        errors.append(f"[G] impl 字段问题 {len(g_err)}: {g_err[:5]}")
    else:
        oks.append(f"[G] impl 元素字段合法 + class 去重")

    # H filePath 存在（interface + impls）
    h_miss = []
    for p in patterns:
        iface_short = p.get('interface', '').split('.')[-1]
        if cidx.get(iface_short):
            pass
        else:
            h_miss.append(f"interface 源文件未找到: {iface_short}")
        for impl in p.get('implementations', []):
            fp = impl.get('filePath', '')
            if fp and not os.path.exists(os.path.join(project_dir, fp)):
                h_miss.append(f"impl 文件缺失: {fp}")
            elif not fp:
                h_miss.append(f"{impl.get('class')}: impl 无 filePath")
    if h_miss:
        # interface 源文件未找到是 warn（可能在 jar），impl 文件缺失是 error
        impl_miss = [m for m in h_miss if 'impl' in m]
        ifce_miss = [m for m in h_miss if 'interface' in m]
        if impl_miss:
            errors.append(f"[H] impl 文件缺失 {len(impl_miss)}: {impl_miss[:5]}")
        if ifce_miss:
            warns.append(f"[H] {len(ifce_miss)} 个 interface 源文件未在项目内（可能在 jar）: {ifce_miss[:3]}")
        if not impl_miss:
            oks.append(f"[H] impl filePath 全存在")
    else:
        oks.append(f"[H] interface + impl filePath 全存在")

    # I 源码回溯（核心：分发点关系真实性，递归继承链）
    i_err = []
    pa_mismatch = []
    iface_kind_counter = {'interface': 0, 'abstract-class': 0, 'concrete-class': 0, 'none': 0}
    for p in patterns:
        iface_short = p.get('interface', '').split('.')[-1]
        iface_rel = cidx.get(iface_short)
        isrc = read_src(project_dir, iface_rel) if iface_rel else None
        kind = is_interface_or_abstract(isrc, iface_short)
        iface_kind_counter[kind or 'none'] = iface_kind_counter.get(kind or 'none', 0) + 1
        if kind == 'concrete-class':
            i_err.append(f"{iface_short}: interface 字段指向具体类（非 interface/abstract）")
        elif kind is None and iface_rel:
            i_err.append(f"{iface_short}: 源文件中未找到 interface/abstract class 声明")
        # impl 关系（递归继承链，不依赖 parentAbstract）
        for impl in p.get('implementations', []):
            impl_short = impl.get('class', '').split('.')[-1]
            impl_rel = cidx.get(impl_short) or impl.get('filePath')
            if not impl_chain_relates(project_dir, cidx, impl_rel, iface_short):
                i_err.append(f"{iface_short}: impl {impl_short} 继承链未到该分发点")
            # parentAbstract 与实际 extends 一致性（暴露 detect read(2000) 截断 bug）
            pa = impl.get('parentAbstract')
            if pa and impl_rel:
                isrc_i = read_src(project_dir, impl_rel)
                if isrc_i:
                    ext = re.search(r'\bextends\s+([\w.]+)', isrc_i)
                    actual = ext.group(1).split('.')[-1] if ext else None
                    if actual and actual != pa:
                        pa_mismatch.append((iface_short, impl_short, pa, actual))
    if i_err:
        errors.append(f"[I] 分发点关系回溯问题 {len(i_err)}: {i_err[:5]}")
    else:
        oks.append(f"[I] 分发点关系回溯全过（递归继承链，interface 类型分布 {iface_kind_counter}）")
    if pa_mismatch:
        warns.append(f"[I] parentAbstract 与实际 extends 不符 {len(pa_mismatch)} 条（detect read(2000) 截断 bug）: {pa_mismatch[:3]}")

    # J interfaceMethods 非空（已过滤数据接口，design 决策 5）
    empty_methods = [p['interface'] for p in patterns if not p.get('interfaceMethods')]
    if empty_methods:
        warns.append(f"[J] {len(empty_methods)} 个 pattern 的 interfaceMethods 为空: {empty_methods[:3]}")
    else:
        oks.append(f"[J] interfaceMethods 全非空（数据接口已过滤）")

    # K _verified 一致性（detect 产物无此字段；verify-apply 后不应残留 false）
    bad_verified = [p['interface'] for p in patterns if p.get('_verified') is False]
    if bad_verified:
        errors.append(f"[K] 残留 _verified=false {len(bad_verified)}（verify-apply 应已移除）: {bad_verified[:5]}")
    else:
        verified_n = sum(1 for p in patterns if '_verified' in p)
        oks.append(f"[K] _verified 一致（{verified_n} 条带标记，无 false 残留）")

    # L dispatchKey 全局唯一对接键（方案A：module:package.class）
    l_err, l_modwarn = [], []
    for p in patterns:
        iface = p.get('interface', '')
        short = iface.split('.')[-1]
        dk = p.get('dispatchKey')
        if not dk:
            l_err.append(f"{short}: 缺 dispatchKey")
            continue
        if ':' not in dk or dk.rsplit(':', 1)[-1] != iface:  # 末段（最后冒号后）须 == interface
            l_err.append(f"{short}: dispatchKey={dk!r} 末段 != interface")
            continue
        dk_mod = dk.rsplit(':', 1)[0]
        iface_rel = cidx.get(short)
        if iface_rel:
            actual_mod = iface_rel.split('/')[0]
            if actual_mod != dk_mod:
                l_modwarn.append(f"{short}: dispatchKey module={dk_mod!r} != 实际 {actual_mod!r}")
    if l_err:
        errors.append(f"[L] dispatchKey 缺失/格式问题 {len(l_err)}: {l_err[:5]}")
    elif patterns:
        oks.append(f"[L] dispatchKey 全齐全且格式正确（{len(patterns)} 条，module:package.class）")
    if l_modwarn:
        warns.append(f"[L] {len(l_modwarn)} 个 dispatchKey module 与实际不符: {l_modwarn[:3]}")

    if not patterns:
        warns.append("无分发点（patterns 为空，分发点维度跳过）")

    # 输出
    print("=" * 64)
    print(f"【phase1c 产物校验】patterns={len(patterns)}（min_implementations={min_impl}）")
    print("=" * 64)
    for m in oks:
        print(f"  ✅ {m}")
    for m in warns:
        print(f"  ⚠️  {m}")
    if errors:
        print(f"\n❌ {len(errors)} 个 error:")
        for m in errors:
            print(f"   - {m}")
    else:
        print("\n✅ 全部维度通过（0 error）")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser(description="phase1c 产物校验（独立实现）")
    ap.add_argument('project_dir', help='Java 项目根目录')
    ap.add_argument('cache_dir', help='.trace-cache 目录')
    ap.add_argument('--rules', default=DEFAULT_RULES, help=f'dispatch-rules.md 路径（默认 {DEFAULT_RULES}）')
    args = ap.parse_args()
    sys.exit(verify(os.path.abspath(args.project_dir), os.path.abspath(args.cache_dir), args.rules))


if __name__ == '__main__':
    main()
