"""Phase 3: Terminal-oriented BFS chain extraction from graph.db"""
import sqlite3
import json
import os
import re
import time
from collections import defaultdict


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Phase 3: Terminal-oriented chain extraction from graph.db")
    p.add_argument("db_path", help="Path to graph.db")
    p.add_argument("entries_path", help="Path to entries JSON")
    p.add_argument("cache_dir", help="Output directory for chain JSON files")
    p.add_argument("--db-schema", help="Path to db-schema JSON file", default=None)
    return p.parse_args()


MAX_DEPTH = 20
MAX_NODES = 500
MAX_FANOUT = 10
TIMEOUT = 30
MIN_CONFIDENCE = 0.5
MIN_CONFIDENCE_TERMINAL = 0.3

# Noise filter: method names to skip
SKIP_METHODS = {
    'toString', 'hashCode', 'equals', 'getClass', 'notify', 'notifyAll', 'wait',
    'clone', 'finalize', 'valueOf', 'values', 'ordinal', 'name', 'getBytes',
    'length', 'charAt', 'substring', 'indexOf', 'contains', 'isEmpty', 'trim',
    'toLowerCase', 'toUpperCase', 'startsWith', 'endsWith', 'replace', 'split',
    'compareTo', 'compareToIgnoreCase', 'format', 'println', 'print',
    'getObjectValue', 'setObjectValue', 'getObject', 'setObject',
    'intValue', 'longValue', 'doubleValue', 'floatValue', 'booleanValue',
    'shortValue', 'byteValue', 'charValue',
    'join', 'exists', 'mkdirs', 'builder',
}

# Noise filter: class names to always skip
SKIP_CLASS_NAMES = {
    'GeneratedCriteria', 'Criteria', 'TaskExecuteResult',
    'ErrorCodeMapping', 'ErrorCode', 'BizErrorCode',
    'SM2', 'SM4Utils', 'SM2Util', 'DateTools',
    'TempMapperRecoderCase',
}

# Noise filter: class name prefixes (infrastructure)
SKIP_CLASS_PREFIXES = ('Ftp', 'Sftp', 'Http', 'Rest')

# Noise filter: class name suffixes (data objects, builders, etc.)
NOISE_SUFFIXES = (
    'DTO', 'Dto', 'VO', 'Vo', 'Entity', 'Bean',
    'Request', 'Response', 'Param', 'Form',
    'Adapter', 'Composite', 'Provider', 'Converter', 'Helper',
    'Builder', 'BuilderFactory', 'Config', 'Constant', 'Constants',
)

# Noise filter: enum-like suffixes
ENUM_SUFFIXES = ('Type', 'Cd', 'Flag', 'Enum')

# Noise filter: utility suffixes
UTIL_SUFFIXES = ('Utils', 'Util', 'Tools')

# Noise filter: package patterns (JDK, third-party frameworks)
NOISE_PACKAGE_PATTERNS = [
    'java.lang', 'java.util', 'java.io', 'java.nio', 'java.net',
    'java.math', 'java.text', 'java.time', 'java.sql',
    'javax.', 'sun.',
    'org.springframework', 'org.apache', 'org.slf4j', 'org.jboss',
    'com.google', 'com.fasterxml', 'lombok',
    'org.hibernate', 'org.mybatis', 'com.alibaba.fastjson',
]

# Noise filter: context-noise methods
CONTEXT_NOISE_METHODS = {
    'of', 'addAll', 'add', 'clear', 'size', 'equalsAny',
    'ok', 'createCriteria', 'createCriteriaInternal', 'addCriterion',
}

# Methods to always keep regardless of context
ALWAYS_KEEP_METHODS = {
    'support', 'matches', 'isSupported', 'canHandle', 'accept',
    'process', 'execute', 'handle', 'run', 'doHandle',
    'apply', 'dispatch', 'perform',
}


def classify_node(class_name, method_name, file_path):
    """Classify a node as NOISE, TERMINAL, or TRAVERSABLE.

    Order: noise check -> terminal check -> default traversable.
    """
    if _is_noise(class_name, method_name, file_path):
        return "NOISE"
    if _is_terminal(class_name):
        return "TERMINAL"
    return "TRAVERSABLE"


def _is_noise(class_name, method_name, file_path):
    """Check if node is noise (should be discarded entirely)."""
    # Method name filter
    if method_name in SKIP_METHODS:
        if method_name in ('join', 'exists', 'mkdirs'):
            if not any(kw in class_name for kw in ('Service', 'Dao', 'Mapper', 'Repository')):
                return True
        else:
            return True

    # Known noise classes
    if class_name in SKIP_CLASS_NAMES:
        return True

    # Infrastructure prefixes
    if any(class_name.startswith(p) for p in SKIP_CLASS_PREFIXES):
        return True

    # Exception/Error classes
    if 'Exception' in class_name or class_name.endswith('ErrorCode'):
        return True

    # Data objects (DTO, VO, Entity, Request, Response, etc.)
    if any(class_name.endswith(s) for s in NOISE_SUFFIXES):
        return True

    # Enum classes
    if any(class_name.endswith(s) for s in ENUM_SUFFIXES):
        return True

    # Utility classes
    if any(class_name.endswith(s) for s in UTIL_SUFFIXES):
        return True

    # Builder/Factory
    if class_name.endswith('Factory') or class_name.endswith('AdapterFactory'):
        return True

    # Getter/setter (except on business classes)
    if method_name.startswith(('get', 'set', 'is')):
        if not any(kw in class_name for kw in ('Mapper', 'Dao', 'Repository', 'Service')):
            # Only skip if it looks like a simple getter/setter (short name after prefix)
            rest = method_name[3:] if method_name.startswith(('get', 'set')) else method_name[2:]
            if rest and rest[0].isupper():
                return True

    # Enum factory methods
    if method_name == 'of' and any(class_name.endswith(s) for s in ENUM_SUFFIXES):
        return True

    # Context-noise methods
    if method_name in CONTEXT_NOISE_METHODS:
        return True

    # Framework packages
    if file_path:
        for pat in NOISE_PACKAGE_PATTERNS:
            if pat in file_path:
                return True

    return False


def _is_terminal(class_name):
    """Check if node is a terminal (Mapper/Dao/Repository/Client/Proxy)."""
    return any(kw in class_name for kw in ('Mapper', 'Dao', 'Repository', 'Client', 'Proxy'))


def determine_layer_type(class_name, method_name, file_path, is_rmb_client=False, is_entry=False):
    if is_entry:
        return "ENTRY"
    if is_rmb_client:
        return "RMB_CLIENT"
    if file_path:
        if '/dao/' in file_path or '/repo/dao/' in file_path or '/mapper/' in file_path:
            if any(kw in class_name for kw in ('Mapper', 'Dao', 'Repository')):
                return "MAPPER"
        if '/service/' in file_path and 'Service' in class_name:
            return "SERVICE"
    if 'Mapper' in class_name or 'Dao' in class_name:
        return "MAPPER"
    if 'Repository' in class_name:
        return "REPOSITORY"
    if 'Service' in class_name:
        return "SERVICE"
    if 'Client' in class_name or 'Proxy' in class_name:
        return "RMB_CLIENT"
    if 'Handler' in class_name or 'Processor' in class_name:
        return "HANDLER"
    return "HANDLER"


def extract_package(file_path):
    if not file_path:
        return ""
    marker = "src/main/java/"
    idx = file_path.find(marker)
    if idx < 0:
        return ""
    pkg = file_path[idx + len(marker):]
    pkg = pkg.rsplit('/', 1)[0] if '/' in pkg else pkg
    return pkg.replace('/', '.')


def determine_role(class_name, layer_type):
    if 'Controller' in class_name:
        return "Controller 入口"
    if 'Dao' in class_name:
        return "数据访问层"
    if 'Mapper' in class_name:
        return "MyBatis Mapper"
    role_map = {
        "ENTRY": "Controller 入口",
        "RMB_CONTROLLER": "RMB 接收端入口",
        "SERVICE": "业务服务层",
        "MAPPER": "MyBatis Mapper",
        "REPOSITORY": "数据仓库",
        "RMB_CLIENT": "RMB 客户端",
        "HANDLER": "处理器",
    }
    return role_map.get(layer_type, "处理器")


def terminal_backtrack(chain, entry_node_id, discarded_edges):
    """Remove nodes not on any terminal path, record as discardedEdges."""
    if not chain:
        return chain

    # Build node lookup
    node_by_id = {n["nodeId"]: n for n in chain if n.get("nodeId")}

    # Collect all terminal nodes
    terminals = [n for n in chain if n.get("terminal")]

    # Backtrack from each terminal to entry, mark reachable
    reachable = {entry_node_id}
    for term in terminals:
        nid = term["nodeId"]
        while nid and nid not in reachable:
            reachable.add(nid)
            node = node_by_id.get(nid)
            if not node:
                break
            nid = node.get("parentId", "")

    # Split chain into reachable and discarded
    new_chain = []
    for n in chain:
        if n.get("nodeId") in reachable or n.get("layer") == 1:
            new_chain.append(n)
        else:
            discarded_edges.append({
                "parent": n.get("parentId", ""),
                "parentClass": "",
                "parentMethod": "",
                "childClass": n.get("class", ""),
                "childMethod": n.get("method", ""),
                "childId": n.get("nodeId", ""),
                "confidence": None,
                "reason": "not_on_terminal_path",
            })

    return new_chain


def resolve_domain_interaction(child_id, child_class, child_method, child_file, conn, table_lookup, rmb_controllers):
    """Resolve domainInteraction for a node using 4 paths."""
    domain_interaction = None
    lt_override = None

    # Path 1: QUERIES relationship
    queries = conn.execute("""
        SELECT
            ce.name AS statement_id,
            json_extract(ce.properties_json, '$.statementKind') AS statement_kind,
            json_extract(ce.properties_json, '$.tableName') AS table_name_direct,
            json_extract(ce.properties_json, '$.sqlText') AS sql_text,
            json_extract(ce.properties_json, '$.namespace') AS namespace
        FROM relationships r
        JOIN nodes ce ON ce.id = r.target_id
        WHERE r.source_id = ? AND r.type = 'QUERIES'
    """, [child_id]).fetchall()

    if queries:
        for q in queries:
            table_name = q["table_name_direct"]
            if table_name:
                op_map = {"select": "SELECT", "insert": "INSERT", "update": "UPDATE", "delete": "DELETE"}
                operation = op_map.get(q["statement_kind"], "SELECT")
                direction = "READ" if operation == "SELECT" else "WRITE"
                domain_interaction = {
                    "type": "DATABASE",
                    "operation": operation,
                    "table": table_name,
                    "direction": direction,
                    "source": "graph-db",
                }
                lt_override = "MAPPER"
                break

    # Path 2: db-schema lookup
    if not domain_interaction:
        schema_key = f"{child_class}.{child_method}"
        if schema_key in table_lookup:
            info = table_lookup[schema_key]
            direction = "READ" if info["operation"] == "SELECT" else "WRITE"
            domain_interaction = {
                "type": "DATABASE",
                "operation": info["operation"],
                "table": info["table"],
                "direction": direction,
                "source": "inferred-from-calls",
            }
            lt_override = "MAPPER"

    # Path 2b: Dao→Mapper delegate resolution
    if not domain_interaction and 'Dao' in child_class:
        delegates = conn.execute("""
            SELECT child.name AS method, owner.name AS class
            FROM relationships r
            JOIN nodes child ON child.id = r.target_id
            LEFT JOIN relationships hm ON hm.target_id = child.id AND hm.type = 'HAS_METHOD'
            LEFT JOIN nodes owner ON owner.id = hm.source_id
            WHERE r.source_id = ? AND r.type = 'CALLS' AND child.label = 'Method'
        """, [child_id]).fetchall()
        for dl in delegates:
            dkey = f"{dl['class']}.{dl['method']}"
            if dkey in table_lookup:
                info = table_lookup[dkey]
                direction = "READ" if info["operation"] == "SELECT" else "WRITE"
                domain_interaction = {
                    "type": "DATABASE",
                    "operation": info["operation"],
                    "table": info["table"],
                    "direction": direction,
                    "source": "delegate-dao-to-mapper",
                }
                lt_override = "MAPPER"
                break

    # Path 3: RMB Client class name
    if 'Client' in child_class or 'Proxy' in child_class:
        if not domain_interaction:
            domain_interaction = {
                "type": "EXTERNAL",
                "direction": "OUT",
                "target": child_method,
                "protocol": "RMB",
                "source": "graph-db",
            }
            lt_override = "RMB_CLIENT"

    # Path 4: RMB Controller ID
    if child_id in rmb_controllers:
        if not domain_interaction:
            domain_interaction = {
                "type": "EXTERNAL",
                "direction": "IN",
                "target": child_method,
                "protocol": "RMB",
                "source": "graph-db",
            }
            lt_override = "RMB_CONTROLLER"

    return domain_interaction, lt_override


def apply_fanout_control(children, max_fanout):
    """Apply confidence filter and fanout limit.

    Layer 2: Confidence filter
    - >= 0.7: keep
    - [0.5, 0.7): keep only terminal types
    - < 0.5: discard

    Layer 3: Fanout limit (top N by confidence)
    """
    kept = []
    discarded = []

    for child in children:
        conf = child.get("confidence", 0) or 0
        cls = child.get("child_class", "")

        if conf >= 0.7:
            kept.append(child)
        elif conf >= 0.5:
            kept.append(child)
        else:
            # At very low confidence, still keep terminals
            if conf >= MIN_CONFIDENCE_TERMINAL and _is_terminal(cls):
                kept.append(child)
            else:
                discarded.append((child, "below_confidence_threshold"))

    # Layer 3: Fanout limit
    if len(kept) > max_fanout:
        kept.sort(key=lambda c: c.get("confidence", 0) or 0, reverse=True)
        overflow = kept[max_fanout:]
        kept = kept[:max_fanout]
        for c in overflow:
            discarded.append((c, "fanout_overflow"))

    return kept, discarded


def main():
    args = parse_args()
    db_path = args.db_path
    entries_path = args.entries_path
    cache_dir = args.cache_dir
    db_schema_path = args.db_schema

    os.makedirs(os.path.join(cache_dir, "phase3"), exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    with open(entries_path, 'r') as f:
        raw = json.load(f)

    if isinstance(raw, dict) and "entries" in raw:
        entries = raw["entries"]
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = raw.get("entries", [])

    for e in entries:
        e.setdefault("class", e.get("className", ""))
        e.setdefault("method", e.get("methodName", ""))
        e.setdefault("file", e.get("filePath", ""))
        e.setdefault("nodeId", e.get("nodeId", e.get("methodId", "")))
        e.setdefault("type", e.get("type", ""))
        e.setdefault("id", e.get("id", ""))

    # Load db-schema
    table_lookup = {}
    if db_schema_path and os.path.exists(db_schema_path):
        with open(db_schema_path, 'r') as f:
            db_schema = json.load(f)
        if "lookup" in db_schema:
            table_lookup = db_schema["lookup"]
        else:
            for t in db_schema.get("tables", []):
                table_name = t.get("tableName", t.get("name", ""))
                for op in t.get("operations", []):
                    mapper_class = op.get("mapperClass", op.get("class", ""))
                    mapper_method = op.get("statementId", op.get("method", op.get("mapperMethod", "")))
                    operation = op.get("type", op.get("operation", "SELECT"))
                    key = f"{mapper_class}.{mapper_method}"
                    table_lookup[key] = {"table": table_name, "operation": operation}

    total = len(entries)
    completed = 0

    rmb_controllers = set()
    for e in entries:
        if e["type"] == "rmb":
            rmb_controllers.add(e["nodeId"])

    print(f"Phase 3: Terminal-oriented chain extraction - {total} entries")
    start_time = time.time()

    for entry in entries:
        entry_id = entry["id"]
        entry_node_id = entry["nodeId"]
        entry_class = entry["class"]
        entry_method = entry["method"]
        entry_file = entry["file"]
        entry_type = entry["type"]

        output_path = os.path.join(cache_dir, "phase3", f"{entry_id}.json")
        if os.path.exists(output_path):
            completed += 1
            continue

        t0 = time.time()
        chain = []
        discarded_edges = []
        unexpanded_nodes = []
        visited = set()

        # Entry node
        entry_layer_type = "RMB_CONTROLLER" if entry_type == "rmb" else "ENTRY"
        entry_node = {
            "layer": 1,
            "layerType": entry_layer_type,
            "class": entry_class,
            "method": entry_method,
            "description": "",
            "parentLayer": 0,
            "source": "graph-db",
            "file_path": entry_file,
            "nodeId": entry_node_id,
            "package": extract_package(entry_file),
            "role": determine_role(entry_class, entry_layer_type),
            "terminal": False,
        }
        chain.append(entry_node)
        visited.add(entry_node_id)

        # BFS
        current_layer_nodes = [entry_node_id]
        depth = 1
        status = "COMPLETE"

        while depth < MAX_DEPTH and len(chain) < MAX_NODES:
            if not current_layer_nodes:
                break
            if time.time() - t0 > TIMEOUT:
                status = "PARTIAL"
                break

            # Batch query CALLS
            placeholders = ','.join(['?' for _ in current_layer_nodes])
            query = f"""
                SELECT
                    parent.id AS parent_id,
                    child.id AS child_id,
                    child.name AS child_method,
                    child.label AS child_label,
                    child.file_path AS child_file,
                    r.confidence,
                    r.reason,
                    r.step AS call_site_line,
                    owner.name AS child_class
                FROM relationships r
                JOIN nodes parent ON parent.id = r.source_id
                JOIN nodes child ON child.id = r.target_id
                LEFT JOIN relationships hm ON hm.target_id = child.id AND hm.type = 'HAS_METHOD'
                LEFT JOIN nodes owner ON owner.id = hm.source_id
                WHERE r.source_id IN ({placeholders})
                  AND r.type = 'CALLS'
                  AND child.label = 'Method'
                  AND r.confidence >= ?
                ORDER BY parent.id, r.step
            """
            params = current_layer_nodes + [MIN_CONFIDENCE_TERMINAL]
            rows = conn.execute(query, params).fetchall()

            children_by_parent = defaultdict(list)
            for row in rows:
                children_by_parent[row["parent_id"]].append(dict(row))

            next_layer_nodes = []

            for parent_id in current_layer_nodes:
                children = children_by_parent.get(parent_id, [])

                parent_layer = 1
                for c in chain:
                    if c.get("nodeId") == parent_id:
                        parent_layer = c["layer"]
                        break

                # Deduplicate
                seen_children = set()
                filtered_children = []
                for child in children:
                    child_class = child.get("child_class") or ""
                    child_method = child.get("child_method") or ""
                    child_key = f"{child_class}.{child_method}"
                    if child_key in seen_children:
                        continue
                    seen_children.add(child_key)

                    if child.get("child_id") in visited:
                        continue

                    # Layer 1: Noise filter
                    child_file = child.get("child_file") or ""
                    node_type = classify_node(child_class, child_method, child_file)
                    if node_type == "NOISE":
                        continue

                    # Always-keep methods bypass further filtering
                    if child_method not in ALWAYS_KEEP_METHODS:
                        # Skip getter/setter that slipped through
                        if child_method.startswith(('get', 'is')) and not any(
                            kw in child_class for kw in ('Mapper', 'Dao', 'Repository', 'Service')
                        ):
                            continue
                        if child_method.startswith('set') and not any(
                            kw in child_class for kw in ('Mapper', 'Dao')
                        ):
                            continue

                    child["node_type"] = node_type
                    filtered_children.append(child)

                # Layer 2+3: Confidence filter + fanout limit
                kept, disc = apply_fanout_control(filtered_children, MAX_FANOUT)
                for child, reason in disc:
                    discarded_edges.append({
                        "parent": parent_id,
                        "parentClass": "",
                        "parentMethod": "",
                        "childClass": child.get("child_class", ""),
                        "childMethod": child.get("child_method", ""),
                        "childId": child.get("child_id", ""),
                        "confidence": child.get("confidence"),
                        "reason": reason,
                    })

                for child in kept:
                    child_id = child["child_id"]
                    child_class = child.get("child_class") or ""
                    child_method = child.get("child_method") or ""
                    child_file = child.get("child_file") or ""
                    node_type = child.get("node_type", "TRAVERSABLE")

                    is_terminal = node_type == "TERMINAL"
                    is_rmb = child_id in rmb_controllers or 'Client' in child_class
                    lt = determine_layer_type(child_class, child_method, child_file, is_rmb_client=is_rmb)

                    # Resolve domainInteraction for terminals
                    domain_interaction = None
                    lt_override = None
                    if is_terminal:
                        domain_interaction, lt_override = resolve_domain_interaction(
                            child_id, child_class, child_method, child_file,
                            conn, table_lookup, rmb_controllers,
                        )
                        if lt_override and lt not in ("MAPPER", "REPOSITORY", "RMB_CLIENT", "RMB_CONTROLLER"):
                            lt = lt_override

                    new_layer = parent_layer + 1
                    node = {
                        "layer": new_layer,
                        "layerType": lt,
                        "class": child_class,
                        "method": child_method,
                        "description": "",
                        "parentLayer": parent_layer,
                        "parentId": parent_id,
                        "callSiteLine": child.get("call_site_line"),
                        "source": "graph-db",
                        "file_path": child_file,
                        "nodeId": child_id,
                        "package": extract_package(child_file),
                        "role": determine_role(child_class, lt),
                        "terminal": is_terminal,
                    }
                    if domain_interaction:
                        node["domainInteraction"] = domain_interaction

                    chain.append(node)
                    visited.add(child_id)

                    # Expand non-terminal nodes (key change from old design)
                    if not is_terminal:
                        next_layer_nodes.append(child_id)

                    if len(chain) >= MAX_NODES:
                        status = "TRUNCATED"
                        break

                if len(chain) >= MAX_NODES:
                    break

            current_layer_nodes = next_layer_nodes
            depth += 1

        # Record unexpanded nodes (truncated by limits)
        if current_layer_nodes and (depth >= MAX_DEPTH or len(chain) >= MAX_NODES or status == "PARTIAL"):
            for nid in current_layer_nodes:
                node_info = None
                for c in chain:
                    if c.get("nodeId") == nid:
                        node_info = c
                        break
                if node_info:
                    reason = "max_depth_reached" if depth >= MAX_DEPTH else "max_nodes_reached"
                    unexpanded_nodes.append({
                        "nodeId": nid,
                        "class": node_info.get("class", ""),
                        "method": node_info.get("method", ""),
                        "layer": node_info.get("layer", 0),
                        "reason": reason,
                    })

        # Terminal backtracking: prune nodes not on any terminal path
        chain = terminal_backtrack(chain, entry_node_id, discarded_edges)

        # Fill parentClass/parentMethod in discarded_edges
        node_by_id = {n["nodeId"]: n for n in chain if n.get("nodeId")}
        for de in discarded_edges:
            if not de.get("parentClass"):
                parent = node_by_id.get(de.get("parent"))
                if parent:
                    de["parentClass"] = parent.get("class", "")
                    de["parentMethod"] = parent.get("method", "")

        chain_data = {
            "entryId": entry_id,
            "entryType": entry_type,
            "status": status,
            "chain": chain,
            "discardedEdges": discarded_edges,
            "unexpandedNodes": unexpanded_nodes,
        }
        with open(output_path, 'w') as f:
            json.dump(chain_data, f, indent=2, ensure_ascii=False)

        completed += 1
        if completed % 50 == 0:
            elapsed = time.time() - start_time
            rate = completed / elapsed if elapsed > 0 else 0
            print(f"  Progress: {completed}/{total} ({rate:.1f}/s)")

    total_elapsed = time.time() - start_time
    print(f"\nPhase 3 complete: {completed} entries processed in {total_elapsed:.1f}s")
    conn.close()


if __name__ == "__main__":
    main()
