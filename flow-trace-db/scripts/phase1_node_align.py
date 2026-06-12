#!/usr/bin/env python3
"""Phase 1 nodeId alignment: match entries to graph.db nodes.

Reads phase1/entries.json, queries graph.db for each entry by
className + methodName, backfills nodeId and graphDbMatch fields.
Generates phase1/align-errors.json for ambiguous and unmatched entries.

Usage:
    python3 phase1_node_align.py <entries_path> <db_path>
"""

import json
import os
import sqlite3
import sys


def query_node_id(conn, class_name, method_name):
    """Query graph.db for a Method node by owner class + method name.

    Returns (node_id, file_path, match_count) tuple.
    match_count > 1 means ambiguous.
    """
    rows = conn.execute("""
        SELECT n.id, n.file_path
        FROM nodes n
        JOIN relationships hm ON hm.target_id = n.id AND hm.type = 'HAS_METHOD'
        JOIN nodes owner ON owner.id = hm.source_id
        WHERE owner.name = ? AND n.label = 'Method' AND n.name = ?
    """, [class_name, method_name]).fetchall()

    if not rows:
        return None, None, 0
    if len(rows) == 1:
        return rows[0][0], rows[0][1], 1
    return rows[0][0], rows[0][1], len(rows)


def resolve_ambiguous(conn, class_name, method_name, file_path):
    """Try to resolve ambiguous match using filePath."""
    rows = conn.execute("""
        SELECT n.id, n.file_path
        FROM nodes n
        JOIN relationships hm ON hm.target_id = n.id AND hm.type = 'HAS_METHOD'
        JOIN nodes owner ON owner.id = hm.source_id
        WHERE owner.name = ? AND n.label = 'Method' AND n.name = ?
    """, [class_name, method_name]).fetchall()

    for row in rows:
        if file_path and row[1] and file_path in row[1]:
            return row[0], row[1]
    return rows[0][0], rows[0][1]


def main():
    if len(sys.argv) < 3:
        print("Usage: phase1_node_align.py <entries_path> <db_path>")
        sys.exit(1)

    entries_path = sys.argv[1]
    db_path = sys.argv[2]

    if not os.path.exists(entries_path):
        print(f"Error: entries not found: {entries_path}")
        sys.exit(1)

    if not os.path.exists(db_path):
        print(f"Warning: graph.db not found: {db_path}")
        with open(entries_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for entry in data["entries"]:
            entry["nodeId"] = ""
            entry["graphDbMatch"] = "error"
            entry["matchNote"] = "db_not_found"
        with open(entries_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        errors_dir = os.path.dirname(entries_path)
        error_path = os.path.join(errors_dir, "align-errors.json")
        with open(error_path, "w", encoding="utf-8") as f:
            json.dump({"totalErrors": len(data["entries"]), "ambiguous": [],
                        "notFound": [], "dbError": True}, f, ensure_ascii=False, indent=2)
        print(f"All {len(data['entries'])} entries marked as error (db not found)")
        return

    with open(entries_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    ambiguous = []
    not_found = []
    matched = 0

    for entry in data["entries"]:
        class_name = entry["className"]
        method_name = entry["methodName"]
        file_path = entry.get("filePath", "")

        node_id, node_file, count = query_node_id(conn, class_name, method_name)

        if count == 0:
            entry["nodeId"] = ""
            entry["graphDbMatch"] = False
            entry["matchNote"] = "not_found"
            not_found.append({
                "entryId": entry["id"],
                "className": class_name,
                "methodName": method_name,
                "filePath": file_path,
            })
        elif count == 1:
            entry["nodeId"] = node_id
            entry["graphDbMatch"] = True
            matched += 1
        else:
            resolved_id, resolved_file = resolve_ambiguous(
                conn, class_name, method_name, file_path
            )
            entry["nodeId"] = resolved_id
            entry["graphDbMatch"] = True
            entry["matchNote"] = "ambiguous"
            ambiguous.append({
                "entryId": entry["id"],
                "className": class_name,
                "methodName": method_name,
                "filePath": file_path,
                "resolvedNodeId": resolved_id,
                "resolvedFilePath": resolved_file,
            })
            matched += 1

    conn.close()

    with open(entries_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    phase1_dir = os.path.dirname(entries_path)
    if ambiguous or not_found:
        error_path = os.path.join(phase1_dir, "align-errors.json")
        error_data = {
            "totalErrors": len(ambiguous) + len(not_found),
            "ambiguous": ambiguous,
            "notFound": not_found,
        }
        with open(error_path, "w", encoding="utf-8") as f:
            json.dump(error_data, f, ensure_ascii=False, indent=2)

    total = len(data["entries"])
    print(f"Aligned {total} entries: {matched} matched, "
          f"{len(not_found)} not found, {len(ambiguous)} ambiguous")


if __name__ == "__main__":
    main()
