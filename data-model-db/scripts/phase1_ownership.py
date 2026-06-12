#!/usr/bin/env python3
"""
Phase 1: Ownership Chain Construction

For each table from Phase 0's registry, builds the ownership chain:
  Table <- Mapper <- [Dao] <- Service

Steps:
  1. Read Phase 0 registry to get table->mapperNamespace mapping
  2. Query graph.db HAS_PROPERTY to find classes holding Mapper types
  3. Query graph.db HAS_PROPERTY to find classes holding Dao types (Service layer)
  4. Build ownership chain: Table -> Mapper -> Dao -> Service
  5. Verify against Java source code, record mismatches

Outputs: <cache_dir>/phase1-ownership.json
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
# Constants
# ---------------------------------------------------------------------------

_RE_FIELD_DECL = re.compile(r"private\s+(\w+)\s+(\w+)\s*;")

# Module extraction: from file_path like "cbrc-bs/src/main/java/..." -> "cbrc-bs"
_RE_MODULE = re.compile(r"^([^/]+)/")


# ---------------------------------------------------------------------------
# graph.db queries
# ---------------------------------------------------------------------------

_SQL_FIND_MAPPER_HOLDERS = """
SELECT c.name AS owner_class, c.file_path,
       p.name AS prop_name,
       json_extract(p.properties_json, '$.declaredType') AS mapper_type
FROM relationships r
JOIN nodes c ON c.id = r.source_id AND c.label = 'Class'
JOIN nodes p ON p.id = r.target_id AND p.label = 'Property'
WHERE r.type = 'HAS_PROPERTY'
  AND json_extract(p.properties_json, '$.declaredType') = ?
  AND c.file_path NOT LIKE '%/test/%'
"""

_SQL_FIND_DAO_HOLDERS = """
SELECT c.name AS owner_class, c.file_path,
       p.name AS prop_name,
       json_extract(p.properties_json, '$.declaredType') AS dao_type
FROM relationships r
JOIN nodes c ON c.id = r.source_id AND c.label = 'Class'
JOIN nodes p ON p.id = r.target_id AND p.label = 'Property'
WHERE r.type = 'HAS_PROPERTY'
  AND json_extract(p.properties_json, '$.declaredType') LIKE '%Dao'
  AND c.file_path NOT LIKE '%/test/%'
"""


def _query_mapper_holders(db_path: str, mapper_class_name: str) -> List[dict]:
    """Find classes that declare a property of the given Mapper type."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(_SQL_FIND_MAPPER_HOLDERS, (mapper_class_name,))
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _query_all_dao_holders(db_path: str) -> Dict[str, List[dict]]:
    """
    Find all classes that declare Dao-typed properties.
    Returns: {dao_type_simple_name: [{owner_class, file_path, prop_name, dao_type}, ...]}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(_SQL_FIND_DAO_HOLDERS)
        result = defaultdict(list)
        for row in cur.fetchall():
            d = dict(row)
            dao_type = d["dao_type"]
            if dao_type:
                result[dao_type].append(d)
        return dict(result)
    finally:
        conn.close()


def _query_all_mapper_to_dao(db_path: str) -> Dict[str, List[dict]]:
    """
    Build mapping: Mapper class name -> list of Dao classes that hold it.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Find all properties with Mapper declaredType
        cur = conn.execute("""
            SELECT c.name AS owner_class, c.file_path,
                   p.name AS prop_name,
                   json_extract(p.properties_json, '$.declaredType') AS mapper_type
            FROM relationships r
            JOIN nodes c ON c.id = r.source_id AND c.label = 'Class'
            JOIN nodes p ON p.id = r.target_id AND p.label = 'Property'
            WHERE r.type = 'HAS_PROPERTY'
              AND json_extract(p.properties_json, '$.declaredType') LIKE '%Mapper'
              AND c.file_path NOT LIKE '%/test/%'
        """)
        result = defaultdict(list)
        for row in cur.fetchall():
            d = dict(row)
            mapper_type = d["mapper_type"]
            if mapper_type:
                result[mapper_type].append(d)
        return dict(result)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Module extraction
# ---------------------------------------------------------------------------

def _extract_module(file_path: str) -> str:
    """Extract module name from file_path. E.g. 'cbrc-bs/src/main/...' -> 'cbrc-bs'."""
    if not file_path:
        return ""
    m = _RE_MODULE.match(file_path)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Source code verification
# ---------------------------------------------------------------------------

def _read_file_safe(filepath: str) -> Optional[str]:
    """Read file content safely, return None if not found."""
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except (FileNotFoundError, IOError):
        return None


def _extract_field_declarations(source: str) -> Dict[str, str]:
    """
    Extract field declarations from Java source.
    Returns: {field_name: declared_type}
    """
    fields = {}
    for m in _RE_FIELD_DECL.finditer(source):
        declared_type = m.group(1)
        field_name = m.group(2)
        fields[field_name] = declared_type
    return fields


def _verify_chain_against_source(
    project_src_dir: str,
    chain: dict,
) -> List[dict]:
    """
    Verify ownership chain by reading actual Java source files.
    Returns list of diff entries.
    """
    diffs = []

    # Verify Service -> Dao field declarations
    for svc in chain.get("services", []):
        svc_file = svc.get("file", "")
        if not svc_file:
            continue
        full_path = os.path.join(project_src_dir, svc_file)
        source = _read_file_safe(full_path)
        if source is None:
            continue

        fields = _extract_field_declarations(source)
        for via_field in svc.get("via", []):
            if via_field in fields:
                actual_type = fields[via_field]
                # Check if the Dao class name matches
                # The service's via field should reference a Dao type
                # We need to cross-check with the dao entries
                dao_names = [d["className"] for d in chain.get("daos", [])]
                if actual_type not in dao_names:
                    diffs.append({
                        "chain": f"{svc['className']} -> {via_field}",
                        "expectedType": ", ".join(dao_names) if dao_names else "N/A",
                        "sourceActual": actual_type,
                        "sourceFile": full_path,
                        "detail": f"Service declares field '{via_field}' as {actual_type}, expected one of: {dao_names}"
                    })

    # Verify Dao -> Mapper field declarations
    for dao in chain.get("daos", []):
        dao_file = dao.get("file", "")
        if not dao_file:
            continue
        full_path = os.path.join(project_src_dir, dao_file)
        source = _read_file_safe(full_path)
        if source is None:
            continue

        fields = _extract_field_declarations(source)
        for expected_mapper in dao.get("mappers", []):
            found = False
            for field_name, field_type in fields.items():
                if field_type == expected_mapper:
                    found = True
                    break
            if not found:
                diffs.append({
                    "chain": f"{dao['className']} -> mapper",
                    "expectedType": expected_mapper,
                    "sourceActual": "NOT_FOUND",
                    "sourceFile": full_path,
                    "detail": f"Dao '{dao['className']}' does not declare field of type '{expected_mapper}'. Found fields: {dict(list(fields.items())[:10])}"
                })

    return diffs


# ---------------------------------------------------------------------------
# Chain builder
# ---------------------------------------------------------------------------

def build_ownership_chains(
    db_path: str,
    registry_tables: List[dict],
    project_src_dir: str,
) -> Tuple[List[dict], List[dict]]:
    """
    Build ownership chains for all tables.

    Returns: (table_entries, ownership_diffs)
    """
    # Pre-load: Mapper -> Dao mappings
    mapper_to_daos = _query_all_mapper_to_dao(db_path)

    # Pre-load: Dao -> Service/other holder mappings
    dao_to_holders = _query_all_dao_holders(db_path)

    table_entries = []
    all_diffs = []

    for table_info in registry_tables:
        table_name = table_info["tableName"]
        namespaces = table_info.get("mapperNamespaces", [])

        # Extract mapper class names
        mapper_class_names = []
        for ns in namespaces:
            parts = ns.split(".")
            if parts:
                mapper_class_names.append(parts[-1])

        if not mapper_class_names:
            table_entries.append({
                "tableName": table_name,
                "ownership": None,
            })
            continue

        # Build chain: Mapper -> Dao
        daos = {}  # keyed by class name to deduplicate
        for mapper_name in mapper_class_names:
            dao_holders = mapper_to_daos.get(mapper_name, [])
            for holder in dao_holders:
                dao_class = holder["owner_class"]
                if dao_class not in daos:
                    daos[dao_class] = {
                        "className": dao_class,
                        "module": _extract_module(holder["file_path"]),
                        "file": holder["file_path"],
                        "mappers": [],
                        "_via_fields": [],
                    }
                if mapper_name not in daos[dao_class]["mappers"]:
                    daos[dao_class]["mappers"].append(mapper_name)
                daos[dao_class]["_via_fields"].append(holder["prop_name"])

        # Build chain: Dao -> Service
        services = {}  # keyed by class name to deduplicate
        for dao_name, dao_info in daos.items():
            # Look for classes that hold this Dao type
            holders = dao_to_holders.get(dao_name, [])
            for holder in holders:
                svc_class = holder["owner_class"]
                if svc_class not in services:
                    services[svc_class] = {
                        "className": svc_class,
                        "module": _extract_module(holder["file_path"]),
                        "file": holder["file_path"],
                        "via": [],
                    }
                via_field = holder["prop_name"]
                if via_field not in services[svc_class]["via"]:
                    services[svc_class]["via"].append(via_field)

        # Also check: if no Dao layer found, Mapper might be held directly by Service/other
        if not daos:
            for mapper_name in mapper_class_names:
                direct_holders = _query_mapper_holders(db_path, mapper_name)
                for holder in direct_holders:
                    holder_class = holder["owner_class"]
                    # Skip if the holder is itself a Mapper or test class
                    if holder_class == mapper_name:
                        continue
                    # Treat as direct owner (could be Service, Processor, etc.)
                    if holder_class not in services:
                        services[holder_class] = {
                            "className": holder_class,
                            "module": _extract_module(holder["file_path"]),
                            "file": holder["file_path"],
                            "via": [],
                        }
                    via_field = holder["prop_name"]
                    if via_field not in services[holder_class]["via"]:
                        services[holder_class]["via"].append(via_field)

        ownership = {}
        if services:
            ownership["services"] = list(services.values())
        if daos:
            ownership["daos"] = [
                {k: v for k, v in d.items() if not k.startswith("_")}
                for d in daos.values()
            ]

        if not ownership:
            table_entries.append({
                "tableName": table_name,
                "ownership": None,
            })
            continue

        # Build chain entry
        chain_entry = {
            "tableName": table_name,
            "ownership": ownership,
        }

        # Source code verification
        diffs = _verify_chain_against_source(project_src_dir, ownership)
        all_diffs.extend(diffs)

        table_entries.append(chain_entry)

    return table_entries, all_diffs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 1: Ownership chain construction"
    )
    parser.add_argument("db_path", help="Path to graph.db SQLite file")
    parser.add_argument("cache_dir", help="Directory containing phase0-registry.json; output written here")
    parser.add_argument("project_src_dir", help="Root directory of the Java project source code")
    args = parser.parse_args()

    db_path = args.db_path
    cache_dir = args.cache_dir
    project_src_dir = args.project_src_dir

    # Validate inputs
    if not os.path.isfile(db_path):
        print(f"ERROR: graph.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    registry_path = os.path.join(cache_dir, "phase0-registry.json")
    if not os.path.isfile(registry_path):
        print(f"ERROR: phase0-registry.json not found at {registry_path}", file=sys.stderr)
        sys.exit(1)

    if not os.path.isdir(project_src_dir):
        print(f"ERROR: project source directory not found at {project_src_dir}", file=sys.stderr)
        sys.exit(1)

    # Load Phase 0 registry
    with open(registry_path, "r", encoding="utf-8") as f:
        phase0 = json.load(f)

    registry_tables = phase0.get("tables", [])
    print(f"Loaded {len(registry_tables)} tables from Phase 0 registry")

    # Build ownership chains
    table_entries, diffs = build_ownership_chains(db_path, registry_tables, project_src_dir)

    # Output
    os.makedirs(cache_dir, exist_ok=True)
    output_path = os.path.join(cache_dir, "phase1-ownership.json")

    output = {
        "phase": "ownership",
        "totalTables": len(table_entries),
        "tablesWithOwnership": sum(1 for t in table_entries if t.get("ownership")),
        "tables": table_entries,
        "ownershipDiffs": diffs,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # Summary
    with_ownership = output["tablesWithOwnership"]
    without_ownership = len(table_entries) - with_ownership

    print(f"\n=== Phase 1 Ownership Summary ===")
    print(f"Total tables: {len(table_entries)}")
    print(f"With ownership chain: {with_ownership}")
    print(f"Without ownership chain: {without_ownership}")
    print(f"Ownership diffs (graph.db vs source): {len(diffs)}")

    # Show some examples
    print(f"\n--- Sample ownership chains ---")
    shown = 0
    for t in table_entries:
        if t.get("ownership") and shown < 5:
            svc_names = [s["className"] for s in t["ownership"].get("services", [])]
            dao_names = [d["className"] for d in t["ownership"].get("daos", [])]
            print(f"  {t['tableName']}:")
            if dao_names:
                print(f"    Dao: {', '.join(dao_names)}")
            if svc_names:
                print(f"    Service: {', '.join(svc_names)}")
            shown += 1

    if diffs:
        print(f"\n--- Ownership Diffs (first 5) ---")
        for d in diffs[:5]:
            print(f"  {d['chain']}: expected={d['expectedType']}, actual={d['sourceActual']}")

    print(f"\nOutput: {output_path}")


if __name__ == "__main__":
    main()
