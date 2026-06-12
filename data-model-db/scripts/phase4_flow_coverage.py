#!/usr/bin/env python3
"""
Phase 4: Flow Coverage Analysis

Cross-references trace cache chain files with Phase 0/1/2 data to determine
per-table flow coverage status (COVERED / PARTIAL / ORPHAN).

Usage:
    python3 phase4_flow_coverage.py <cache_dir> <trace_cache_dir>
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from glob import glob
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SKIP_FILES = {"bridges.json", "progress.json"}

_OP_KEY_MAP = {
    "SELECT": "select",
    "INSERT": "insert",
    "UPDATE": "update",
    "DELETE": "delete",
}


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def load_json(path: str) -> Optional[Dict[str, Any]]:
    """Load a JSON file, return None on failure."""
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_table_ops_from_phase2(phase2_data: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Extract per-table operation types from Phase 2 output.

    Returns {table_name: {"select", "insert", ...}} (lowercase op keys).
    """
    result: Dict[str, Set[str]] = {}
    for table_entry in phase2_data.get("tables", []):
        table_name = table_entry["tableName"]
        ops: Set[str] = set()
        for op_key in ("select", "insert", "update", "delete"):
            if table_entry.get("operations", {}).get(op_key):
                ops.add(op_key)
        result[table_name] = ops
    return result


def get_ownership_from_phase1(phase1_data: Dict[str, Any]) -> Dict[str, Set[str]]:
    """Extract per-table owner class names from Phase 1 output.

    Returns {table_name: {"SomeService", "SomeDao", ...}}
    """
    result: Dict[str, Set[str]] = {}
    for table_entry in phase1_data.get("tables", []):
        table_name = table_entry["tableName"]
        owners: Set[str] = set()
        ownership = table_entry.get("ownership") or {}
        for svc in ownership.get("services", []):
            owners.add(svc.get("className", ""))
        for dao in ownership.get("daos", []):
            owners.add(dao.get("className", ""))
        # Filter empty strings
        result[table_name] = {o for o in owners if o}
    return result


# ---------------------------------------------------------------------------
# Chain file scanning
# ---------------------------------------------------------------------------

def scan_trace_chains(trace_cache_dir: str) -> Dict[str, Dict[str, Any]]:
    """Scan trace cache for chain files, build table -> flow reverse index.

    Returns:
        table_flow_ops: {
            table_name: {
                "flows": [{"entryId": "...", "entryType": "...", "operations": ["SELECT", ...]}],
                "ops": {"SELECT", "INSERT", ...}
            }
        }
    """
    table_flow_ops: Dict[str, Dict[str, Any]] = {}

    if not os.path.isdir(trace_cache_dir):
        return table_flow_ops

    chain_files = glob(os.path.join(trace_cache_dir, "*.json"))
    chain_files = [
        f for f in chain_files
        if os.path.basename(f) not in _SKIP_FILES
    ]

    for chain_file in sorted(chain_files):
        try:
            with open(chain_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        entry_id = data.get("entryId", os.path.basename(chain_file))
        entry_type = data.get("entryType", "unknown")
        chain = data.get("chain", [])

        # Collect (table, operation) pairs for this entry, deduplicated
        entry_table_ops: Dict[str, Set[str]] = defaultdict(set)

        for node in chain:
            di = node.get("domainInteraction")
            if not di or not isinstance(di, dict):
                continue
            if di.get("type") != "DATABASE":
                continue

            table_name = di.get("table")
            operation = di.get("operation")
            if not table_name or not operation:
                continue

            entry_table_ops[table_name].add(operation.upper())

        # Merge into global reverse index
        for table_name, operations in entry_table_ops.items():
            if table_name not in table_flow_ops:
                table_flow_ops[table_name] = {
                    "flows": [],
                    "ops": set(),
                }
            table_flow_ops[table_name]["flows"].append({
                "entryId": entry_id,
                "entryType": entry_type,
                "operations": sorted(operations),
            })
            table_flow_ops[table_name]["ops"].update(operations)

    return table_flow_ops


def get_chain_classes(trace_cache_dir: str) -> Set[str]:
    """Extract all class names appearing in chain files for cross-validation."""
    classes: Set[str] = set()

    if not os.path.isdir(trace_cache_dir):
        return classes

    chain_files = glob(os.path.join(trace_cache_dir, "*.json"))
    chain_files = [
        f for f in chain_files
        if os.path.basename(f) not in _SKIP_FILES
    ]

    for chain_file in chain_files:
        try:
            with open(chain_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue

        for node in data.get("chain", []):
            cls = node.get("class")
            if cls:
                classes.add(cls)

    return classes


# ---------------------------------------------------------------------------
# Coverage determination
# ---------------------------------------------------------------------------

def determine_coverage(
    table_name: str,
    table_ops: Set[str],
    flow_ops: Set[str],
) -> Tuple[str, List[str]]:
    """Determine coverage status and list orphan operations.

    Returns (status, orphan_operations).
    - COVERED: all code operations are covered by at least one flow
    - PARTIAL: some operations covered
    - ORPHAN: no flows reference this table
    """
    if not flow_ops:
        return "ORPHAN", sorted(table_ops)

    # Map flow ops to lowercase for comparison
    flow_ops_lower = {_OP_KEY_MAP.get(op, op.lower()) for op in flow_ops}

    # Find operations in code but not covered by flows
    uncovered = table_ops - flow_ops_lower

    if not uncovered:
        return "COVERED", []
    elif uncovered != table_ops:
        return "PARTIAL", sorted(uncovered)
    else:
        return "PARTIAL", sorted(uncovered)


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def build_coverage_report(
    cache_dir: str,
    trace_cache_dir: str,
) -> Dict[str, Any]:
    """Build the full Phase 4 coverage report.

    Returns the output JSON structure.
    """
    # Load Phase 0 registry
    phase0_path = os.path.join(cache_dir, "phase0-registry.json")
    phase0_data = load_json(phase0_path)
    if not phase0_data:
        print(f"ERROR: phase0-registry.json not found at {phase0_path}", file=sys.stderr)
        sys.exit(1)

    phase0_tables = [t["tableName"] for t in phase0_data.get("tables", [])]
    print(f"Loaded Phase 0 registry: {len(phase0_tables)} tables")

    # Load Phase 2 operations
    phase2_path = os.path.join(cache_dir, "phase2-operations.json")
    phase2_data = load_json(phase2_path)
    if not phase2_data:
        print(f"ERROR: phase2-operations.json not found at {phase2_path}", file=sys.stderr)
        sys.exit(1)

    table_ops_map = get_table_ops_from_phase2(phase2_data)
    print(f"Loaded Phase 2 operations: {len(table_ops_map)} tables")

    # Load Phase 1 ownership (optional, for cross-validation)
    phase1_path = os.path.join(cache_dir, "phase1-ownership.json")
    phase1_data = load_json(phase1_path)
    ownership_map: Dict[str, Set[str]] = {}
    if phase1_data:
        ownership_map = get_ownership_from_phase1(phase1_data)
        print(f"Loaded Phase 1 ownership: {len(ownership_map)} tables")

    # Scan trace cache
    if not os.path.isdir(trace_cache_dir):
        print(f"WARNING: trace_cache_dir not found: {trace_cache_dir}", file=sys.stderr)
        print("All tables will be marked as ORPHAN.")

    table_flow_ops = scan_trace_chains(trace_cache_dir)
    chain_classes = get_chain_classes(trace_cache_dir)
    print(f"Scanned trace cache: {len(table_flow_ops)} tables referenced in flows")

    # Build coverage per table
    tables_result: List[Dict[str, Any]] = []

    for table_name in phase0_tables:
        table_ops = table_ops_map.get(table_name, set())
        flow_data = table_flow_ops.get(table_name)
        flow_ops = flow_data["ops"] if flow_data else set()
        flows = flow_data["flows"] if flow_data else []

        status, orphan_ops = determine_coverage(table_name, table_ops, flow_ops)

        # Cross-validate ownership: check if any owner class appears in chains
        ownership_match: List[str] = []
        if ownership_map and table_name in ownership_map:
            for owner_class in ownership_map[table_name]:
                if owner_class in chain_classes:
                    ownership_match.append(owner_class)

        table_entry: Dict[str, Any] = {
            "tableName": table_name,
            "flowCoverage": {
                "status": status,
                "flows": flows,
                "orphanOperations": orphan_ops,
            },
        }

        # Add ownership cross-validation if available
        if ownership_map and table_name in ownership_map:
            table_entry["ownershipCrossValidation"] = {
                "ownerClassesInFlows": sorted(ownership_match),
                "ownerClassesNotInFlows": sorted(
                    ownership_map[table_name] - set(ownership_match)
                ),
            }

        tables_result.append(table_entry)

    # Summary stats
    from collections import Counter
    status_counts = Counter(t["flowCoverage"]["status"] for t in tables_result)
    print(f"\nCoverage summary:")
    for status_val in ["COVERED", "PARTIAL", "ORPHAN"]:
        print(f"  {status_val}: {status_counts.get(status_val, 0)}")

    return {
        "phase": "flow_coverage",
        "totalTables": len(tables_result),
        "tablesReferencedInFlows": len(table_flow_ops),
        "tables": tables_result,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 4: Flow coverage analysis"
    )
    parser.add_argument("cache_dir", help="Cache directory with Phase 0/1/2 outputs")
    parser.add_argument("trace_cache_dir", help="Trace cache directory with chain JSON files")
    args = parser.parse_args()

    cache_dir = args.cache_dir
    trace_cache_dir = args.trace_cache_dir

    # Run analysis
    report = build_coverage_report(cache_dir, trace_cache_dir)

    # Write output
    os.makedirs(cache_dir, exist_ok=True)
    output_path = os.path.join(cache_dir, "phase4-coverage.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nOutput written to: {output_path}")


if __name__ == "__main__":
    main()
