#!/usr/bin/env python3
"""
Phase 0: Table Discovery + graph.db Alignment

Discovers database tables from:
  1. graph.db CodeElement nodes (kind=mybatis-statement)
  2. Optional user-provided table list file
  3. Optional db-schema.json for cross-reference

Outputs a unified table registry to <cache_dir>/phase0-registry.json
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# SQL table extraction
# ---------------------------------------------------------------------------

# Pre-compile regexes for performance
_RE_MYBATIS_TAGS = re.compile(r"<[^>]+>")  # strip <if>, <set>, <include>, <trim>, <foreach> etc.

# Table extraction patterns (applied AFTER stripping mybatis tags and lowering)
_RE_SELECT_FROM = re.compile(r"\bfrom\s+(\w+)")
_RE_JOIN = re.compile(r"\bjoin\s+(\w+)")
_RE_UPDATE = re.compile(r"\bupdate\s+(\w+)")
_RE_INSERT_INTO = re.compile(r"\binto\s+(\w+)")
_RE_DELETE_FROM = re.compile(r"\bdelete\s+from\s+(\w+)")

# Words that look like table names but are SQL keywords / pseudo-tables
_SQL_KEYWORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null",
    "set", "values", "into", "as", "on", "join", "left", "right", "inner",
    "outer", "cross", "natural", "using", "group", "by", "order", "having",
    "limit", "offset", "union", "all", "exists", "case", "when", "then",
    "else", "end", "like", "between", "asc", "desc", "distinct", "true",
    "false", "dual",
}


def _strip_mybatis_tags(sql: str) -> str:
    """Remove MyBatis dynamic tags like <if>, <set>, <include>, <trim>, <foreach>."""
    return _RE_MYBATIS_TAGS.sub(" ", sql)


def _extract_tables_from_sql(sql: str, statement_kind: str) -> Set[str]:
    """Extract table names from a single SQL statement."""
    cleaned = _strip_mybatis_tags(sql).lower()
    tables: Set[str] = set()

    kind = (statement_kind or "").lower()

    if kind == "select":
        for m in _RE_SELECT_FROM.finditer(cleaned):
            t = m.group(1)
            if t not in _SQL_KEYWORDS:
                tables.add(t)
        for m in _RE_JOIN.finditer(cleaned):
            t = m.group(1)
            if t not in _SQL_KEYWORDS:
                tables.add(t)
    elif kind == "update":
        for m in _RE_UPDATE.finditer(cleaned):
            t = m.group(1)
            if t not in _SQL_KEYWORDS:
                tables.add(t)
    elif kind == "insert":
        for m in _RE_INSERT_INTO.finditer(cleaned):
            t = m.group(1)
            if t not in _SQL_KEYWORDS:
                tables.add(t)
    elif kind == "delete":
        for m in _RE_DELETE_FROM.finditer(cleaned):
            t = m.group(1)
            if t not in _SQL_KEYWORDS:
                tables.add(t)

    return tables


# ---------------------------------------------------------------------------
# graph.db queries
# ---------------------------------------------------------------------------

_MYBATIS_STATEMENTS_SQL = """
SELECT
  ce.name AS statement_id,
  json_extract(ce.properties_json, '$.statementKind') AS statement_kind,
  json_extract(ce.properties_json, '$.sqlText') AS sql_text,
  json_extract(ce.properties_json, '$.namespace') AS namespace
FROM nodes ce
WHERE ce.label = 'CodeElement'
  AND json_extract(ce.properties_json, '$.kind') = 'mybatis-statement'
"""


def _query_mybatis_statements(db_path: str) -> List[dict]:
    """Query all mybatis-statement nodes from graph.db."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(_MYBATIS_STATEMENTS_SQL)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# User table list
# ---------------------------------------------------------------------------

def _load_user_tables(filepath: str) -> Set[str]:
    """Load table names from a text file (one per line)."""
    tables: Set[str] = set()
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            name = line.strip().lower()
            if name and not name.startswith("#"):
                tables.add(name)
    return tables


# ---------------------------------------------------------------------------
# db-schema.json cross-reference
# ---------------------------------------------------------------------------

def _load_db_schema_tables(filepath: str) -> Set[str]:
    """Extract table names from db-schema.json."""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    tables: Set[str] = set()
    for entry in data.get("tables", []):
        name = entry.get("name", "").strip().lower()
        if name:
            tables.add(name)
    return tables


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------

def _determine_source(table: str, user_tables: Set[str], db_tables: Set[str]) -> str:
    in_user = table in user_tables
    in_db = table in db_tables
    if in_user and in_db:
        return "USER_AND_DB"
    elif in_user:
        return "USER_ONLY"
    elif in_db:
        return "DB_ONLY"
    return "DB_ONLY"  # fallback


def _determine_coverage(statement_count: dict) -> str:
    has_select = statement_count.get("select", 0) > 0
    has_write = (
        statement_count.get("insert", 0) > 0
        or statement_count.get("update", 0) > 0
        or statement_count.get("delete", 0) > 0
    )
    if has_select and has_write:
        return "FULL"
    elif has_select or has_write:
        return "PARTIAL"
    else:
        return "ORPHAN"


def build_registry(
    db_path: str,
    user_tables: Set[str],
    db_schema_tables: Optional[Set[str]] = None,
) -> List[dict]:
    """
    Build the full table registry from graph.db + user list.

    Returns a sorted list of table entries.
    """
    statements = _query_mybatis_statements(db_path)

    # Per-table accumulators
    table_namespaces: Dict[str, Set[str]] = defaultdict(set)
    table_statement_counts: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"select": 0, "insert": 0, "update": 0, "delete": 0}
    )

    db_tables: Set[str] = set()

    for stmt in statements:
        sql_text = stmt.get("sql_text") or ""
        kind = (stmt.get("statement_kind") or "").lower()
        namespace = stmt.get("namespace") or ""

        tables_found = _extract_tables_from_sql(sql_text, kind)

        for t in tables_found:
            db_tables.add(t)
            table_namespaces[t].add(namespace)
            table_statement_counts[t][kind] += 1

    # Build unified set
    all_tables = db_tables | user_tables

    registry: List[dict] = []
    for table in sorted(all_tables):
        source = _determine_source(table, user_tables, db_tables)
        namespaces = sorted(table_namespaces.get(table, set()))
        counts = table_statement_counts.get(table, {"select": 0, "insert": 0, "update": 0, "delete": 0})
        coverage = _determine_coverage(counts)

        entry = {
            "tableName": table,
            "source": source,
            "mapperNamespaces": namespaces,
            "coverage": coverage,
            "statementCount": counts,
        }

        # Cross-reference with db-schema.json if provided
        if db_schema_tables is not None:
            entry["inDbSchema"] = table in db_schema_tables

        registry.append(entry)

    return registry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 0: Table discovery + graph.db alignment"
    )
    parser.add_argument("db_path", help="Path to graph.db SQLite file")
    parser.add_argument("cache_dir", help="Output directory for phase0-registry.json")
    parser.add_argument(
        "--tables",
        help="Path to user table list file (one table name per line)",
        default=None,
    )
    parser.add_argument(
        "--db-schema",
        help="Path to db-schema.json for cross-reference",
        default=None,
    )
    args = parser.parse_args()

    db_path = args.db_path
    cache_dir = args.cache_dir

    # Validate inputs
    if not os.path.isfile(db_path):
        print(f"ERROR: graph.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    # Load user table list (optional)
    user_tables: Set[str] = set()
    if args.tables:
        if not os.path.isfile(args.tables):
            print(f"ERROR: table list file not found at {args.tables}", file=sys.stderr)
            sys.exit(1)
        user_tables = _load_user_tables(args.tables)
        print(f"Loaded {len(user_tables)} tables from user list")

    # Load db-schema.json (optional)
    db_schema_tables: Optional[Set[str]] = None
    if args.db_schema:
        if not os.path.isfile(args.db_schema):
            print(f"ERROR: db-schema.json not found at {args.db_schema}", file=sys.stderr)
            sys.exit(1)
        db_schema_tables = _load_db_schema_tables(args.db_schema)
        print(f"Loaded {len(db_schema_tables)} tables from db-schema.json")

    # Build registry
    registry = build_registry(db_path, user_tables, db_schema_tables)

    # Output
    os.makedirs(cache_dir, exist_ok=True)
    output_path = os.path.join(cache_dir, "phase0-registry.json")

    output = {
        "phase": "discovery",
        "totalTables": len(registry),
        "tables": registry,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Summary
    sources = defaultdict(int)
    coverages = defaultdict(int)
    for t in registry:
        sources[t["source"]] += 1
        coverages[t["coverage"]] += 1

    print(f"\n=== Phase 0 Discovery Summary ===")
    print(f"Total tables: {len(registry)}")
    print(f"Sources: {dict(sources)}")
    print(f"Coverage: {dict(coverages)}")
    if db_schema_tables is not None:
        in_schema = sum(1 for t in registry if t.get("inDbSchema"))
        print(f"In db-schema.json: {in_schema}")
    print(f"\nOutput: {output_path}")


if __name__ == "__main__":
    main()
