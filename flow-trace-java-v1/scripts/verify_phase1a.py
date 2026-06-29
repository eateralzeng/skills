#!/usr/bin/env python3
"""phase1a 产物校验（独立实现，不 import 任何 skill 模块）。

依据 skills-design/flow-trace-java-v1/design/phase1a-design.md 的字段 schema（第 3 章）
与解析规则（第 5 章）作为校验基准。所有解析逻辑（nodeId 构造、常量解析、注解 SQL
括号配平、继承链）在本脚本内独立实现，与被校验的 phase1a_entry_scan.py 解耦 ——
因此能客观发现 skill 解析 bug，且 skill 演进时本脚本仍稳定。

用法:
    python3 verify_phase1a.py <project_dir> <cache_dir>

退出码: 0 = 无 error（warn 不影响）; 1 = 有 error
"""
import json, os, re, sys, argparse
from collections import Counter

VALID_TYPES = {'controller', 'rmb', 'job'}
REQUIRED_FIELDS = ['id', 'type', 'className', 'methodName', 'filePath', 'nodeId']


# ── 独立解析函数（design 5.1 / 5.2 / 4.2.x，非 skill 代码）──────────

def build_node_id(fpath, method_name):
    """design 5.1: nodeId = 模块:包.类:方法。模块=filePath 首段，包=src/main/java 后路径。"""
    fp = fpath.replace('\\', '/')
    parts = fp.split('/')
    module = parts[0] if parts else ''
    marker = 'src/main/java/'
    idx = fp.find(marker)
    if idx >= 0:
        pkg_path = fp[idx + len(marker):].rsplit('/', 1)[0]
        pkg = pkg_path.replace('/', '.')
    else:
        pkg = ''
    cls = os.path.basename(fp).replace('.java', '')
    fqcn = f"{pkg}.{cls}" if pkg else cls
    return f"{module}:{fqcn}:{method_name}"


def resolve_const(expr, consts):
    """design 5.2: 取常量名最后一段查字典；未命中返回 [常量未解析:expr]。"""
    name = expr.strip().split('.')[-1]
    return consts.get(name, f"[常量未解析:{expr}]")


def extract_annot_body(src, method, annot='@RmbTopic'):
    """独立括号配平：找 method 定义上方最近的 annot，返回注解体（含 SQL）。"""
    m = re.search(r'\b' + re.escape(method) + r'\s*\(', src)
    if not m:
        return None
    before = src[:m.start()]
    idx = before.rfind(annot)
    if idx < 0:
        return None
    paren = before.find('(', idx)
    if paren < 0:
        return None
    depth, in_s, sc, i, end = 0, False, None, paren, paren
    while i < len(before):
        c = before[i]
        if in_s:
            if c == '\\':
                i += 2
                continue
            if c == sc:
                in_s = False
        else:
            if c in ('"', "'"):
                in_s, sc = True, c
            elif c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        i += 1
    return before[paren + 1:end]


def parse_topic_mode(body, consts):
    """从 @RmbTopic body 解析 (topic, mode)。覆盖字面量/常量引用/简写三种写法。"""
    if not body:
        return None, None
    topic = None
    m = re.search(r'topic\s*=\s*"([^"]+)"', body)
    if m:
        topic = m.group(1)
    else:
        m = re.search(r'topic\s*=\s*([A-Za-z_][\w.]*)', body)
        if m:
            topic = resolve_const(m.group(1), consts)
    mode = None
    m = re.search(r'topicMode\s*=\s*(?:TopicMode\.)?(\w+)', body)
    if m:
        mode = m.group(1)
    return topic, mode


JOB_BASES = {'QuartzJobBean', 'AbstractQuartzJob', 'SimpleTaskExecutor', 'Job',
             'SimpleJob', 'DataflowJob', 'ConcurrentTaskExecutor', 'CcpConcurrentTaskExecutor'}


def build_class_index(project_dir):
    """className -> 相对 filePath 索引（供 job 继承链向上找父类源码）。"""
    idx = {}
    for root, _, files in os.walk(project_dir):
        for fn in files:
            if fn.endswith('.java') and fn[:-5] not in idx:
                idx[fn[:-5]] = os.path.relpath(os.path.join(root, fn), project_dir)
    return idx


def chain_has_method(project_dir, cidx, rel_fp, method, depth=0, seen=None):
    """沿 extends 链向上找 method 定义（≤5 层，design 4.2.6）。"""
    if seen is None:
        seen = set()
    if depth > 5 or not rel_fp or rel_fp in seen:
        return False
    seen.add(rel_fp)
    try:
        src = open(os.path.join(project_dir, rel_fp)).read()
    except Exception:
        return False
    if re.search(r'\b' + re.escape(method) + r'\s*\(', src):
        return True
    ext = re.search(r'extends\s+([\w.]+)', src)
    if ext:
        base = ext.group(1).split('.')[-1]
        parent = cidx.get(base)
        if parent and chain_has_method(project_dir, cidx, parent, method, depth + 1, seen):
            return True
    return False


def chain_hit_base(project_dir, cidx, rel_fp, depth=0, seen=None):
    """递归 extends 链，任一祖先在 JOB_BASES 命中则返回基类名。"""
    if seen is None:
        seen = set()
    if depth > 5 or not rel_fp or rel_fp in seen:
        return None
    seen.add(rel_fp)
    try:
        src = open(os.path.join(project_dir, rel_fp)).read()
    except Exception:
        return None
    ext = re.search(r'extends\s+([\w.]+)', src)
    if ext:
        base = ext.group(1).split('.')[-1]
        if base in JOB_BASES:
            return base
        hit = chain_hit_base(project_dir, cidx, cidx.get(base), depth + 1, seen)
        if hit:
            return hit
    impl = re.search(r'implements\s+([\w.,\s]+)', src)
    if impl:
        for b in ('SimpleJob', 'DataflowJob'):
            if b in impl.group(1):
                return f'implements[{b}]'
    return None


# ── 主校验 ──────────────────────────────────────────────────────────

def verify(project_dir, cache_dir):
    errors, warns, oks = [], [], []
    e_path = os.path.join(cache_dir, 'phase1a', 'entries.json')
    if not os.path.exists(e_path):
        print(f"❌ 找不到 {e_path}（phase1a 是否已运行？）")
        return 1
    data = json.load(open(e_path))
    entries = data.get('entries', [])
    summary = data.get('summary', {})

    # constants.json 可选（phase1a 产物，但缺失时降级）
    c_path = os.path.join(cache_dir, 'phase1a', 'constants.json')
    consts = json.load(open(c_path)) if os.path.exists(c_path) else {}
    if not consts:
        warns.append("constants.json 缺失或为空，常量解析维度（H-rmb/I）将降级")

    cidx = build_class_index(project_dir)

    # A 结构
    if data.get('version') == '2.0':
        oks.append("[A] version=2.0")
    else:
        errors.append(f"[A] version 非 2.0: {data.get('version')!r}")
    if 'entries' in data and 'summary' in data:
        oks.append("[A] entries/summary 顶层结构齐全")
    else:
        errors.append("[A] 缺 entries 或 summary 顶层字段")

    # B 计数自洽
    counts = Counter(e.get('type') for e in entries)
    cnt_ok = True
    for t in VALID_TYPES:
        if counts.get(t, 0) != summary.get(t):
            errors.append(f"[B] summary.{t}={summary.get(t)} 与实际 {counts.get(t, 0)} 不一致")
            cnt_ok = False
    if len(entries) != summary.get('total'):
        errors.append(f"[B] summary.total={summary.get('total')} 与实际 {len(entries)} 不一致")
        cnt_ok = False
    if cnt_ok:
        oks.append(f"[B] 计数自洽（controller={counts.get('controller', 0)}/rmb={counts.get('rmb', 0)}/job={counts.get('job', 0)}/total={len(entries)}）")

    # C 字段+格式
    field_err = []
    for e in entries:
        for f in REQUIRED_FIELDS:
            v = e.get(f)
            if v is None or v == '':
                field_err.append(f"{e.get('id')}: 缺 {f}")
        if not re.match(r'^(controller|rmb|job)-\d{3}$', e.get('id', '')):
            field_err.append(f"id 格式异常: {e.get('id')!r}")
        if e.get('id', '').split('-')[0] != e.get('type'):
            field_err.append(f"{e.get('id')}: id 前缀 != type")
        if e.get('nodeId', '').count(':') < 2:
            field_err.append(f"{e.get('id')}: nodeId 格式异常 {e.get('nodeId')!r}")
        if e.get('filePath', '').startswith('/'):
            field_err.append(f"{e.get('id')}: filePath 非相对路径")
    if field_err:
        errors.append(f"[C] 字段/格式问题 {len(field_err)}: {field_err[:5]}")
    else:
        oks.append(f"[C] 全部字段合法 + id/nodeId 格式正确（{len(entries)} 条）")

    # D 唯一性
    id_dups = [i for i, c in Counter(e.get('id', '') for e in entries).items() if c > 1 and i]
    nid_dups = [n for n, c in Counter(e.get('nodeId', '') for e in entries).items() if c > 1 and n]
    if id_dups:
        errors.append(f"[D] id 重复: {id_dups[:5]}")
    if nid_dups:
        errors.append(f"[D] nodeId 重复: {nid_dups[:5]}")
    if not id_dups and not nid_dups:
        oks.append(f"[D] id/nodeId 全局唯一（{len(entries)} 条）")

    # E 类型特有字段
    ctrl = [e for e in entries if e.get('type') == 'controller']
    rmb = [e for e in entries if e.get('type') == 'rmb']
    job = [e for e in entries if e.get('type') == 'job']
    e_err = []
    for e in ctrl:
        if 'httpMapping' not in e:
            e_err.append(f"{e['id']}: controller 缺 httpMapping")
    for e in rmb:
        for f in ('rmbTopic', 'rmbTopicMode', 'transCode'):
            if f not in e:
                e_err.append(f"{e['id']}: rmb 缺 {f}")
    if e_err:
        errors.append(f"[E] 类型特有字段缺失 {len(e_err)}: {e_err[:5]}")
    else:
        oks.append(f"[E] 类型特有字段齐全（controller={len(ctrl)} rmb={len(rmb)} job={len(job)}）")

    # F filePath 存在
    missing = [(e.get('id'), fp) for e in entries
               if (fp := e.get('filePath')) and not os.path.exists(os.path.join(project_dir, fp))]
    if missing:
        errors.append(f"[F] filePath 指向文件不存在 {len(missing)}: {missing[:5]}")
    else:
        oks.append(f"[F] filePath 全量存在 {len(entries)}/{len(entries)}")

    # G nodeId 合规（独立重算对比）
    nid_mm = []
    for e in entries:
        fp = e.get('filePath'); method = e.get('methodName')
        if not fp or not method:
            continue  # C 维度已报字段缺失
        actual = e.get('nodeId')
        if actual is None:
            continue  # C 维度已报缺 nodeId
        exp = build_node_id(fp, method)
        if exp != actual:
            nid_mm.append((e.get('id'), actual, exp))
    if nid_mm:
        errors.append(f"[G] nodeId 不符合 design 5.1 规范 {len(nid_mm)}: {nid_mm[:3]}")
    else:
        oks.append(f"[G] nodeId 全部符合 design 5.1（模块:包.类:方法，{len(entries)}/{len(entries)}）")

    # H 源码回溯
    h_err = []
    # H1 controller @XxxMapping
    for e in ctrl:
        eid = e.get('id'); rel = e.get('filePath'); method = e.get('methodName')
        if not rel or not method:
            continue  # C 维度已报
        try:
            src = open(os.path.join(project_dir, rel)).read()
        except Exception:
            h_err.append(f"{eid}: 源码读取失败"); continue
        if method not in src:
            h_err.append(f"{eid}: methodName 不在源码")
        if 'Mapping(' not in src:
            h_err.append(f"{eid}: 源码无 HTTP @XxxMapping")
    # H2 rmb @RmbTopic（独立括号配平 + 常量解析）
    for e in rmb:
        eid = e.get('id'); rel = e.get('filePath'); method = e.get('methodName')
        if not rel or not method:
            continue
        try:
            src = open(os.path.join(project_dir, rel)).read()
        except Exception:
            h_err.append(f"{eid}: 源码读取失败"); continue
        body = extract_annot_body(src, method)
        if not body:
            h_err.append(f"{eid}: 方法上方无 @RmbTopic"); continue
        topic, mode = parse_topic_mode(body, consts)
        if topic is not None and topic != e.get('rmbTopic'):
            h_err.append(f"{eid}: topic 期望[{topic}] 实际[{e.get('rmbTopic')}]")
        if mode is not None and mode != e.get('rmbTopicMode'):
            h_err.append(f"{eid}: mode 期望[{mode}] 实际[{e.get('rmbTopicMode')}]")
    # H3 job 继承链找方法 + 依据
    for e in job:
        eid = e.get('id'); rel = e.get('filePath'); method = e.get('methodName')
        if not rel or not method:
            continue
        if not chain_has_method(project_dir, cidx, rel, method):
            h_err.append(f"{eid}: 入口方法沿继承链未找到")
        hit = chain_hit_base(project_dir, cidx, rel)
        has_ann = False
        try:
            src = open(os.path.join(project_dir, rel)).read()
            positions = [m.start() for m in re.finditer(r'\b' + re.escape(method) + r'\s*\(', src)]
            has_ann = any(re.search(r'@(Scheduled|XxlJob)', src[max(0, p - 200):p]) for p in positions)
        except Exception:
            pass
        if not has_ann and not hit:
            h_err.append(f"{eid}: 无注解无继承命中（弱依据）")
    if h_err:
        errors.append(f"[H] 源码回溯问题 {len(h_err)}: {h_err[:5]}")
    else:
        oks.append(f"[H] 源码回溯全过（controller {len(ctrl)} + rmb {len(rmb)} + job {len(job)}）")

    # I 常量残留
    if rmb:
        residual = [e['id'] for e in rmb if e.get('rmbTopic') and '[' in e['rmbTopic']]
        if residual:
            errors.append(f"[I] rmbTopic 常量未解析残留 {len(residual)}: {residual[:5]}")
        else:
            oks.append(f"[I] rmb topic 常量全解析（无残留，{len(rmb)} 条）")

    # 空类型 graceful 提示
    if not ctrl: warns.append("无 controller 入口（controller 维度跳过）")
    if not rmb: warns.append("无 rmb 入口（rmb 维度跳过）")
    if not job: warns.append("无 job 入口（job 维度跳过）")

    # 输出
    print("=" * 64)
    print(f"【phase1a 产物校验】entries={len(entries)}")
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
    ap = argparse.ArgumentParser(description="phase1a 产物校验（独立实现）")
    ap.add_argument('project_dir', help='Java 项目根目录')
    ap.add_argument('cache_dir', help='.trace-cache 目录')
    args = ap.parse_args()
    sys.exit(verify(os.path.abspath(args.project_dir), os.path.abspath(args.cache_dir)))


if __name__ == '__main__':
    main()
