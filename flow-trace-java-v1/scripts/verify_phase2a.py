#!/usr/bin/env python3
"""phase2a 产物校验（独立实现，不 import 任何 skill 模块）。

依据 phase2a-design.md 的 tree.json schema（3.1）/ progress.json schema（3.2）/
nodeId 构造（5.1）/ calls 保序（5.2）/ 13 决策（第 9 章）作为校验基准。
核心是「DAG 完整性校验」——引用完整、无环、root 无入边、多 parent 调用链不丢
（决策 2 核心修复点 ISSUE-2a-36）、calls 保序（决策 13）。

图遍历（无环 / 可达 / 多 parent 统计）在脚本内独立实现，不依赖 _tree_graph.py。

用法:
    python3 verify_phase2a.py <project_dir> <cache_dir>                       # 全量校验所有 *-tree.json
    python3 verify_phase2a.py <project_dir> <cache_dir> --entry-id <id>       # 单入口校验

退出码: 0 = 无 error（warn 不影响）; 1 = 有 error
"""
import json, os, re, sys, argparse
from collections import Counter, defaultdict

CALL_TYPES = {'DIRECT', 'POLYMORPHIC', 'ASYNC', 'DISPATCH', 'EXTERNAL', 'RMB', 'HTTP', 'MQ', 'EVENT'}


# ── 独立解析函数（design 5.1 规则，非 skill 代码）──────────────────────

def build_node_id(file_path, class_name, method):
    """design 5.1: nodeId = 模块名:包名.类名:方法名。
    模块名 = filePath 第一段；包名.类名 = filePath 中 src/main/java（或 src/test/java）之后推导（**非 class 字段**——
    RMB 终点等包装场景下 class 字段指向被包装的 Client，与 filePath 来源不同）。
    filePath 为 null（外部依赖）时 fallback 为 2 段 `包名.类名:方法名`（design 5.1 fallback）。"""
    if not file_path:
        return f"{class_name}:{method}"
    parts = file_path.split('/')
    module = parts[0] if parts else ''
    pkg_class = class_name
    for marker in ('src/main/java/', 'src/test/java/'):
        idx = file_path.find(marker)
        if idx >= 0:
            rel = file_path[idx + len(marker):]
            if rel.endswith('.java'):
                rel = rel[:-5]
            pkg_class = rel.replace('/', '.')
            break
    return f"{module}:{pkg_class}:{method}"


def build_incoming(edges):
    inc = defaultdict(list)
    for e in edges:
        inc[e.get('to')].append(e)
    return inc


def build_outgoing(edges):
    out = defaultdict(list)
    for e in edges:
        out[e.get('from')].append(e)
    return out


def has_cycle(node_ids, edges):
    """DFS 三色标记检测有向环。返回 True 若有环。"""
    out = defaultdict(list)
    for e in edges:
        out[e.get('from')].append(e.get('to'))
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}
    stack = []
    for start in node_ids:
        if color[start] != WHITE:
            continue
        stack.append((start, iter(out.get(start, []))))
        while stack:
            u, it = stack[-1]
            if color[u] == WHITE:
                color[u] = GRAY
            advanced = False
            for v in it:
                if v not in color:
                    continue
                if color[v] == GRAY:
                    return True
                if color[v] == WHITE:
                    stack.append((v, iter(out.get(v, []))))
                    advanced = True
                    break
            if not advanced:
                color[u] = BLACK
                stack.pop()
    return False


def reachable_from(root, edges):
    """从 root 出发可达的 node 集合（BFS/DFS）。"""
    out = defaultdict(list)
    for e in edges:
        out[e.get('from')].append(e.get('to'))
    seen, stack = set(), [root]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        for v in out.get(u, []):
            if v not in seen:
                stack.append(v)
    return seen


# ── 单入口校验 ──────────────────────────────────────────────────────

def verify_entry(project_dir, cache_dir, entry_id, entries_index, dispatch_keys=None):
    errors, warns, oks = [], [], []
    p2a = os.path.join(cache_dir, 'phase2a')
    tree_path = os.path.join(p2a, f'{entry_id}-tree.json')
    prog_path = os.path.join(p2a, f'{entry_id}-progress.json')

    if not os.path.exists(tree_path):
        return [f"[{entry_id}] tree.json 不存在（该入口未 init？）"], [], [], None

    tree = json.load(open(tree_path))
    nodes = tree.get('nodes', {}) if isinstance(tree.get('nodes'), dict) else {}
    edges = tree.get('edges', []) if isinstance(tree.get('edges'), list) else []
    root = tree.get('rootNodeId')
    nid_count, edge_count = len(nodes), len(edges)

    prog = json.load(open(prog_path)) if os.path.exists(prog_path) else None

    # ── A 顶层字段（design 3.1.1）──
    top_required = ['version', 'edgeKeyFormat', 'entryId', 'rootNodeId', 'nodes', 'edges']
    miss = [f for f in top_required if f not in tree]
    if miss:
        errors.append(f"[A] 顶层缺字段: {miss}")
    else:
        extra = '（entryType=' + tree['entryType'] + '）' if 'entryType' in tree else ''
        oks.append(f"[A] 顶层 6 字段齐全{extra}")

    # ── B 版本/格式（决策 5）──
    if tree.get('version') != '2.0':
        errors.append(f"[B] version 非 2.0: {tree.get('version')!r}")
    elif tree.get('edgeKeyFormat') != 'nodeId':
        errors.append(f"[B] edgeKeyFormat 非 nodeId: {tree.get('edgeKeyFormat')!r}")
    else:
        oks.append(f"[B] version=2.0 + edgeKeyFormat=nodeId（决策5）")

    # ── C entryId 一致 ──
    if tree.get('entryId') != entry_id:
        errors.append(f"[C] entryId={tree.get('entryId')!r} != 文件名 {entry_id}")
    else:
        oks.append(f"[C] entryId 与文件名一致")

    # ── D root 存在 ──
    if root not in nodes:
        errors.append(f"[D] rootNodeId 不在 nodes: {root}")
    else:
        oks.append(f"[D] rootNodeId ∈ nodes")

    # ── E nodes 是 dict（决策 1）──
    if not isinstance(tree.get('nodes'), dict):
        errors.append(f"[E] nodes 非 dict: {type(tree.get('nodes')).__name__}")
    else:
        oks.append(f"[E] nodes 是 dict（决策1，key=nodeId）")

    # ── F node 必填字段（决策 1/2）──
    node_required = ['shortId', 'nodeId', 'class', 'method', 'terminal']
    f_err = []
    for nid, node in nodes.items():
        for rf in node_required:
            v = node.get(rf)
            if v is None or v == '':
                f_err.append(f"{nid}: 缺 {rf}")
        if not isinstance(node.get('terminal'), bool):
            f_err.append(f"{nid}: terminal 非 bool")
    if f_err:
        errors.append(f"[F] node 必填字段问题 {len(f_err)}: {f_err[:5]}")
    else:
        oks.append(f"[F] 全 node 必填字段齐全（{nid_count} 节点；filePath 可 null）")

    # ── G nodeId 格式（error）+ 独立重算（warn，ISSUE-2a-39）──
    # 格式错误（无冒号等）= error；重算不符 = warn（discover 跨模块 targetFilePath 报错致
    # nodeId 与 filePath 不同步，内部引用仍完整，留 reconcile 处理，详见 ISSUE-2a-39）
    g_fmt_err, g_recalc, g_skip = [], [], 0
    for nid, node in nodes.items():
        if nid.count(':') < 1:
            g_fmt_err.append(f"{nid}: nodeId 无冒号（格式错误）")
        fp, cls, meth = node.get('filePath'), node.get('class', ''), node.get('method', '')
        if fp is None:
            g_skip += 1
            continue
        if build_node_id(fp, cls, meth) != nid:
            g_recalc.append(nid)
    if g_fmt_err:
        errors.append(f"[G] nodeId 格式错误 {len(g_fmt_err)}: {g_fmt_err[:5]}")
    if g_recalc:
        warns.append(f"[G] {len(g_recalc)} 个 nodeId 重算不符（discover 跨模块 targetFilePath 报错，ISSUE-2a-39）: {g_recalc[:3]}")
    if not g_fmt_err and not g_recalc:
        suf = f"（{g_skip} 个 null filePath 跳过重算）" if g_skip else ''
        oks.append(f"[G] nodeId 格式合法 + 重算一致{suf}")

    # ── H key == node.nodeId（决策 1）──
    h_err = [nid for nid, node in nodes.items() if node.get('nodeId') != nid]
    if h_err:
        errors.append(f"[H] key != node.nodeId {len(h_err)}: {h_err[:5]}")
    else:
        oks.append(f"[H] 全 node key == node.nodeId（决策1）")

    # ── I nodeId 唯一（dict 天然，决策 2 单实例）──
    oks.append(f"[I] nodeId 唯一（dict key 天然，决策2 单实例）")

    # ── J shortId 格式（决策 6/8）──
    j_bad = [f"{nid}: {node.get('shortId')!r}" for nid, node in nodes.items()
             if not re.match(r'^n_[0-9a-f]{8,}$', node.get('shortId', ''))]
    if j_bad:
        warns.append(f"[J] shortId 格式异常 {len(j_bad)}: {j_bad[:3]}")
    else:
        oks.append(f"[J] shortId 全 n_+hex（决策6/8）")

    # ── K filePath 相对 ──
    k_err = [f"{nid}: {node.get('filePath')}" for nid, node in nodes.items()
             if node.get('filePath') and str(node.get('filePath')).startswith('/')]
    if k_err:
        errors.append(f"[K] filePath 绝对路径 {len(k_err)}: {k_err[:3]}")
    else:
        oks.append(f"[K] filePath 全相对路径（或 null 外部依赖）")

    # ── L edge 必填字段（决策 9）──
    edge_required = ['id', 'from', 'to', 'layer', 'callType']
    l_err = [f"{e.get('id', '?')}: 缺 {rf}" for e in edges for rf in edge_required if e.get(rf) is None]
    if l_err:
        errors.append(f"[L] edge 必填字段问题 {len(l_err)}: {l_err[:5]}")
    else:
        oks.append(f"[L] 全 edge 必填字段齐全（{edge_count} 边）")

    # ── M 引用完整 ──
    m_err = []
    for e in edges:
        if e.get('from') not in nodes:
            m_err.append(f"{e.get('id')}: from 悬空")
        if e.get('to') not in nodes:
            m_err.append(f"{e.get('id')}: to 悬空")
    if m_err:
        errors.append(f"[M] 悬空引用 {len(m_err)}: {m_err[:5]}")
    else:
        oks.append(f"[M] 引用完整（无悬空 from/to）")

    # ── N edge.id 唯一 ──
    id_dups = [i for i, c in Counter(e.get('id') for e in edges).items() if c > 1]
    if id_dups:
        errors.append(f"[N] edge.id 重复: {id_dups[:5]}")
    else:
        oks.append(f"[N] edge.id 全唯一（e0..e{max(edge_count-1,0)}）")

    # ── O (from,to) 去重（决策 9）──
    pair_dups = [p for p, c in Counter((e.get('from'), e.get('to')) for e in edges).items() if c > 1]
    if pair_dups:
        errors.append(f"[O] (from,to) 重复 {len(pair_dups)}: {pair_dups[:3]}（决策9 去重失效）")
    else:
        oks.append(f"[O] (from,to) 无重复（决策9）")

    # ── P layer（决策 3）──
    p_err = [f"{e.get('id')}: layer={e.get('layer')!r}" for e in edges
             if not isinstance(e.get('layer'), int) or e.get('layer') < 1]
    if p_err:
        errors.append(f"[P] layer 问题 {len(p_err)}: {p_err[:5]}")
    else:
        oks.append(f"[P] layer 全 int≥1（决策3）")

    # ── Q callType 合法 ──
    bad_ct = [f"{e.get('id')}={e.get('callType')}" for e in edges if e.get('callType') not in CALL_TYPES]
    if bad_ct:
        warns.append(f"[Q] 非标准 callType {len(bad_ct)}: {bad_ct[:5]}")
    else:
        oks.append(f"[Q] callType 全合法")

    # ── R sourceLine（决策 13）──
    r_bad = [f"{e.get('id')}: {e.get('sourceLine')!r}" for e in edges
             if e.get('sourceLine') is not None and (not isinstance(e.get('sourceLine'), int) or e.get('sourceLine') <= 0)]
    r_missing = sum(1 for e in edges if e.get('sourceLine') is None)
    if r_bad:
        warns.append(f"[R] sourceLine 非法 {len(r_bad)}: {r_bad[:5]}")
    if r_missing:
        warns.append(f"[R] {r_missing}/{edge_count} 条 edge 无 sourceLine（决策13保序依据缺失）")
    if not r_bad and not r_missing:
        oks.append(f"[R] 全 edge 有 sourceLine（int>0）")

    # ── S condition（决策 13）──
    s_null = sum(1 for e in edges if e.get('condition') is None)
    if s_null:
        warns.append(f"[S] {s_null}/{edge_count} 条 edge condition=null（决策13控制流标注缺失）")
    else:
        oks.append(f"[S] 全 edge condition 非空（决策13）")

    # ── T root 无入边（决策 7，排除自环）──
    # 自环（from==to==root，重载/同名方法递归，ISSUE-2a-38）归维度 U warn，不算违反决策 7
    inc = build_incoming(edges)
    real_inc_root = [e for e in inc.get(root, []) if e.get('from') != root]
    if real_inc_root:
        errors.append(f"[T] root 有真实入边 {len(real_inc_root)} 条（决策7）")
    else:
        oks.append(f"[T] root 无入边（决策7，layer 隐式 0；自环归维度U）")

    # ── U 环检测（warn，非 error）──
    # design nodeId = 模块:类:方法名（5.1，不含参数签名）→ 重载方法 nodeId 相同，
    # 重载分派/同名方法互调会产生自环（from==to）。多节点环则是互调/回调。
    # 二者均为 phase2a 设计局限/真实代码模式，调用关系完整；design 决策11 phase6
    # 用路径级 visited 防环，故环存在不判 error，仅 warn 报告。
    self_loops = [e.get('id') for e in edges if e.get('from') == e.get('to')]
    multi_edges = [e for e in edges if e.get('from') != e.get('to')]
    has_multi = has_cycle(list(nodes.keys()), multi_edges)
    if self_loops and has_multi:
        warns.append(f"[U] {len(self_loops)} 自环（重载/同名互调，nodeId 无重载区分）+ 多节点环（phase6 路径级 visited 处理）")
    elif self_loops:
        warns.append(f"[U] {len(self_loops)} 自环 edge（重载/同名方法互调，nodeId 无重载区分）: {self_loops[:3]}")
    elif has_multi:
        warns.append(f"[U] 多节点环（互调/回调，phase6 决策11 路径级 visited 处理）")
    else:
        oks.append(f"[U] 无环（无自环 + 无多节点环）")

    # ── V 引用对称 ──
    no_inc = [nid for nid in nodes if nid != root and not inc.get(nid)]
    if no_inc:
        warns.append(f"[V] {len(no_inc)} 个非 root 节点无入边: {no_inc[:3]}")
    else:
        oks.append(f"[V] 所有非 root 节点均有入边")

    # ── W 可达性 ──
    reach = reachable_from(root, edges) if root else set()
    isolated = [nid for nid in nodes if nid not in reach]
    if isolated:
        warns.append(f"[W] {len(isolated)} 个节点从 root 不可达: {isolated[:3]}")
    else:
        oks.append(f"[W] 全部 {nid_count} 节点从 root 可达")

    # ── X 多 parent 调用链不丢（决策 2 核心，ISSUE-2a-36 修复点）──
    shared = [(nid, len(es)) for nid, es in inc.items() if len(es) >= 2]
    if shared:
        oks.append(f"[X] {len(shared)} 个共享节点（多 parent 调用链保留，决策2核心）最多入边 {max(s[1] for s in shared)}")
    else:
        warns.append(f"[X] 无共享节点（本入口纯树形，决策2多 parent 未触发）")

    # ── Y terminal/truncated 拆分（决策 4）──
    term_nodes = [nid for nid, n in nodes.items() if n.get('terminal')]
    truncated_e = [e.get('id') for e in edges if e.get('truncated')]
    di_filled = sum(1 for nid, n in nodes.items() if n.get('terminal') and n.get('domainInteraction'))
    if term_nodes:
        oks.append(f"[Y] terminal 节点 {len(term_nodes)}（DI 已填 {di_filled}）+ truncated edge {len(truncated_e)}（决策4）")
    else:
        warns.append(f"[Y] 无 terminal 节点")

    # ── Z calls 保序（决策 13，同 from 出边按 sourceLine 升序）──
    out = build_outgoing(edges)
    z_bad = []
    for frm, es in out.items():
        sls = [e.get('sourceLine') for e in es]
        if all(isinstance(s, int) for s in sls) and sls != sorted(sls):
            z_bad.append(frm.split(':')[-1])
    if z_bad:
        warns.append(f"[Z] {len(z_bad)} 个 from 的出边未按 sourceLine 升序: {z_bad[:3]}")
    else:
        oks.append(f"[Z] 同 from 出边按 sourceLine 升序（决策13保序）")

    # ── AA-AD progress.json（design 3.2）──
    if prog is None:
        warns.append(f"[AA-AD] progress.json 缺失，进度维度跳过")
    else:
        if prog.get('totalNodes') != nid_count:
            errors.append(f"[AA] progress.totalNodes={prog.get('totalNodes')} != len(nodes)={nid_count}")
        else:
            oks.append(f"[AA] totalNodes 一致（{nid_count}）")
        if prog.get('totalEdges') != edge_count:
            errors.append(f"[AB] progress.totalEdges={prog.get('totalEdges')} != len(edges)={edge_count}")
        else:
            oks.append(f"[AB] totalEdges 一致（{edge_count}）")
        pending = prog.get('pendingNodes', [])
        expanded = prog.get('expandedNodes', [])
        if pending:
            warns.append(f"[AC] BFS 未完成，pending={len(pending)}")
        elif root in expanded:
            oks.append(f"[AC] BFS 完成（pending 空，expanded 含 root）")
        else:
            warns.append(f"[AC] pending 空但 expanded 不含 root")
        miss_ctrl = [c for c in ['maxDepth', 'maxNodes', 'maxFanout'] if c not in prog]
        if miss_ctrl:
            warns.append(f"[AD] progress 缺控制参数 {miss_ctrl}")
        else:
            oks.append(f"[AD] 控制参数 maxDepth/maxNodes/maxFanout 齐")

    # ── AE-AF 跨阶段对齐（phase1a）──
    entry = entries_index.get(entry_id)
    if entry is None:
        warns.append(f"[AE] entries.json 无此 entry，跨阶段对齐跳过")
    else:
        if root != entry.get('nodeId'):
            errors.append(f"[AE] rootNodeId != entry.nodeId={entry.get('nodeId')!r}（phase1a build_node_id 不一致）")
        else:
            oks.append(f"[AE] rootNodeId == entry.nodeId（phase1a 对齐）")
        e_fp = entry.get('filePath')
        r_fp = nodes.get(root, {}).get('filePath')
        if e_fp and r_fp and r_fp != e_fp:
            errors.append(f"[AF] root.filePath={r_fp!r} != entry.filePath={e_fp!r}")
        else:
            oks.append(f"[AF] root.filePath 对齐 entry")

    # ── AG 截断流/未展开穿透节点（树级独立检测，不依赖 progress；ISSUE-2a-44 下游质量风险）──
    truncated_to = {e.get('to') for e in edges if e.get('truncated')}
    unexpanded = [nid for nid, n in nodes.items()
                  if not n.get('terminal') and n.get('filePath')
                  and not out.get(nid) and nid not in truncated_to]
    if unexpanded:
        warns.append(f"[AG] {len(unexpanded)} 个非终点节点有 filePath 但 0 出边且未截断"
                     f"（疑似未展开/应标终点，截断流风险 ISSUE-2a-44）: {[u.split(':')[-1] for u in unexpanded[:5]]}")
    else:
        oks.append(f"[AG] 无截断流（非终点节点均已展开或正常截断）")

    # ── AH DISPATCH patternRef = dispatchKey（方案A：跨阶段对接键，ISSUE-2b-17）──
    if dispatch_keys:
        ah_bad = [f"{n.get('class','?').split('.')[-1]}.{n.get('method')}:{n.get('patternRef')!r}"
                  for n in nodes.values()
                  if n.get('endpointType') == 'DISPATCH' and n.get('patternRef') not in dispatch_keys]
        disp_n = sum(1 for n in nodes.values() if n.get('endpointType') == 'DISPATCH')
        if ah_bad:
            warns.append(f"[AH] {len(ah_bad)}/{disp_n} 个 DISPATCH patternRef 非 dispatchKey（迁移前残留/脏值/非注册分发点）: {ah_bad[:5]}")
        elif disp_n:
            oks.append(f"[AH] 全部 {disp_n} 个 DISPATCH patternRef ∈ dispatchKey 集")

    # ── AI RMB 发送端 topic 完整（RMB 桥接修复；外部无 filePath 节点豁免）──
    rmb_internal = [n for n in nodes.values()
                    if n.get('endpointType') == 'RMB_EXTERNAL' and n.get('filePath')]
    ai_bad = [f"{n.get('class','?').split('.')[-1]}.{n.get('method')}"
              for n in rmb_internal
              if not ((n.get('domainInteraction') or {}).get('routingKeys') or {}).get('topic')]
    if ai_bad:
        warns.append(f"[AI] {len(ai_bad)}/{len(rmb_internal)} 个内部 RMB_EXTERNAL 节点缺 routingKeys.topic"
                     f"（rmb-topic-backfill 未跑/源码无@RmbTopic，桥接会漏）: {ai_bad[:5]}")
    elif rmb_internal:
        oks.append(f"[AI] 全部 {len(rmb_internal)} 个内部 RMB 发送节点有 topic")

    stats = {'nodes': nid_count, 'edges': edge_count, 'terminal': len(term_nodes), 'shared': len(shared)}
    return errors, warns, oks, stats


def main():
    ap = argparse.ArgumentParser(description="phase2a 产物校验（独立实现，不 import skill 模块）")
    ap.add_argument('project_dir', help='Java 项目根目录')
    ap.add_argument('cache_dir', help='.trace-cache 目录')
    ap.add_argument('--entry-id', help='单入口校验（默认全量校验所有 *-tree.json）')
    args = ap.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    cache_dir = os.path.abspath(args.cache_dir)
    p2a = os.path.join(cache_dir, 'phase2a')

    if not os.path.isdir(p2a):
        print(f"❌ 找不到 {p2a}（phase2a 是否已运行？）")
        return 1

    entries_index = {}
    ej = os.path.join(cache_dir, 'phase1a', 'entries.json')
    if os.path.exists(ej):
        for e in json.load(open(ej)).get('entries', []):
            entries_index[e.get('id')] = e

    dispatch_keys = set()  # 方案A：pattern-index 的 dispatchKey 集（[AH] 校验 DISPATCH patternRef）
    pidx = os.path.join(cache_dir, 'phase1c', 'pattern-index.json')
    if os.path.exists(pidx):
        for p in json.load(open(pidx)).get('patterns', []):
            if p.get('dispatchKey'):
                dispatch_keys.add(p['dispatchKey'])

    if args.entry_id:
        entry_ids = [args.entry_id]
    else:
        entry_ids = sorted(f.replace('-tree.json', '') for f in os.listdir(p2a) if f.endswith('-tree.json'))

    t_err = t_warn = t_ok = 0
    for eid in entry_ids:
        errs, wrns, oks, stats = verify_entry(project_dir, cache_dir, eid, entries_index, dispatch_keys)
        hdr = f"【{eid}】" + (f" nodes={stats['nodes']} edges={stats['edges']} term={stats['terminal']} shared={stats['shared']}" if stats else "")
        print(f"\n{'=' * 64}\n{hdr}\n{'=' * 64}")
        for m in oks:
            print(f"  ✅ {m}")
        for m in wrns:
            print(f"  ⚠️  {m}")
        for m in errs:
            print(f"  ❌ {m}")
        t_err += len(errs)
        t_warn += len(wrns)
        t_ok += len(oks)

    print(f"\n{'=' * 64}")
    print(f"【汇总】入口 {len(entry_ids)} | ✅ {t_ok} ok  ⚠️  {t_warn} warn  ❌ {t_err} error")
    print('=' * 64)
    return 1 if t_err else 0


if __name__ == '__main__':
    sys.exit(main())
