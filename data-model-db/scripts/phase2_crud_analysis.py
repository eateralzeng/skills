#!/usr/bin/env python3
"""
Phase 2: CRUD Operation Analysis + Source Code Verification

Reads Phase 0 registry + graph.db MyBatis statements, parses CRUD field-level
details, verifies against source (XML > annotations > inline), outputs diffs.

Usage:
    python3 phase2_crud_analysis.py <db_path> <cache_dir> <project_src_dir>
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# UPDATE: column = #{...}  -- captures column name before = #{
_RE_UPDATE_SET = re.compile(r"(\w+)\s*=\s*#\{")

# INSERT with column list: INSERT INTO table (col1, col2, ...) VALUES (...)
_RE_INSERT_COLS = re.compile(r"INSERT\s+INTO\s+\w+\s*\(([^)]+)\)", re.IGNORECASE)

# INSERT with <trim>: extract  colName,  before VALUES section
_RE_TRIM_INSERT_COL = re.compile(r"<if>\s*(\w+)\s*,")

# SELECT fields after select keyword
_RE_SELECT_FIELDS = re.compile(r"select\s+(.*?)\s+from", re.IGNORECASE | re.DOTALL)

# Method name semantics patterns
_RE_SELECT_SEMANTIC = re.compile(
    r"^select(By|Overdue|ByIdx|WithLock|Condition|Pk|First|One|All|Count|Distinct|Recent|Latest)",
    re.IGNORECASE,
)

# Known SQL / MyBatis noise tokens to filter from field lists
_NOISE_TOKENS = {
    "jdbcType", "javaType", "typeHandler", "resultType", "parameterType",
    "VARCHAR", "CHAR", "INTEGER", "BIGINT", "DECIMAL", "TIMESTAMP", "DATE",
    "BIT", "LONGVARCHAR", "BLOB", "CLOB", "NUMERIC",
    "for", "update", "order", "by", "where", "and", "or", "not", "in",
    "set", "values", "into", "as", "on", "join", "left", "right", "inner",
    "group", "having", "limit", "offset", "union", "exists", "case", "when",
    "then", "else", "end", "like", "between", "asc", "desc", "distinct",
    "select", "from", "null", "is", "true", "false", "count",
}


# ---------------------------------------------------------------------------
# SQL field parsing helpers
# ---------------------------------------------------------------------------

def parse_update_set_fields(sql_text: str) -> Tuple[List[str], bool]:
    """Parse SET clause column names from UPDATE SQL.

    Returns (field_list, has_unresolved_include).
    """
    fields: List[str] = []
    seen: Set[str] = set()
    has_include = "<include>" in sql_text

    # Find all column = #{...} patterns
    for m in _RE_UPDATE_SET.finditer(sql_text):
        col = m.group(1).strip().lower()
        if col and col not in _NOISE_TOKENS and col not in seen:
            fields.append(col)
            seen.add(col)

    return fields, has_include


def parse_insert_columns(sql_text: str) -> Tuple[List[str], bool]:
    """Parse column names from INSERT SQL.

    Handles two forms:
      1. INSERT INTO table (col1, col2, ...) VALUES (...)
      2. INSERT INTO table <trim><if>col1,<if>col2,...<trim><if>#{},...

    Returns (column_list, has_unresolved_include).
    """
    columns: List[str] = []
    seen: Set[str] = set()
    has_include = "<include>" in sql_text

    # Form 1: explicit column list
    m = _RE_INSERT_COLS.search(sql_text)
    if m:
        raw_cols = m.group(1)
        for col in re.split(r"[,\s]+", raw_cols):
            col = col.strip().lower()
            if col and col not in _NOISE_TOKENS and col not in seen:
                columns.append(col)
                seen.add(col)
        return columns, has_include

    # Form 2: <trim> block with <if>colName,
    if "<trim>" in sql_text:
        # Extract column names from the first <trim> block (column names part)
        # The pattern: <if> colName,  before the VALUES <trim>
        parts = sql_text.split("<trim>")
        if len(parts) >= 2:
            col_part = parts[1]
            # Extract until we hit the values section (second <trim>)
            for m2 in _RE_TRIM_INSERT_COL.finditer(col_part):
                col = m2.group(1).strip().lower()
                if col and col not in _NOISE_TOKENS and col not in seen:
                    columns.append(col)
                    seen.add(col)

    return columns, has_include


def parse_select_fields(sql_text: str) -> List[str]:
    """Parse projected fields from SELECT SQL."""
    m = _RE_SELECT_FIELDS.search(sql_text)
    if not m:
        return ["*"]

    raw = m.group(1).strip()
    # Strip MyBatis tags
    cleaned = re.sub(r"<[^>]+>", " ", raw)

    if "*" in cleaned or not cleaned:
        return ["*"]

    # Split by comma, extract column names
    fields: List[str] = []
    seen: Set[str] = set()
    for part in cleaned.split(","):
        part = part.strip()
        if not part:
            continue
        # Handle "t1.col" -> "col", "col as alias" -> "col", "col alias" -> "col"
        # Remove table alias prefix
        if "." in part:
            part = part.rsplit(".", 1)[-1]
        # Remove alias
        part = re.split(r"\s+(?:as\s+)?", part, flags=re.IGNORECASE)[0].strip()
        part = part.strip("`\"[]").lower()
        if part and part not in _NOISE_TOKENS and part not in seen:
            fields.append(part)
            seen.add(part)

    return fields if fields else ["*"]


def describe_statement(statement_id: str, kind: str) -> str:
    """Generate a human-readable description from method name."""
    name = statement_id
    kind_lower = kind.lower() if kind else ""

    # Common patterns
    if name.startswith("selectByPk"):
        return "按主键查询"
    if name.startswith("selectByPkWithLock") or "WithLock" in name:
        return "按主键加锁查询"
    if name.startswith("selectByCondition"):
        return "按条件查询"
    if name.startswith("selectByConditionWithRowbounds"):
        return "按条件分页查询"
    if name.startswith("selectByIdx"):
        return "按索引查询"
    if name.startswith("selectOverdue"):
        return "查询逾期记录"
    if name.startswith("selectFirst") or name.startswith("selectOne"):
        return "查询单条记录"
    if name.startswith("selectAll"):
        return "查询全部"
    if name.startswith("selectRecent") or name.startswith("selectLatest"):
        return "查询最近记录"
    if name.startswith("select") and "By" in name:
        suffix = name.split("By", 1)[1]
        return f"按{suffix}查询"
    if name.startswith("select"):
        return "查询"

    if name.startswith("updateByPk"):
        return "按主键更新"
    if name.startswith("updateByPkSelective"):
        return "按主键选择性更新"
    if name.startswith("updateByCondition"):
        if "Selective" in name:
            return "按条件选择性更新"
        return "按条件更新"
    if name.startswith("update"):
        if "Selective" in name:
            return "选择性更新"
        if "Status" in name:
            return "更新状态"
        return "更新"

    if name.startswith("insertSelective"):
        return "选择性插入"
    if name.startswith("insert"):
        if "Batch" in name or "batch" in name:
            return "批量插入"
        return "插入"

    if name.startswith("deleteByPk"):
        return "按主键删除"
    if name.startswith("deleteByCondition"):
        return "按条件删除"
    if name.startswith("delete"):
        return "删除"

    if name.startswith("countByCondition"):
        return "按条件计数"
    if name.startswith("count"):
        return "计数"

    return f"{kind_lower}操作"


# ---------------------------------------------------------------------------
# Source code verification (three-tier fallback)
# ---------------------------------------------------------------------------

def _namespace_to_simple_name(namespace: str) -> str:
    """Extract simple class name from full namespace."""
    return namespace.rsplit(".", 1)[-1] if "." in namespace else namespace


def _find_xml_file(project_src_dir: str, namespace: str) -> Optional[str]:
    """Find MyBatis XML file by namespace."""
    simple = _namespace_to_simple_name(namespace)
    # Walk the project for matching XML files
    for root, dirs, files in os.walk(project_src_dir):
        for f in files:
            if f == f"{simple}.xml":
                full_path = os.path.join(root, f)
                # Quick check: file should contain the namespace
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read(8192)
                    if namespace in content:
                        return full_path
                except Exception:
                    pass
    # Fallback: find any XML file that references this namespace
    for root, dirs, files in os.walk(project_src_dir):
        for f in files:
            if f.endswith(".xml"):
                full_path = os.path.join(root, f)
                try:
                    with open(full_path, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read(8192)
                    if namespace in content:
                        return full_path
                except Exception:
                    pass
    return None


def _find_mapper_java(project_src_dir: str, namespace: str) -> Optional[str]:
    """Find Mapper Java interface file."""
    simple = _namespace_to_simple_name(namespace)
    for root, dirs, files in os.walk(project_src_dir):
        for f in files:
            if f == f"{simple}.java":
                return os.path.join(root, f)
    return None


def _parse_xml_statements(xml_path: str, namespace: str) -> Dict[str, Dict[str, Any]]:
    """Parse MyBatis XML file, extract all SQL statements.

    Returns {statementId: {"kind": "select|insert|update|delete",
                           "setFields": [...], "columns": [...],
                           "hasInclude": bool}}
    """
    results: Dict[str, Dict[str, Any]] = {}
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    except Exception:
        return results

    tag_to_kind = {
        "select": "select",
        "insert": "insert",
        "update": "update",
        "delete": "delete",
    }

    for tag_name, kind in tag_to_kind.items():
        for elem in root.findall(f".//{tag_name}"):
            sid = elem.get("id")
            if not sid:
                continue
            sql_text = "".join(elem.itertext())

            set_fields, has_inc = parse_update_set_fields(sql_text) if kind == "update" else ([], False)
            columns, _ = parse_insert_columns(sql_text) if kind == "insert" else ([], False)

            results[sid] = {
                "kind": kind,
                "setFields": set_fields,
                "columns": columns,
                "hasInclude": has_inc or "<include" in sql_text,
            }

    return results


def _parse_mapper_annotations(java_path: str) -> Dict[str, Dict[str, Any]]:
    """Parse Mapper Java interface for @Select/@Insert/@Update/@Delete annotations.

    Returns {statementId: {"kind": ..., "setFields": [...], "columns": [...]}}
    """
    results: Dict[str, Dict[str, Any]] = {}
    try:
        with open(java_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception:
        return results

    # Match annotation patterns like @Select("...") or @Select({"..."})
    ann_pattern = re.compile(
        r"@(Select|Insert|Update|Delete)\s*\(\s*(?:\{\s*)?\"([^\"]+)\"", re.IGNORECASE
    )
    # Match method name
    method_pattern = re.compile(r"\b(\w+)\s*\(")

    lines = content.split("\n")
    current_ann = None
    current_sql = ""
    current_kind = None

    for line in lines:
        m = ann_pattern.search(line)
        if m:
            current_kind = m.group(1).lower()
            current_sql = m.group(2)
            # Look for method name on same or next lines
            mm = method_pattern.search(line[m.end():])
            if mm:
                sid = mm.group(1)
                _add_annotation_result(results, sid, current_kind, current_sql)
                current_ann = None
            else:
                current_ann = "pending"
            continue

        if current_ann == "pending":
            mm = method_pattern.search(line)
            if mm and current_kind:
                sid = mm.group(1)
                _add_annotation_result(results, sid, current_kind, current_sql)
                current_ann = None

    return results


def _add_annotation_result(
    results: Dict[str, Dict[str, Any]], sid: str, kind: str, sql: str
):
    set_fields, has_inc = parse_update_set_fields(sql) if kind == "update" else ([], False)
    columns, _ = parse_insert_columns(sql) if kind == "insert" else ([], False)
    results[sid] = {
        "kind": kind,
        "setFields": set_fields,
        "columns": columns,
        "hasInclude": has_inc,
    }


def verify_statement_source(
    project_src_dir: str, namespace: str, statement_id: str,
    db_kind: str, db_set_fields: List[str], db_insert_cols: List[str],
) -> Tuple[str, Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Three-tier source verification.

    Returns (source_type, source_data, diff_record_or_None).
    source_type: "MYBATIS_XML" | "MYBATIS_ANNOTATION" | "INLINE_SQL" | "SOURCE_NOT_FOUND"
    """
    diff_record = None

    # Priority 1: MyBatis XML
    xml_path = _find_xml_file(project_src_dir, namespace)
    if xml_path:
        xml_stmts = _parse_xml_statements(xml_path, namespace)
        if statement_id in xml_stmts:
            src = xml_stmts[statement_id]
            diff_record = _compare_with_source(
                statement_id, os.path.basename(xml_path), "MYBATIS_XML",
                db_kind, db_set_fields, db_insert_cols, src,
            )
            return "MYBATIS_XML", src, diff_record

    # Priority 2: MyBatis Annotations
    java_path = _find_mapper_java(project_src_dir, namespace)
    if java_path:
        ann_stmts = _parse_mapper_annotations(java_path)
        if statement_id in ann_stmts:
            src = ann_stmts[statement_id]
            diff_record = _compare_with_source(
                statement_id, os.path.basename(java_path), "MYBATIS_ANNOTATION",
                db_kind, db_set_fields, db_insert_cols, src,
            )
            return "MYBATIS_ANNOTATION", src, diff_record

    # Priority 3: Inline SQL (JdbcTemplate/EntityManager scan)
    # This is a lightweight scan — full implementation would require deeper analysis
    simple_ns = _namespace_to_simple_name(namespace)
    for root, dirs, files in os.walk(project_src_dir):
        for f in files:
            if f.endswith(".java"):
                fpath = os.path.join(root, f)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as fh:
                        content = fh.read()
                except Exception:
                    continue
                if statement_id in content and (
                    "JdbcTemplate" in content or "EntityManager" in content
                ):
                    return "INLINE_SQL", None, None

    return "SOURCE_NOT_FOUND", None, None


def _compare_with_source(
    statement_id: str, source_file: str, source_type: str,
    db_kind: str, db_set_fields: List[str], db_insert_cols: List[str],
    source_data: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Compare DB-parsed fields with source-parsed fields, return diff if any."""
    _MARKER = "UNRESOLVED_INCLUDE"

    if db_kind == "update":
        src_fields = source_data.get("setFields", [])
        if src_fields and db_set_fields:
            # Filter out the marker token for comparison purposes
            db_set = set(db_set_fields) - {_MARKER}
            src_set = set(src_fields) - {_MARKER}
            if db_set != src_set:
                extra_in_src = sorted(src_set - db_set)
                extra_in_db = sorted(db_set - src_set)
                detail_parts = []
                if extra_in_src:
                    detail_parts.append(f"源中多出 {', '.join(extra_in_src)} 字段")
                if extra_in_db:
                    detail_parts.append(f"DB中多出 {', '.join(extra_in_db)} 字段")
                if detail_parts:
                    return {
                        "statementId": statement_id,
                        "source": source_file,
                        "sourceType": source_type,
                        "dbSetFields": sorted(f for f in db_set_fields if f != _MARKER),
                        "sourceSetFields": sorted(src_fields),
                        "detail": "; ".join(detail_parts),
                    }

    elif db_kind == "insert":
        src_cols = source_data.get("columns", [])
        if src_cols and db_insert_cols:
            db_set = set(db_insert_cols) - {_MARKER}
            src_set = set(src_cols) - {_MARKER}
            if db_set != src_set:
                extra_in_src = sorted(src_set - db_set)
                extra_in_db = sorted(db_set - src_set)
                detail_parts = []
                if extra_in_src:
                    detail_parts.append(f"源中多出 {', '.join(extra_in_src)} 字段")
                if extra_in_db:
                    detail_parts.append(f"DB中多出 {', '.join(extra_in_db)} 字段")
                if detail_parts:
                    return {
                        "statementId": statement_id,
                        "source": source_file,
                        "sourceType": source_type,
                        "dbInsertCols": sorted(f for f in db_insert_cols if f != _MARKER),
                        "sourceInsertCols": sorted(src_cols),
                        "detail": "; ".join(detail_parts),
                    }

    return None


# ---------------------------------------------------------------------------
# Main analysis logic
# ---------------------------------------------------------------------------

def extract_statements_from_db(db_path: str) -> List[Dict[str, Any]]:
    """Extract all mybatis-statement records from graph.db."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT ce.name AS statement_id,
                   json_extract(ce.properties_json, '$.statementKind') AS statement_kind,
                   json_extract(ce.properties_json, '$.sqlText') AS sql_text,
                   json_extract(ce.properties_json, '$.namespace') AS namespace
            FROM nodes ce
            WHERE ce.label = 'CodeElement'
              AND json_extract(ce.properties_json, '$.kind') = 'mybatis-statement'
            """
        )
        rows = cur.fetchall()
        return [
            {
                "statementId": r[0],
                "statementKind": r[1],
                "sqlText": r[2] or "",
                "namespace": r[3] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()


def _namespace_matches_table(
    namespace: str, table_name: str, mapper_namespaces: List[str]
) -> bool:
    """Check if a namespace belongs to a table (via Phase 0 registry)."""
    return namespace in mapper_namespaces


def build_table_operations(
    db_path: str,
    phase0_tables: List[Dict[str, Any]],
    project_src_dir: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build per-table CRUD operation details with source verification.

    Returns (tables_result, crud_diffs).
    """
    all_statements = extract_statements_from_db(db_path)

    # Index statements by namespace
    stmts_by_ns: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for stmt in all_statements:
        stmts_by_ns[stmt["namespace"]].append(stmt)

    tables_result: List[Dict[str, Any]] = []
    crud_diffs: List[Dict[str, Any]] = []

    for table_info in phase0_tables:
        table_name = table_info["tableName"]
        mapper_namespaces = table_info.get("mapperNamespaces", [])

        if not mapper_namespaces:
            tables_result.append({
                "tableName": table_name,
                "operations": {"select": [], "insert": [], "update": [], "delete": []},
                "sourceType": "NO_NAMESPACE",
            })
            continue

        # Collect all statements for this table's namespaces
        ops: Dict[str, List[Dict[str, Any]]] = {
            "select": [], "insert": [], "update": [], "delete": []
        }
        overall_source_type = "SOURCE_NOT_FOUND"
        seen_source_types: Set[str] = set()

        for ns in mapper_namespaces:
            ns_stmts = stmts_by_ns.get(ns, [])
            for stmt in ns_stmts:
                kind = (stmt["statementKind"] or "").lower()
                if kind not in ops:
                    continue

                sid = stmt["statementId"]
                sql = stmt["sqlText"]

                # Parse fields based on kind
                if kind == "update":
                    fields, has_inc = parse_update_set_fields(sql)
                    op_entry = {
                        "statementId": sid,
                        "namespace": ns,
                        "setFields": fields,
                        "description": describe_statement(sid, kind),
                    }
                    if has_inc:
                        op_entry["setFields"].append("UNRESOLVED_INCLUDE")

                    # Source verification
                    src_type, src_data, diff = verify_statement_source(
                        project_src_dir, ns, sid, kind, fields, [],
                    )
                    seen_source_types.add(src_type)
                    if diff:
                        crud_diffs.append(diff)

                elif kind == "insert":
                    cols, has_inc = parse_insert_columns(sql)
                    op_entry = {
                        "statementId": sid,
                        "namespace": ns,
                        "columns": cols,
                        "description": describe_statement(sid, kind),
                    }
                    if has_inc:
                        op_entry["columns"].append("UNRESOLVED_INCLUDE")

                    # Source verification
                    src_type, src_data, diff = verify_statement_source(
                        project_src_dir, ns, sid, kind, [], cols,
                    )
                    seen_source_types.add(src_type)
                    if diff:
                        crud_diffs.append(diff)

                elif kind == "select":
                    proj_fields = parse_select_fields(sql)
                    op_entry = {
                        "statementId": sid,
                        "namespace": ns,
                        "fields": proj_fields,
                        "description": describe_statement(sid, kind),
                    }
                    # Light source verification for SELECT
                    src_type, _, _ = verify_statement_source(
                        project_src_dir, ns, sid, kind, [], [],
                    )
                    seen_source_types.add(src_type)

                else:  # delete
                    op_entry = {
                        "statementId": sid,
                        "namespace": ns,
                        "description": describe_statement(sid, kind),
                    }
                    src_type, _, _ = verify_statement_source(
                        project_src_dir, ns, sid, kind, [], [],
                    )
                    seen_source_types.add(src_type)

                ops[kind].append(op_entry)

        # Determine overall source type (prefer XML over annotation over inline)
        if "MYBATIS_XML" in seen_source_types:
            overall_source_type = "MYBATIS_XML"
        elif "MYBATIS_ANNOTATION" in seen_source_types:
            overall_source_type = "MYBATIS_ANNOTATION"
        elif "INLINE_SQL" in seen_source_types:
            overall_source_type = "INLINE_SQL"
        elif seen_source_types:
            overall_source_type = "SOURCE_NOT_FOUND"

        tables_result.append({
            "tableName": table_name,
            "operations": ops,
            "sourceType": overall_source_type,
        })

    return tables_result, crud_diffs


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 2: CRUD operation analysis + source verification"
    )
    parser.add_argument("db_path", help="Path to graph.db")
    parser.add_argument("cache_dir", help="Cache directory (for I/O)")
    parser.add_argument("project_src_dir", help="Java project source root")
    args = parser.parse_args()

    db_path = args.db_path
    cache_dir = args.cache_dir
    project_src_dir = args.project_src_dir

    # Validate inputs
    if not os.path.isfile(db_path):
        print(f"ERROR: graph.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    phase0_path = os.path.join(cache_dir, "phase0-registry.json")
    if not os.path.isfile(phase0_path):
        print(f"ERROR: phase0-registry.json not found at {phase0_path}", file=sys.stderr)
        sys.exit(1)

    # Load Phase 0 registry
    with open(phase0_path, "r", encoding="utf-8") as f:
        phase0_data = json.load(f)

    phase0_tables = phase0_data.get("tables", [])
    print(f"Loaded Phase 0 registry: {len(phase0_tables)} tables")

    # Run analysis
    print("Extracting statements from graph.db...")
    tables_result, crud_diffs = build_table_operations(
        db_path, phase0_tables, project_src_dir
    )

    # Summary
    total_selects = sum(len(t["operations"].get("select", [])) for t in tables_result)
    total_updates = sum(len(t["operations"].get("update", [])) for t in tables_result)
    total_inserts = sum(len(t["operations"].get("insert", [])) for t in tables_result)
    total_deletes = sum(len(t["operations"].get("delete", [])) for t in tables_result)

    print(f"  SELECT:  {total_selects}")
    print(f"  INSERT:  {total_inserts}")
    print(f"  UPDATE:  {total_updates}")
    print(f"  DELETE:  {total_deletes}")
    print(f"  Diffs:   {len(crud_diffs)}")

    # Output
    output = {
        "phase": "crud_analysis",
        "totalTables": len(tables_result),
        "tables": tables_result,
        "crudDiffs": crud_diffs,
    }

    os.makedirs(cache_dir, exist_ok=True)
    output_path = os.path.join(cache_dir, "phase2-operations.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nOutput written to: {output_path}")


if __name__ == "__main__":
    main()
