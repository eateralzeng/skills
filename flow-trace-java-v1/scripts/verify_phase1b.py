#!/usr/bin/env python3
"""phase1b 产物校验（独立实现，不 import 任何 skill 模块）。

依据 skills-design/flow-trace-java-v1/design/phase1b-design.md 的字段 schema（第 3 章）、
SQL 解析规则（第 4.2 章）、表名正则（第 5.3 章）作为校验基准。所有解析逻辑（表名正则、
注解 SQL 括号配平、XML 标签扫描）在本脚本内独立实现，与 phase1b_db_schema.py 解耦。

用法:
    python3 verify_phase1b.py <project_dir> <cache_dir>

退出码: 0 = 无 error（warn 不影响）; 1 = 有 error
"""
import json, os, re, sys, argparse, glob
from collections import defaultdict

VALID_OP = {'SELECT', 'INSERT', 'UPDATE', 'DELETE'}
VALID_SRC = {'annotation-sql', 'mapper-xml'}


# ── 独立解析函数（design 4.2.3 / 4.2.1 / 4.2.2，非 skill 代码）──────

TABLE_PATTERNS = [
    (re.compile(r'UPDATE\s+(\w+)', re.I), 'UPDATE'),
    (re.compile(r'INSERT\s+(?:INTO\s+)?(\w+)', re.I), 'INSERT'),
    (re.compile(r'DELETE\s+FROM\s+(\w+)', re.I), 'DELETE'),
    (re.compile(r'FROM\s+(\w+)', re.I), 'SELECT'),
]


def extract_table(sql):
    """design 4.2.3 / 5.3: 清洗 MyBatis 动态标签 + #{}，按优先级取第一个表名。"""
    if not sql:
        return None
    clean = re.sub(r'<[^>]+>', ' ', sql)
    clean = re.sub(r'#\{[^}]+\}', '?', clean)
    for pat, _ in TABLE_PATTERNS:
        m = pat.search(clean)
        if m:
            return m.group(1)
    return None


METHOD_RE = re.compile(r'(?:public\s+|private\s+|protected\s+)?\S+\s+(\w+)\s*\(')


def scan_annotation_sql(project_dir):
    """design 4.2.1: 独立扫描 *Mapper.java+*Dao.java 注解 SQL（括号配平）。返回 entry 列表。"""
    results = []
    java_files = sorted(set(
        glob.glob(os.path.join(project_dir, '**', 'src', 'main', 'java', '**', '*Mapper.java'), recursive=True)
        + glob.glob(os.path.join(project_dir, '**', 'src', 'main', 'java', '**', '*Dao.java'), recursive=True)
    ))
    for fpath in java_files:
        try:
            content = open(fpath).read()
        except Exception:
            continue
        cls = os.path.basename(fpath)[:-5]
        pos = 0
        while pos < len(content):
            best_pos, best_type = len(content), None
            for annot in ('@Select', '@Insert', '@Update', '@Delete'):
                idx = content.find(annot, pos)
                if idx >= 0 and idx < best_pos:
                    best_pos, best_type = idx, annot.lstrip('@').upper()
            if best_type is None:
                break
            paren_pos = content.find('(', best_pos + len(best_type))
            if paren_pos < 0:
                pos = best_pos + 1; continue
            depth, in_s, sc, i, end = 0, False, None, paren_pos, paren_pos
            while i < min(len(content), paren_pos + 10000):
                c = content[i]
                if in_s:
                    if c == '\\' and i + 1 < len(content):
                        i += 2; continue
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
                            end = i; break
                i += 1
            if depth != 0:
                pos = end + 1; continue
            body = content[paren_pos + 1:end]
            mm = METHOD_RE.search(content[end + 1:end + 201])
            if not mm:
                pos = end + 1; continue
            sql_parts = re.findall(r'"([^"]*)"', body)
            sql = ' '.join(sql_parts) if sql_parts else body.strip().strip('"')
            results.append({
                'table': extract_table(sql), 'mapperClass': cls, 'mapperMethod': mm.group(1),
                'operation': best_type, 'sql': sql, 'source': 'annotation-sql', 'file': fpath,
            })
            pos = end + 1
    return results


def scan_xml_mapper(project_dir):
    """design 4.2.2: 独立扫描 XML Mapper（select/insert/update/delete 标签）。"""
    results = []
    xml_files = []
    for base in ('resources', 'java'):
        xml_files += glob.glob(os.path.join(project_dir, '**', 'src', 'main', base, '**', '*Mapper.xml'), recursive=True)
        xml_files += glob.glob(os.path.join(project_dir, '**', 'src', 'main', base, '**', '*mapper*.xml'), recursive=True)
    tag_map = {'select': 'SELECT', 'insert': 'INSERT', 'update': 'UPDATE', 'delete': 'DELETE'}
    for fpath in sorted(set(xml_files)):
        try:
            content = open(fpath).read()
        except Exception:
            continue
        ns = re.search(r'namespace\s*=\s*"([^"]+)"', content)
        namespace = ns.group(1) if ns else ''
        cls = namespace.split('.')[-1] if namespace else ''
        for tag, op in tag_map.items():
            for m in re.finditer(rf'<{tag}\s[^>]*id\s*=\s*"(\w+)"[^>]*>(.*?)</{tag}>', content, re.DOTALL):
                sql = m.group(2).strip()
                results.append({
                    'table': extract_table(sql), 'mapperClass': cls, 'mapperMethod': m.group(1),
                    'operation': op, 'sql': sql, 'source': 'mapper-xml', 'file': fpath,
                })
    return results


# ── 主校验 ──────────────────────────────────────────────────────────

def verify(project_dir, cache_dir):
    errors, warns, oks = [], [], []
    t_path = os.path.join(cache_dir, 'phase1b', 'db-schema-tables.json')
    l_path = os.path.join(cache_dir, 'phase1b', 'db-schema-lookup.json')
    if not os.path.exists(t_path) or not os.path.exists(l_path):
        print(f"❌ 找不到 phase1b 产物（{t_path} / {l_path}，phase1b 是否已运行？）")
        return 1
    T = json.load(open(t_path))
    L = json.load(open(l_path))
    tables = T.get('tables', [])
    lookup = L.get('lookup', {})

    # A 结构
    if T.get('version') == '2.0':
        oks.append("[A] tables.version=2.0")
    else:
        errors.append(f"[A] tables.version 非 2.0: {T.get('version')!r}")
    if T.get('sources') == ['annotation-sql', 'mapper-xml']:
        oks.append("[A] sources 正确")
    else:
        errors.append(f"[A] sources 异常: {T.get('sources')}")
    if T.get('totalTables') == len(tables):
        oks.append(f"[A] totalTables={len(tables)} 一致")
    else:
        errors.append(f"[A] totalTables={T.get('totalTables')} != 实际 {len(tables)}")
    real_ops = sum(len(t.get('operations', [])) for t in tables)
    if T.get('totalOperations') == real_ops:
        oks.append(f"[A] totalOperations={real_ops} 一致")
    else:
        errors.append(f"[A] totalOperations={T.get('totalOperations')} != 实际 {real_ops}")
    tnames = [t.get('tableName', '') for t in tables]
    if tnames == sorted(tnames):
        oks.append("[A] tables 按 tableName 升序")
    else:
        errors.append("[A] tables 未按 tableName 升序")

    # B 字段 + op 排序
    field_err = []
    for t in tables:
        if not t.get('tableName'):
            field_err.append("空 tableName")
        if not set(t.get('sources', [])) <= VALID_SRC:
            field_err.append(f"非法 sources {t.get('sources')}")
        opkeys = []
        for op in t.get('operations', []):
            if not op.get('mapperClass') or not op.get('statementId'):
                field_err.append(f"{t.get('tableName')}: 空 mapperClass/statementId")
            if op.get('type') not in VALID_OP:
                field_err.append(f"{t.get('tableName')}: 非法 type {op.get('type')}")
            if not set(op.get('sources', [])) <= VALID_SRC:
                field_err.append(f"{t.get('tableName')}: 非法 op.sources")
            opkeys.append((op.get('mapperClass', ''), op.get('statementId', ''), op.get('type', '')))
        if opkeys != sorted(opkeys):
            field_err.append(f"{t.get('tableName')}: operations 未排序")
    if field_err:
        errors.append(f"[B] 字段/排序问题 {len(field_err)}: {field_err[:5]}")
    else:
        oks.append(f"[B] 全部 table/op 字段合法 + 排序正确（{len(tables)} 表/{real_ops} op）")

    # C lookup 结构
    c_err = []
    if L.get('version') != '2.0':
        c_err.append(f"lookup.version 非 2.0: {L.get('version')!r}")
    if L.get('lookupSize') != len(lookup):
        c_err.append(f"lookupSize={L.get('lookupSize')} != 实际 {len(lookup)}")
    bad_keys = [k for k in lookup if '.' not in k or k.startswith('.') or k.endswith('.')]
    bad_vals = [k for k, v in lookup.items() if not v.get('table') or v.get('operation') not in VALID_OP]
    if bad_keys:
        c_err.append(f"key 异常 {len(bad_keys)}: {bad_keys[:5]}")
    if bad_vals:
        c_err.append(f"value 异常 {len(bad_vals)}: {bad_vals[:5]}")
    if c_err:
        errors.append(f"[C] lookup 结构问题: {c_err}")
    else:
        oks.append(f"[C] lookup 结构合法（{len(lookup)} 条）")

    # D 两产物一致性 + key 冲突
    op_index = [(o.get('mapperClass'), o.get('statementId'), o.get('type'), t.get('tableName'))
                for t in tables for o in t.get('operations', [])]
    lookup_mm = []
    for k, v in lookup.items():
        cls, _, method = k.rpartition('.')
        matches = [(c, s, tp, tn) for (c, s, tp, tn) in op_index if c == cls and s == method]
        if not matches:
            lookup_mm.append((k, 'tables 无对应 op'))
        elif not any(tp == v.get('operation') and tn == v.get('table') for (_, _, tp, tn) in matches):
            lookup_mm.append((k, f'table/op 不符 lookup={v}'))
    if lookup_mm:
        errors.append(f"[D] lookup↔tables 不一致 {len(lookup_mm)}: {lookup_mm[:3]}")
    else:
        oks.append(f"[D] lookup 全部 {len(lookup)} 条在 tables 有对应 table/operation")
    variants = defaultdict(set)
    for (c, s, tp, tn) in op_index:
        variants[f"{c}.{s}"].add((tn, tp))
    conflicts = {k: v for k, v in variants.items() if len(v) > 1}
    if conflicts:
        warns.append(f"[D] key 冲突 {len(conflicts)} 个（lookup 只保留首个，design issue 4）")
    else:
        oks.append(f"[D] 无 key 冲突（mapperClass.statementId 全唯一）")

    # E 覆盖度（独立扫描）
    annot = scan_annotation_sql(project_dir)
    xml = scan_xml_mapper(project_dir)
    annot_cls = set(e['mapperClass'] for e in annot)
    xml_cls = set(e['mapperClass'] for e in xml if e['mapperClass'])
    oks.append(f"[E] 注解源 {len(annot)} 方法/{len(annot_cls)} 类；XML 源 {len(xml)} 语句/{len(xml_cls)} 类（独立扫描）")

    # F 全量回溯（独立扫描结果对比 lookup）
    ent_by_key = defaultdict(list)
    for e in annot + xml:
        ent_by_key[f"{e['mapperClass']}.{e['mapperMethod']}"].append(e)
    f_mm, checked, miss = [], 0, 0
    for k, v in lookup.items():
        ents = ent_by_key.get(k, [])
        if not ents:
            miss += 1; continue
        checked += 1
        match = next((e for e in ents if e['operation'] == v.get('operation')), ents[0])
        if match['table'] != v.get('table') or match['operation'] != v.get('operation'):
            f_mm.append((k, f"重算 table={match['table']} op={match['operation']} vs lookup={v}"))
    if f_mm:
        errors.append(f"[F] 全量回溯不一致 {len(f_mm)}/{checked}: {f_mm[:5]}")
    else:
        oks.append(f"[F] 全量 {checked} 条独立重算 table/operation 与 lookup 一致")
    if miss:
        warns.append(f"[F] {miss} 条 lookup 在独立扫描中未定位（namespace≠文件名等），跳过")

    # G 已知问题（design 第 8 章，warn）
    empty_table = [e for e in annot + xml if not e['table']]
    ns_missing = len([e for e in xml if not e['mapperClass']])
    dao_files = len(set(e['file'] for e in annot if e['file'].endswith('Dao.java')))
    warns.append(f"[G] 表名解析失败 {len(empty_table)} 条（phase1b 已丢弃，design issue 6）")
    if ns_missing:
        warns.append(f"[G] XML namespace 缺失 {ns_missing} 条（design issue 5，lookup key 失效风险）")
    else:
        oks.append("[G] XML namespace 全部存在（无 issue 5 风险）")
    if dao_files:
        warns.append(f"[G] *Dao.java 扫描 {dao_files} 个（design issue 10，与下游「Dao 非终点」语义冲突）")

    # 输出
    print("=" * 64)
    print(f"【phase1b 产物校验】tables={len(tables)} operations={real_ops} lookup={len(lookup)}")
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
    ap = argparse.ArgumentParser(description="phase1b 产物校验（独立实现）")
    ap.add_argument('project_dir', help='Java 项目根目录')
    ap.add_argument('cache_dir', help='.trace-cache 目录')
    args = ap.parse_args()
    sys.exit(verify(os.path.abspath(args.project_dir), os.path.abspath(args.cache_dir)))


if __name__ == '__main__':
    main()
