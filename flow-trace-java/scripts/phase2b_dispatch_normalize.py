"""Phase 2b: Dispatch Summary Normalizer

Normalizes sub-agent output format for all dispatch-summary files.
Handles known field name variants and structural inconsistencies.

Usage:
    python3 phase2b_dispatch_normalize.py --cache-dir <cache_dir>
"""
import json, os, argparse, glob, sys


def parse_args():
    p = argparse.ArgumentParser(description="Phase 2b: Dispatch Summary Normalizer")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    return p.parse_args()


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# Field name mappings: variant -> canonical
_RESULT_FIELD_MAP = {
    "implClass": "class",
    "className": "class",
    "fullClassName": "class",
    "name": "shortName",
    "routeCondition": "condition",
    "routeConditionSource": "condition",
    "downstreamCalls": "endpoints",
    "calls": "endpoints",
}

_ENDPOINT_FIELD_MAP = {
    "endpointType": "type",
    "target": "class",
    "targetMethod": "method",
}


def _normalize_result(r):
    """Normalize a single result entry. Returns (normalized_result, fields_fixed)."""
    fixed = 0
    nr = {}

    for old_key, new_key in _RESULT_FIELD_MAP.items():
        if old_key in r and new_key not in r:
            nr[new_key] = r.pop(old_key)
            fixed += 1

    # Copy remaining fields
    for k, v in r.items():
        nr[k] = v

    # Ensure required fields
    if "class" not in nr:
        nr["class"] = ""
        fixed += 1
    if "shortName" not in nr:
        nr["shortName"] = nr.get("class", "").rsplit(".", 1)[-1] if nr.get("class") else ""
        fixed += 1
    if "condition" not in nr:
        nr["condition"] = "unknown"
        fixed += 1
    if "endpoints" not in nr:
        nr["endpoints"] = []
        fixed += 1

    # Normalize endpoint fields
    norm_eps = []
    for ep in nr.get("endpoints", []):
        if not isinstance(ep, dict):
            continue
        nep = {}
        for old_key, new_key in _ENDPOINT_FIELD_MAP.items():
            if old_key in ep and new_key not in ep:
                nep[new_key] = ep.pop(old_key)
                fixed += 1
        for k, v in ep.items():
            nep[k] = v
        norm_eps.append(nep)
    nr["endpoints"] = norm_eps

    return nr, fixed


def normalize_file(filepath, pattern_index):
    """Normalize a single dispatch-summary file. Returns (was_modified, stats)."""
    basename = os.path.basename(filepath)
    short_name = basename.replace("dispatch-summary-", "").replace(".json", "")

    with open(filepath) as f:
        data = json.load(f)

    total_fixed = 0
    structural_fix = False

    # Fix 1: bare array -> wrapped object
    if isinstance(data, list):
        interface = ""
        dispatch_type = "UNKNOWN"
        for p in pattern_index:
            if p["interface"].rsplit(".", 1)[-1] == short_name:
                interface = p["interface"]
                dispatch_type = p.get("type", "UNKNOWN")
                break
        data = {
            "interface": interface,
            "dispatchType": dispatch_type,
            "results": data,
        }
        structural_fix = True

    # Fix 2: normalize each result entry
    if not isinstance(data, dict) or "results" not in data:
        return False, {"error": "unrecognized structure"}

    norm_results = []
    for r in data["results"]:
        nr, fixed = _normalize_result(r)
        norm_results.append(nr)
        total_fixed += fixed

    data["results"] = norm_results

    # Save if changed
    if total_fixed > 0 or structural_fix:
        _save_json(filepath, data)

    return (total_fixed > 0 or structural_fix), {
        "results": len(norm_results),
        "fields_fixed": total_fixed,
        "structural_fix": structural_fix,
    }


def main():
    args = parse_args()
    phase2b_dir = os.path.join(args.cache_dir, "phase2b")

    # Load pattern-index for interface name lookup
    pi_path = os.path.join(args.cache_dir, "phase1c", "pattern-index.json")
    if not os.path.exists(pi_path):
        print("ERROR: pattern-index.json not found", file=sys.stderr)
        sys.exit(1)
    with open(pi_path) as f:
        pi = json.load(f)
    pattern_index = pi.get("patterns", [])

    files = sorted(glob.glob(os.path.join(phase2b_dir, "dispatch-summary-*.json")))
    if not files:
        print("No dispatch-summary files found")
        return

    total_fixed = 0
    total_ok = 0
    for filepath in files:
        modified, stats = normalize_file(filepath, pattern_index)
        name = os.path.basename(filepath)
        if modified:
            total_fixed += 1
            print(f"  Fixed: {name} ({stats['fields_fixed']} fields, {stats['results']} results)")
        else:
            total_ok += 1

    print(f"\nNormalized: {total_fixed} files fixed, {total_ok} files already correct")


if __name__ == "__main__":
    main()
