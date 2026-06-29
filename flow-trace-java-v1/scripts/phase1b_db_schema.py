"""Phase 1b: DB Schema Collection for flow-trace-java

Collects database table/operation info from 2 sources (pure source code, no graph.db):
1. Java Mapper annotations (@Select/@Insert/@Update/@Delete)
2. XML Mapper files (<select>/<insert>/<update>/<delete>)

Outputs db-schema-tables.json + db-schema-lookup.json (lookup dictionary for
Phase 2a endpoint identification).
"""
import json, glob, os, re, argparse


def parse_args():
    p = argparse.ArgumentParser(description="Phase 1b: DB Schema Collection (flow-trace-java)")
    p.add_argument("project_dir", help="Java project root directory")
    p.add_argument("cache_dir", help="Cache root directory (.trace-cache/)")
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

        pos = 0
        while pos < len(content):
            best_pos = len(content)
            best_type = None
            for annot in ('@Select', '@Insert', '@Update', '@Delete'):
                idx = content.find(annot, pos)
                if idx >= 0 and idx < best_pos:
                    best_pos = idx
                    best_type = annot.lstrip('@').upper()

            if best_type is None:
                break

            start = best_pos
            paren_pos = content.find('(', start + len(best_type))
            if paren_pos < 0:
                pos = start + 1
                continue

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

            if depth != 0:
                print(f"  WARNING: annotation body exceeds 10000 chars in {cls_name}, skipped")
                pos = end_pos + 1
                continue

            annot_body = content[paren_pos + 1:end_pos]

            rest = content[end_pos + 1:end_pos + 200]
            mm = METHOD_PATTERN.search(rest)
            if not mm:
                pos = end_pos + 1
                continue
            method_name = mm.group(1)

            sql_parts = re.findall(r'"([^"]*)"', annot_body)
            sql_text = ' '.join(sql_parts) if sql_parts else annot_body.strip().strip('"')

            table = extract_table_from_sql(sql_text)
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

        key = (entry['mapperClass'], entry['mapperMethod'], entry['operation'])
        if key not in tables[table]['operations']:
            tables[table]['operations'][key] = {
                'mapperClass': entry['mapperClass'],
                'statementId': entry['mapperMethod'],
                'type': entry['operation'],
                'sources': set(),
            }
        tables[table]['operations'][key]['sources'].add(entry['source'])

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


def do_phase1b():
    args = parse_args()
    project_dir = os.path.abspath(args.project_dir)
    cache_dir = os.path.abspath(args.cache_dir)

    print("Phase 1b: DB Schema Collection (flow-trace-java)")
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

    print("\n[Merging] All sources...")
    merged = merge_schema(all_entries)

    # Build lookup for Phase 2a: "MapperClass.methodName" -> {table, operation}
    lookup = {}
    for t in merged:
        for op in t['operations']:
            key = f"{op['mapperClass']}.{op['statementId']}"
            if key not in lookup:
                lookup[key] = {"table": t['tableName'], "operation": op['type']}

    # Output 1: tables (for human review)
    tables_output = {
        "version": "2.0",
        "sources": ["annotation-sql", "mapper-xml"],
        "totalTables": len(merged),
        "totalOperations": sum(len(t['operations']) for t in merged),
        "tables": merged,
    }

    tables_path = os.path.join(cache_dir, "phase1b", "db-schema-tables.json")
    os.makedirs(os.path.dirname(tables_path), exist_ok=True)
    with open(tables_path, 'w') as f:
        json.dump(tables_output, f, indent=2, ensure_ascii=False)

    # Output 2: lookup (for Phase 2a subagent consumption)
    lookup_output = {
        "version": "2.0",
        "lookupSize": len(lookup),
        "lookup": lookup,
    }

    lookup_path = os.path.join(cache_dir, "phase1b", "db-schema-lookup.json")
    with open(lookup_path, 'w') as f:
        json.dump(lookup_output, f, indent=2, ensure_ascii=False)

    print(f"\nPhase 1b Complete!")
    print(f"  Tables: {len(merged)}")
    print(f"  Operations: {sum(len(t['operations']) for t in merged)}")
    print(f"  Lookup entries: {len(lookup)}")
    print(f"  Tables file: {tables_path}")
    print(f"  Lookup file: {lookup_path}")


if __name__ == '__main__':
    do_phase1b()
