"""Phase 2: DB Schema Collection for flow-trace-db

Collects database table/operation info from 3 sources:
1. Java Mapper annotations (@Select/@Insert/@Update/@Delete)
2. XML Mapper files (<select>/<insert>/<update>/<delete>)
3. graph.db QUERIES relationships

Merges all into db-schema.json.
"""
import json, glob, os, re, sqlite3, argparse


def parse_args():
    p = argparse.ArgumentParser(description="Phase 2: DB Schema Collection")
    p.add_argument("project_dir", help="Java project root directory")
    p.add_argument("cache_dir", help="Cache root directory (.trace-cache/)")
    p.add_argument("db_path", help="Path to graph.db")
    return p.parse_args()


# ── SQL table name extraction ──────────────────────────────────────

TABLE_PATTERNS = [
    (re.compile(r'UPDATE\s+(\w+)', re.I), 'UPDATE'),
    (re.compile(r'INSERT\s+(?:INTO\s+)?(\w+)', re.I), 'INSERT'),
    (re.compile(r'DELETE\s+FROM\s+(\w+)', re.I), 'DELETE'),
    (re.compile(r'FROM\s+(\w+)', re.I), 'SELECT'),
]


def extract_table_from_sql(sql):
    """Extract table name from SQL text."""
    if not sql:
        return None
    # Clean MyBatis dynamic tags
    clean = re.sub(r'<[^>]+>', ' ', sql)
    clean = re.sub(r'#\{[^}]+\}', '?', clean)
    for pat, _ in TABLE_PATTERNS:
        m = pat.search(clean)
        if m:
            return m.group(1)
    return None


METHOD_PATTERN = re.compile(
    r'(?:public\s+|private\s+|protected\s+)?\S+\s+(\w+)\s*\('
)


def source_annotation_sql(project_dir):
    """Scan Java Mapper files for @Select/@Insert/@Update/@Delete annotations."""
    results = []
    java_files = glob.glob(os.path.join(
        project_dir, '**', 'src', 'main', 'java', '**', '*Mapper.java'
    ), recursive=True)
    java_files += glob.glob(os.path.join(
        project_dir, '**', 'src', 'main', 'java', '**', '*Dao.java'
    ), recursive=True)

    for fpath in sorted(set(java_files)):
        cls_name = os.path.basename(fpath).replace('.java', '')
        with open(fpath) as f:
            content = f.read()

        # Split content at annotation boundaries
        # Find all annotation blocks and the method following them
        pos = 0
        while pos < len(content):
            # Find next @Select/@Insert/@Update/@Delete
            best_pos = len(content)
            best_type = None
            for annot in ('@Select', '@Insert', '@Update', '@Delete'):
                idx = content.find(annot, pos)
                if idx >= 0 and idx < best_pos:
                    best_pos = idx
                    best_type = annot.lstrip('@').upper()

            if best_type is None:
                break

            # Extract annotation value (could be {...} or "...")
            start = best_pos
            # Find the opening ( after annotation name
            paren_pos = content.find('(', start + len(best_type))
            if paren_pos < 0:
                pos = start + 1
                continue

            # Find matching closing ) — skip over string literals to avoid false matches
            depth = 0
            end_pos = paren_pos
            in_string = False
            string_char = None
            i = paren_pos
            while i < min(len(content), paren_pos + 10000):
                c = content[i]
                if in_string:
                    if c == '\\' and i + 1 < len(content):
                        i += 2
                        continue
                    if c == string_char:
                        in_string = False
                else:
                    if c in ('"', "'"):
                        in_string = True
                        string_char = c
                    elif c == '(':
                        depth += 1
                    elif c == ')':
                        depth -= 1
                        if depth == 0:
                            end_pos = i
                            break
                i += 1

            annot_body = content[paren_pos + 1:end_pos]

            # Find method name after the annotation closing )
            rest = content[end_pos + 1:end_pos + 200]
            mm = METHOD_PATTERN.search(rest)
            if not mm:
                pos = end_pos + 1
                continue
            method_name = mm.group(1)

            # Extract SQL from annotation body
            # Handle: {"line1", "line2", ...} format
            sql_parts = re.findall(r'"([^"]*)"', annot_body)
            sql_text = ' '.join(sql_parts) if sql_parts else annot_body.strip().strip('"')

            table = extract_table_from_sql(sql_text)
            # Use annotation name as primary operation type (most reliable)
            op_type = best_type

            results.append({
                'table': table or '',
                'mapperClass': cls_name,
                'mapperMethod': method_name,
                'operation': op_type,
                'sql': sql_text[:200],
                'source': 'annotation-sql',
            })

            pos = end_pos + 1

    return results


# ── Source 2: XML Mapper files ──────────────────────────────────────

def source_xml_mapper(project_dir):
    """Scan XML Mapper files for SQL statements."""
    results = []
    # XML mappers can be in resources/ or alongside Java sources in java/
    xml_files = []
    for base in ('resources', 'java'):
        xml_files += glob.glob(os.path.join(
            project_dir, '**', 'src', 'main', base, '**', '*Mapper.xml'
        ), recursive=True)
        xml_files += glob.glob(os.path.join(
            project_dir, '**', 'src', 'main', base, '**', '*mapper*.xml'
        ), recursive=True)

    tag_map = {'select': 'SELECT', 'insert': 'INSERT', 'update': 'UPDATE', 'delete': 'DELETE'}

    for fpath in sorted(set(xml_files)):
        with open(fpath) as f:
            content = f.read()

        # Extract namespace
        ns_match = re.search(r'namespace\s*=\s*"([^"]+)"', content)
        namespace = ns_match.group(1) if ns_match else ''
        cls_name = namespace.split('.')[-1] if namespace else ''

        for tag, op in tag_map.items():
            for m in re.finditer(rf'<{tag}\s[^>]*id\s*=\s*"(\w+)"[^>]*>(.*?)</{tag}>', content, re.DOTALL):
                stmt_id = m.group(1)
                sql_body = m.group(2).strip()
                table = extract_table_from_sql(sql_body)

                results.append({
                    'table': table or '',
                    'mapperClass': cls_name,
                    'mapperMethod': stmt_id,
                    'operation': op,
                    'sql': sql_body[:200],
                    'source': 'mapper-xml',
                })

    return results


# ── Source 3: graph.db QUERIES ──────────────────────────────────────

def source_graph_db(db_path):
    """Extract DB operations from graph.db QUERIES relationships."""
    if not os.path.exists(db_path):
        return []

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            m.name AS mapper_method,
            c.name AS mapper_class,
            ce.name AS statement_id,
            json_extract(ce.properties_json, '$.statementKind') AS statement_kind,
            json_extract(ce.properties_json, '$.tableName') AS table_name_direct,
            json_extract(ce.properties_json, '$.sqlText') AS sql_text,
            json_extract(ce.properties_json, '$.namespace') AS namespace
        FROM relationships r
        JOIN nodes m ON m.id = r.source_id AND m.label = 'Method'
        JOIN nodes ce ON ce.id = r.target_id
        LEFT JOIN relationships hm ON hm.target_id = m.id AND hm.type = 'HAS_METHOD'
        LEFT JOIN nodes c ON c.id = hm.source_id
        WHERE r.type = 'QUERIES'
    """).fetchall()
    conn.close()

    results = []
    for r in rows:
        table = r['table_name_direct']
        sql_text = r['sql_text'] or ''
        if not table and sql_text:
            table = extract_table_from_sql(sql_text)
        if not table and r['namespace']:
            cn = r['namespace'].split('.')[-1]
            for suf in ('Mapper', 'Dao', 'Repository'):
                if cn.endswith(suf):
                    cn = cn[:-len(suf)]
                    break
            s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', cn)
            table = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

        op_map = {'select': 'SELECT', 'insert': 'INSERT', 'update': 'UPDATE', 'delete': 'DELETE'}
        op = op_map.get(r['statement_kind'], 'SELECT')

        results.append({
            'table': table or '',
            'mapperClass': r['mapper_class'] or '',
            'mapperMethod': r['mapper_method'] or r['statement_id'] or '',
            'operation': op,
            'sql': sql_text[:200],
            'source': 'graph-db',
        })
    return results


# ── Merge all sources ──────────────────────────────────────────────

def merge_schema(all_entries):
    """Merge entries from all sources into unified db-schema."""
    tables = {}  # tableName -> {operations: [], sources: set()}

    for entry in all_entries:
        table = entry['table']
        if not table:
            continue
        if table not in tables:
            tables[table] = {'operations': {}, 'sources': set()}

        tables[table]['sources'].add(entry['source'])

        # Key by (mapperClass, mapperMethod, operation) to deduplicate
        key = (entry['mapperClass'], entry['mapperMethod'], entry['operation'])
        if key not in tables[table]['operations']:
            tables[table]['operations'][key] = {
                'mapperClass': entry['mapperClass'],
                'statementId': entry['mapperMethod'],
                'type': entry['operation'],
                'sources': set(),
            }
        tables[table]['operations'][key]['sources'].add(entry['source'])

    # Build output
    result = []
    for tname in sorted(tables.keys()):
        ops = []
        for op_key in sorted(tables[tname]['operations'].keys()):
            op = tables[tname]['operations'][op_key]
            ops.append({
                'mapperClass': op['mapperClass'],
                'statementId': op['statementId'],
                'type': op['type'],
                'sources': sorted(op['sources']),
            })
        result.append({
            'tableName': tname,
            'operations': ops,
            'sources': sorted(tables[tname]['sources']),
        })
    return result


def do_phase2():
    args = parse_args()
    project_dir = args.project_dir
    cache_dir = args.cache_dir
    db_path = args.db_path

    print("Phase 2: DB Schema Collection")
    print("=" * 50)

    all_entries = []

    print("\n[Source 1] Scanning Java Mapper annotations...")
    annot = source_annotation_sql(project_dir)
    print(f"  Found {len(annot)} annotation SQL methods")
    all_entries.extend(annot)

    print("\n[Source 2] Scanning XML Mapper files...")
    xml = source_xml_mapper(project_dir)
    print(f"  Found {len(xml)} XML SQL statements")
    all_entries.extend(xml)

    print("\n[Source 3] Querying graph.db QUERIES...")
    gdb = source_graph_db(db_path)
    print(f"  Found {len(gdb)} QUERIES relationships")
    all_entries.extend(gdb)

    print("\n[Merging] All sources...")
    merged = merge_schema(all_entries)

    # Build lookup for Phase 1: "MapperClass.methodName" -> {table, operation}
    lookup = {}
    for t in merged:
        for op in t['operations']:
            key = f"{op['mapperClass']}.{op['statementId']}"
            if key not in lookup:
                lookup[key] = {"table": t['tableName'], "operation": op['type']}

    # Also add Tidb-prefixed versions (skip if already Tidb-prefixed)
    extra = {}
    for key, val in lookup.items():
        cls, method = key.rsplit('.', 1)
        if not cls.startswith('Tidb'):
            tidb_key = f"Tidb{cls}.{method}"
            if tidb_key not in lookup:
                extra[tidb_key] = val
    lookup.update(extra)

    output = {
        "version": "2.0",
        "sources": ["annotation-sql", "mapper-xml", "graph-db"],
        "totalTables": len(merged),
        "totalOperations": sum(len(t['operations']) for t in merged),
        "lookupSize": len(lookup),
        "tables": merged,
        "lookup": lookup,
    }

    out_path = os.path.join(cache_dir, "phase2", "db-schema.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nPhase 2 Complete!")
    print(f"  Tables: {len(merged)}")
    print(f"  Operations: {sum(len(t['operations']) for t in merged)}")
    print(f"  Lookup entries: {len(lookup)}")
    print(f"  Output: {out_path}")


if __name__ == '__main__':
    do_phase2()
