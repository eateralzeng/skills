#!/usr/bin/env python3
"""
Phase 3: State Transition Inference + Source Code Verification

Reads Phase 2's phase2-operations.json to find status-like fields in UPDATE
setFields, matches them to Enum classes (deterministic via declaredType, or
speculative via fuzzy name matching), reads enum source to extract values,
and optionally scans trace cache for transition triggers.

Usage:
    python3 phase3_state_inference.py <db_path> <cache_dir> <project_src_dir> \
                                      [--trace-cache <trace_cache_dir>]
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STATUS_PATTERNS = re.compile(
    r"(status|state|flag|step)", re.IGNORECASE
)

_BASIC_TYPES = frozenset({
    "String", "Integer", "int", "Long", "long", "Boolean", "boolean",
    "Byte", "byte", "Short", "short", "Float", "float", "Double", "double",
    "Character", "char", "BigDecimal", "Date", "LocalDate", "LocalDateTime",
})

_MARKER = "UNRESOLVED_INCLUDE"

# Java enum constant pattern: handles both simple and complex enums
# Handles: NAME("val", "desc"), or NAME("val"), // comment, or NAME; // comment
_RE_ENUM_CONST = re.compile(
    r"(?:@\w+(?:\([^)]*\))?\s+)*"  # optional annotations like @SerializedName("Y")
    r"([A-Z][A-Z0-9_]*)\s*"
    r"(?:\(([^)]*)\))?\s*"          # constructor args (optional)
    r"[,;]?\s*"                      # optional comma or semicolon
    r"(?://\s*(.*))?$",              # trailing comment (optional)
    re.MULTILINE,
)

# Java inner class constant pattern: public static final String NAME = "value"; // desc
_RE_INNER_CONST = re.compile(
    r'public\s+static\s+final\s+String\s+([A-Z][A-Z0-9_]*)\s*=\s*"([^"]*)"\s*;'
    r"\s*(?://\s*(.*))?$",
    re.MULTILINE,
)

# Map field name pattern for STATUS_DESC_MAP: put("value", "desc")
_RE_MAP_PUT = re.compile(
    r'\.put\s*\(\s*(?:[A-Z_]+|"[^"]*")\s*,\s*"([^"]*)"\s*\)',
)


# ---------------------------------------------------------------------------
# CamelCase splitting for fuzzy matching
# ---------------------------------------------------------------------------

def camel_split(name: str) -> Set[str]:
    """Split a camelCase / snake_case name into lower-case tokens."""
    parts = name.replace("-", "_").split("_")
    tokens: Set[str] = set()
    for p in parts:
        for t in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", p):
            tokens.add(t.lower())
    return tokens


def match_score(field_name: str, enum_class: str) -> float:
    """Compute fuzzy match score between a field name and an enum class name."""
    ft = camel_split(field_name)
    et = camel_split(
        enum_class.replace("Enum", "").replace("Type", "").replace("Status", "")
    )
    if not ft or not et:
        return 0.0
    return len(ft & et) / max(len(ft), len(et))


# ---------------------------------------------------------------------------
# Graph DB queries
# ---------------------------------------------------------------------------

def query_enum_nodes(db_path: str) -> List[Dict[str, Any]]:
    """Query all Enum nodes from graph.db."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT n.name, n.file_path,
                   json_extract(n.properties_json, '$.constants') AS constants
            FROM nodes n
            WHERE n.label = 'Enum'
            """
        )
        rows = cur.fetchall()
        return [
            {"name": r[0], "filePath": r[1], "constants": r[2] or ""}
            for r in rows
        ]
    finally:
        conn.close()


def query_entity_property_types(db_path: str) -> Dict[str, Dict[str, str]]:
    """Query entity class properties with their declared types.

    Returns: {(tableName, fieldName): declaredType}
    """
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            """
            SELECT json_extract(c.properties_json, '$.tableName') AS tbl,
                   p.name AS field_name,
                   json_extract(p.properties_json, '$.declaredType') AS declared_type
            FROM relationships r
            JOIN nodes c ON c.id = r.source_id AND c.label = 'Class'
            JOIN nodes p ON p.id = r.target_id AND p.label = 'Property'
            WHERE r.type = 'HAS_PROPERTY'
              AND json_extract(c.properties_json, '$.tableName') IS NOT NULL
              AND json_extract(p.properties_json, '$.declaredType') IS NOT NULL
            """
        )
        result: Dict[str, Dict[str, str]] = {}
        for r in cur.fetchall():
            tbl = r[0]
            field = r[1]
            dtype = r[2]
            if tbl not in result:
                result[tbl] = {}
            result[tbl][field] = dtype
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Enum source file parsing
# ---------------------------------------------------------------------------

def _snake_to_camel(snake: str) -> str:
    """Convert snake_case to CamelCase for file name matching."""
    return "".join(part.capitalize() for part in snake.split("_"))


def _find_enum_file(project_src_dir: str, enum_name: str) -> Optional[str]:
    """Find enum Java source file in the project."""
    # Direct file search
    for root, dirs, files in os.walk(project_src_dir):
        for f in files:
            if f == f"{enum_name}.java":
                return os.path.join(root, f)
    # Camel case fallback
    camel_name = _snake_to_camel(enum_name)
    for root, dirs, files in os.walk(project_src_dir):
        for f in files:
            if f == f"{camel_name}.java":
                return os.path.join(root, f)
    return None


def parse_enum_source(file_path: str) -> List[Dict[str, str]]:
    """Parse a Java enum source file to extract constant values.

    Handles:
      - Standard enum: CONSTANT("value", "desc")
      - Standard enum: CONSTANT("value") // desc
      - Standard enum: CONSTANT  // desc
      - Inner class constants: public static final String NAME = "value"; // desc
    """
    if not file_path or not os.path.isfile(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception:
        return []

    values: List[Dict[str, str]] = []
    seen_names: Set[str] = set()

    # Strategy 1: Standard enum constants
    for m in _RE_ENUM_CONST.finditer(content):
        name = m.group(1)
        if name in seen_names:
            continue
        # Skip common non-constant names
        if name in ("STATUS_DESC_MAP", "VALUE", "ENUM", "SERIAL_VERSION_UID"):
            continue
        args_str = m.group(2) or ""
        comment = (m.group(3) or "").strip()

        # Parse constructor args to find the value
        value = name  # default: the constant name itself
        desc = comment

        if args_str:
            # Split by comma, respecting nested parens
            args = _split_constructor_args(args_str)
            if args:
                # First arg is typically the value
                raw_val = args[0].strip()
                value = raw_val.strip('"').strip("'")
                # Second arg is typically description
                if len(args) > 1:
                    raw_desc = args[1].strip().strip('"').strip("'")
                    if raw_desc and not desc:
                        desc = raw_desc

        seen_names.add(name)
        values.append({"name": name, "value": value, "description": desc})

    # Strategy 2: Inner class static final String constants
    # (for classes like Status.java that use inner classes)
    if not values:
        for m in _RE_INNER_CONST.finditer(content):
            name = m.group(1)
            if name in seen_names:
                continue
            value = m.group(2)
            desc = (m.group(3) or "").strip()
            seen_names.add(name)
            values.append({"name": name, "value": value, "description": desc})

    return values


def _split_constructor_args(args_str: str) -> List[str]:
    """Split constructor arguments, handling nested structures."""
    args: List[str] = []
    depth = 0
    current = ""
    for ch in args_str:
        if ch == "(":
            depth += 1
            current += ch
        elif ch == ")":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            args.append(current)
            current = ""
        else:
            current += ch
    if current.strip():
        args.append(current)
    return args


def parse_status_inner_class(file_path: str, inner_class_name: str) -> List[Dict[str, str]]:
    """Parse a specific inner class from a Status-like Java file.

    The inner_class_name like 'RequestInfoStatus' maps to a block of
    `public static final String NAME = "value"; // desc` declarations.
    """
    if not file_path or not os.path.isfile(file_path):
        return []

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception:
        return []

    # Find the inner class block
    # Pattern: public static final class InnerClassName { ... }
    pattern = re.compile(
        r"(?:public\s+)?(?:static\s+)?(?:final\s+)?class\s+"
        + re.escape(inner_class_name)
        + r"\s*\{",
        re.IGNORECASE,
    )
    m = pattern.search(content)
    if not m:
        return []

    # Find the matching closing brace
    start = m.end()
    depth = 1
    pos = start
    while pos < len(content) and depth > 0:
        if content[pos] == "{":
            depth += 1
        elif content[pos] == "}":
            depth -= 1
        pos += 1

    block = content[start : pos - 1]
    values: List[Dict[str, str]] = []
    seen: Set[str] = set()

    for cm in _RE_INNER_CONST.finditer(block):
        name = cm.group(1)
        if name in seen:
            continue
        value = cm.group(2)
        desc = (cm.group(3) or "").strip()
        seen.add(name)
        values.append({"name": name, "value": value, "description": desc})

    return values


# ---------------------------------------------------------------------------
# Trace cache scanning for transition triggers
# ---------------------------------------------------------------------------

def scan_trace_cache(
    trace_cache_dir: str,
    table_status_fields: Dict[str, List[str]],
) -> Dict[Tuple[str, str], List[Dict[str, str]]]:
    """Scan trace cache JSON files for handlers that trigger status field updates.

    Returns: {(tableName, field): [{"from": ..., "to": ..., "trigger": ...}]}
    """
    transitions: Dict[Tuple[str, str], List[Dict[str, str]]] = defaultdict(list)

    if not os.path.isdir(trace_cache_dir):
        return transitions

    # Build a set of status-related method names from the tables' mapper namespaces
    status_method_patterns = set()
    for tbl, fields in table_status_fields.items():
        for field in fields:
            # Common patterns: updateStatus, updateByStatus, updateXxxStatus
            status_method_patterns.add(field.lower())

    for fname in sorted(os.listdir(trace_cache_dir)):
        if not fname.endswith(".json") or fname == "bridges.json":
            continue
        fpath = os.path.join(trace_cache_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue

        chain = data.get("chain", [])
        entry_id = data.get("entryId", "")
        entry_type = data.get("entryType", "")

        # Look for update/setStatus methods in the chain
        for entry in chain:
            method = entry.get("method", "")
            cls = entry.get("class", "")
            method_lower = method.lower()

            # Check if this method updates a status-like field
            matched_field = None
            for tbl, fields in table_status_fields.items():
                for field in fields:
                    field_lower = field.lower()
                    # Match patterns like updateStatus, updateFeedbackStatus, etc.
                    if field_lower in method_lower or _field_in_method(field_lower, method_lower):
                        matched_field = (tbl, field)
                        break
                if matched_field:
                    break

            if matched_field:
                # Find the trigger (entry handler or job at layer 1-2)
                trigger = _find_trigger_for_entry(chain, entry)
                if trigger:
                    transitions[matched_field].append({
                        "trigger": trigger,
                        "source": fname,
                        "method": f"{cls}.{method}",
                        "context": entry.get("description", ""),
                    })

    return transitions


def _field_in_method(field_lower: str, method_lower: str) -> bool:
    """Check if a field name is meaningfully referenced in a method name."""
    # Convert field name variations
    field_camel = "".join(part.capitalize() for part in field_lower.split("_"))
    return field_camel.lower() in method_lower


def _find_trigger_for_entry(chain: List[Dict], target_entry: Dict) -> Optional[str]:
    """Find the trigger (entry handler/service) for a chain entry."""
    target_layer = target_entry.get("layer", 99)
    # Walk backwards to find the closest L1 or L2 entry
    for entry in reversed(chain):
        layer = entry.get("layer", 99)
        if layer <= 2 and layer < target_layer:
            cls = entry.get("class", "")
            method = entry.get("method", "")
            return f"{cls}.{method}" if method else cls
    # Fallback: use entry itself
    cls = target_entry.get("class", "")
    return cls


# ---------------------------------------------------------------------------
# Core state inference logic
# ---------------------------------------------------------------------------

def identify_status_fields(
    phase2_data: Dict[str, Any],
) -> Dict[str, List[str]]:
    """Identify status-like fields from UPDATE setFields per table.

    Returns: {tableName: [field1, field2, ...]}
    """
    result: Dict[str, List[str]] = {}

    for table in phase2_data.get("tables", []):
        table_name = table["tableName"]
        updates = table.get("operations", {}).get("update", [])
        if not updates:
            continue

        # Collect all setFields across update statements
        all_fields: Set[str] = set()
        for upd in updates:
            for field in upd.get("setFields", []):
                if field != _MARKER and _STATUS_PATTERNS.search(field):
                    all_fields.add(field)

        if all_fields:
            result[table_name] = sorted(all_fields)

    return result


def match_enums_to_fields(
    status_fields: Dict[str, List[str]],
    entity_property_types: Dict[str, Dict[str, str]],
    enum_nodes: List[Dict[str, Any]],
    project_src_dir: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Match status fields to enum classes.

    Returns: (state_transitions_per_table, state_diffs)
    """
    # Build enum lookup
    enum_by_name: Dict[str, Dict[str, Any]] = {}
    for en in enum_nodes:
        enum_by_name[en["name"]] = en

    tables_result: List[Dict[str, Any]] = []
    state_diffs: List[Dict[str, Any]] = []

    for table_name, fields in sorted(status_fields.items()):
        table_entry: Dict[str, Any] = {
            "tableName": table_name,
            "stateTransitions": [],
        }

        for field in fields:
            match = _match_field_to_enum(
                field, table_name, entity_property_types,
                enum_by_name, project_src_dir,
            )
            if match:
                table_entry["stateTransitions"].append(match)

                # Check for diffs
                if match.get("sourceValues") and match.get("values"):
                    db_names = {v["name"] for v in match["values"]}
                    src_names = {v["name"] for v in match["sourceValues"]}
                    if db_names != src_names:
                        state_diffs.append({
                            "table": table_name,
                            "field": field,
                            "enumClass": match.get("enumClass"),
                            "enumFile": match.get("enumFile"),
                            "graphDbValues": sorted(db_names),
                            "sourceValues": sorted(src_names),
                            "extraInDb": sorted(db_names - src_names),
                            "extraInSource": sorted(src_names - db_names),
                        })

                # Clean up internal field
                match.pop("sourceValues", None)

        if table_entry["stateTransitions"]:
            tables_result.append(table_entry)

    return tables_result, state_diffs


def _match_field_to_enum(
    field: str,
    table_name: str,
    entity_property_types: Dict[str, Dict[str, str]],
    enum_by_name: Dict[str, Dict[str, Any]],
    project_src_dir: str,
) -> Optional[Dict[str, Any]]:
    """Try to match a single field to an enum class."""
    # Look up declared type from entity properties
    # First try camelCase field name, then snake_case
    field_variants = _field_name_variants(field)
    declared_type = None

    tbl_props = entity_property_types.get(table_name, {})
    for variant in field_variants:
        if variant in tbl_props:
            declared_type = tbl_props[variant]
            break

    # Priority 1: Deterministic match via non-basic declaredType
    if declared_type and declared_type not in _BASIC_TYPES:
        # Check if declaredType matches an Enum node exactly
        if declared_type in enum_by_name:
            enum_info = enum_by_name[declared_type]
            return _build_enum_match(
                field, declared_type, enum_info, project_src_dir,
                "DETERMINISTIC", "HIGH",
            )

        # Check if it's a case-insensitive match
        for ename, einfo in enum_by_name.items():
            if ename.lower() == declared_type.lower():
                return _build_enum_match(
                    field, ename, einfo, project_src_dir,
                    "DETERMINISTIC", "HIGH",
                )

        # The declaredType might be a class but not an Enum node
        # Try to find it as a source file anyway
        src_file = _find_enum_file(project_src_dir, declared_type)
        if src_file:
            src_values = parse_enum_source(src_file)
            if src_values:
                return {
                    "field": field,
                    "enumClass": declared_type,
                    "enumFile": src_file,
                    "matchType": "DETERMINISTIC",
                    "confidence": "HIGH",
                    "values": src_values,
                    "transitions": [],
                    "sourceValues": src_values,
                }

    # Priority 2: Speculative fuzzy matching
    best_score = 0.0
    best_enum = None
    for ename, einfo in enum_by_name.items():
        score = match_score(field, ename)
        if score > best_score:
            best_score = score
            best_enum = (ename, einfo)

    if best_enum and best_score >= 0.3:
        ename, einfo = best_enum
        confidence = "MEDIUM" if best_score >= 0.5 else "LOW"
        return _build_enum_match(
            field, ename, einfo, project_src_dir,
            "SPECULATIVE", confidence,
        )

    # Priority 3: Check inner classes of Status.java for well-known patterns
    inner_match = _try_status_inner_class(field, project_src_dir)
    if inner_match:
        return inner_match

    # No match found
    return {
        "field": field,
        "enumClass": None,
        "enumFile": None,
        "matchType": "UNMATCHED",
        "confidence": "NONE",
        "values": [],
        "transitions": [],
    }


def _field_name_variants(field: str) -> List[str]:
    """Generate camelCase and snake_case variants of a field name."""
    variants = [field]
    # snake_case -> camelCase
    if "_" in field:
        camel = "".join(part.capitalize() for part in field.split("_"))
        camel = camel[0].lower() + camel[1:]
        variants.append(camel)
    # camelCase -> snake_case
    snake = re.sub(r"([A-Z])", r"_\1", field).lower().lstrip("_")
    if snake != field:
        variants.append(snake)
    return variants


def _build_enum_match(
    field: str,
    enum_name: str,
    enum_info: Dict[str, Any],
    project_src_dir: str,
    match_type: str,
    confidence: str,
) -> Dict[str, Any]:
    """Build a match result from an enum info dict."""
    enum_file = enum_info.get("filePath", "")
    full_path = os.path.join(project_src_dir, enum_file) if enum_file else ""

    # Parse source code for actual values
    src_values = parse_enum_source(full_path) if full_path else []

    # Parse graph.db constants as fallback
    db_values: List[Dict[str, str]] = []
    constants = enum_info.get("constants", "")
    if constants:
        try:
            const_list = json.loads(constants) if isinstance(constants, str) else constants
            if isinstance(const_list, list):
                for c in const_list:
                    if isinstance(c, str):
                        db_values.append({"name": c, "value": c, "description": ""})
                    elif isinstance(c, dict):
                        db_values.append({
                            "name": c.get("name", ""),
                            "value": c.get("value", ""),
                            "description": c.get("description", ""),
                        })
        except (json.JSONDecodeError, TypeError):
            pass

    # Prefer source values, fall back to graph.db values
    final_values = src_values if src_values else db_values

    return {
        "field": field,
        "enumClass": enum_name,
        "enumFile": full_path or enum_file,
        "matchType": match_type,
        "confidence": confidence,
        "values": final_values,
        "transitions": [],
        "sourceValues": src_values,
    }


def _try_status_inner_class(field: str, project_src_dir: str) -> Optional[Dict[str, Any]]:
    """Try to match a field to an inner class of Status.java."""
    # Known field-to-inner-class mappings based on naming conventions
    field_lower = field.lower().replace("_", "")

    # Map common field name patterns to Status inner class names
    inner_class_candidates = []

    if "requestinfo" in field_lower or field_lower == "status":
        inner_class_candidates.append("RequestInfoStatus")
    if "file" in field_lower and "status" in field_lower:
        inner_class_candidates.append("RequestFileStatus")
    if "feedback" in field_lower and "status" in field_lower:
        inner_class_candidates.append("FeedbackStatus")
    if "genfile" in field_lower and "status" in field_lower:
        inner_class_candidates.append("RequestFileStatus")
    if "rcn" in field_lower and "status" in field_lower:
        inner_class_candidates.append("ReconStatus")
    if "currentstep" in field_lower or "step" in field_lower:
        # Try CbrcStep enum
        cbrc_step_file = _find_enum_file(project_src_dir, "CbrcStep")
        if cbrc_step_file:
            values = parse_enum_source(cbrc_step_file)
            if values:
                return {
                    "field": field,
                    "enumClass": "CbrcStep",
                    "enumFile": cbrc_step_file,
                    "matchType": "SPECULATIVE",
                    "confidence": "MEDIUM",
                    "values": values,
                    "transitions": [],
                    "sourceValues": values,
                }
    if "convertstatus" in field_lower:
        # Try YesOrNo
        yesno_file = _find_enum_file(project_src_dir, "YesOrNo")
        if yesno_file:
            values = parse_enum_source(yesno_file)
            if values:
                return {
                    "field": field,
                    "enumClass": "YesOrNo",
                    "enumFile": yesno_file,
                    "matchType": "SPECULATIVE",
                    "confidence": "LOW",
                    "values": values,
                    "transitions": [],
                    "sourceValues": values,
                }

    # Try Status.java inner classes
    status_file = _find_enum_file(project_src_dir, "Status")
    if not status_file:
        return None

    for inner_name in inner_class_candidates:
        values = parse_status_inner_class(status_file, inner_name)
        if values:
            return {
                "field": field,
                "enumClass": f"Status.{inner_name}",
                "enumFile": status_file,
                "matchType": "SPECULATIVE",
                "confidence": "MEDIUM",
                "values": values,
                "transitions": [],
                "sourceValues": values,
            }

    return None


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Phase 3: State transition inference + source code verification"
    )
    parser.add_argument("db_path", help="Path to graph.db")
    parser.add_argument("cache_dir", help="Cache directory for I/O")
    parser.add_argument("project_src_dir", help="Java project source root")
    parser.add_argument(
        "--trace-cache",
        dest="trace_cache",
        default=None,
        help="Optional trace cache directory for transition triggers",
    )
    args = parser.parse_args()

    db_path = args.db_path
    cache_dir = args.cache_dir
    project_src_dir = args.project_src_dir
    trace_cache_dir = args.trace_cache

    # Validate inputs
    if not os.path.isfile(db_path):
        print(f"ERROR: graph.db not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    phase2_path = os.path.join(cache_dir, "phase2-operations.json")
    if not os.path.isfile(phase2_path):
        print(f"ERROR: phase2-operations.json not found at {phase2_path}", file=sys.stderr)
        sys.exit(1)

    # Load Phase 2 data
    with open(phase2_path, "r", encoding="utf-8") as f:
        phase2_data = json.load(f)

    print(f"Loaded Phase 2: {phase2_data.get('totalTables', 0)} tables")

    # Step 1: Identify status fields
    status_fields = identify_status_fields(phase2_data)
    total_status_fields = sum(len(v) for v in status_fields.values())
    print(f"Identified {total_status_fields} status-like fields in {len(status_fields)} tables")

    # Step 2: Query graph.db for enum nodes
    print("Querying graph.db for Enum nodes...")
    enum_nodes = query_enum_nodes(db_path)
    print(f"  Found {len(enum_nodes)} Enum nodes")

    # Step 3: Query entity property types
    print("Querying entity property declared types...")
    entity_prop_types = query_entity_property_types(db_path)
    print(f"  Found properties for {len(entity_prop_types)} entity classes")

    # Step 4: Match enums to fields
    print("Matching status fields to enum classes...")
    tables_result, state_diffs = match_enums_to_fields(
        status_fields, entity_prop_types, enum_nodes, project_src_dir,
    )

    # Step 5: Trace cache scanning (optional)
    transition_count = 0
    if trace_cache_dir and os.path.isdir(trace_cache_dir):
        print(f"Scanning trace cache at {trace_cache_dir}...")
        cache_transitions = scan_trace_cache(trace_cache_dir, status_fields)

        # Merge transitions into table results
        for table_entry in tables_result:
            tbl_name = table_entry["tableName"]
            for st in table_entry["stateTransitions"]:
                field = st["field"]
                key = (tbl_name, field)
                if key in cache_transitions:
                    # Deduplicate triggers
                    triggers_seen: Set[str] = set()
                    for t in cache_transitions[key]:
                        trigger_name = t["trigger"]
                        if trigger_name not in triggers_seen:
                            st["transitions"].append({
                                "from": "",
                                "to": "",
                                "trigger": trigger_name,
                                "context": t.get("context", ""),
                            })
                            triggers_seen.add(trigger_name)
                    transition_count += len(st["transitions"])

        print(f"  Found {transition_count} transition triggers")
    else:
        if trace_cache_dir:
            print(f"WARNING: trace cache dir not found: {trace_cache_dir}", file=sys.stderr)

    # Summary
    matched_count = sum(
        1
        for t in tables_result
        for st in t["stateTransitions"]
        if st.get("enumClass")
    )
    unmatched_count = sum(
        1
        for t in tables_result
        for st in t["stateTransitions"]
        if not st.get("enumClass")
    )
    deterministic_count = sum(
        1
        for t in tables_result
        for st in t["stateTransitions"]
        if st.get("matchType") == "DETERMINISTIC"
    )
    speculative_count = sum(
        1
        for t in tables_result
        for st in t["stateTransitions"]
        if st.get("matchType") == "SPECULATIVE"
    )

    print(f"\nResults:")
    print(f"  Tables with state transitions: {len(tables_result)}")
    print(f"  Matched fields: {matched_count} (DETERMINISTIC: {deterministic_count}, SPECULATIVE: {speculative_count})")
    print(f"  Unmatched fields: {unmatched_count}")
    print(f"  State diffs: {len(state_diffs)}")
    print(f"  Transition triggers: {transition_count}")

    # Output
    output = {
        "phase": "state_inference",
        "totalTables": len(tables_result),
        "totalStatusFields": total_status_fields,
        "tables": tables_result,
        "stateDiffs": state_diffs,
    }

    os.makedirs(cache_dir, exist_ok=True)
    output_path = os.path.join(cache_dir, "phase3-states.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nOutput written to: {output_path}")


if __name__ == "__main__":
    main()
