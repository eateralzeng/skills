"""Phase 2b: Dispatch Summary Preparation

Manages the dispatch analysis process with modes:
- prepare: Generate prompt context for each dispatch point
- merge: Merge multi-batch subagent results into a single dispatch-summary
- validate: Check dispatch-summary completeness against pattern-index

Usage:
    python3 phase2b_dispatch_prepare.py --mode prepare --cache-dir <cache> --project-dir <project>
    python3 phase2b_dispatch_prepare.py --mode merge --cache-dir <cache> --pattern-name <name> --results <path>
    python3 phase2b_dispatch_prepare.py --mode validate --cache-dir <cache>
"""
import json, os, argparse, sys


def parse_args():
    p = argparse.ArgumentParser(description="Phase 2b: Dispatch Summary Preparation")
    p.add_argument("--mode", required=True,
                   choices=["prepare", "merge", "validate"],
                   help="Operation mode")
    p.add_argument("--cache-dir", required=True, help="Cache root (.trace-cache/)")
    p.add_argument("--project-dir", help="Project source root (for prepare mode)")
    p.add_argument("--pattern-name", help="Dispatch point short name (for merge mode)")
    p.add_argument("--results", help="Path to batch results file or directory (for merge mode)")
    return p.parse_args()


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _save_json(path, data):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _class_from_path(impl_path):
    """Extract full qualified class name from Java file path."""
    impl_path = impl_path.replace('\\', '/')
    marker = 'src/main/java/'
    marker_idx = impl_path.find(marker)
    if marker_idx >= 0:
        pkg_path = impl_path[marker_idx + len(marker):]
        pkg_path = pkg_path.rsplit('/', 1)[0]
        pkg = pkg_path.replace('/', '.')
        cls_name = os.path.basename(impl_path).replace('.java', '')
        return f'{pkg}.{cls_name}' if pkg else cls_name
    return os.path.basename(impl_path).replace('.java', '')


# ── Prepare mode ───────────────────────────────────────────────────

def do_prepare(args):
    """Generate prompt context for each verified dispatch point."""
    pi_path = os.path.join(args.cache_dir, "phase1c", "pattern-index.json")
    if not os.path.exists(pi_path):
        print("ERROR: pattern-index.json not found", file=sys.stderr)
        sys.exit(1)
    patterns = _load_json(pi_path).get("patterns", [])

    phase2b_dir = os.path.join(args.cache_dir, "phase2b")
    os.makedirs(phase2b_dir, exist_ok=True)

    prepared = 0
    skipped = 0

    for pat in patterns:
        if not pat.get("_verified"):
            skipped += 1
            continue

        interface = pat["interface"]
        short_name = interface.rsplit(".", 1)[-1]
        methods = pat.get("interfaceMethods", pat.get("methods", []))
        dispatch_type = pat.get("type", "UNKNOWN")

        # Support both formats: implementations (objects) and implFiles (paths)
        if "implementations" in pat:
            implementations = pat["implementations"]
        else:
            implementations = []
            for impl_path in pat.get("implFiles", []):
                impl_path = impl_path.replace('\\', '/')
                module = impl_path.split('/')[0] if '/' in impl_path else ""
                full_class = _class_from_path(impl_path)
                implementations.append({
                    "class": full_class,
                    "filePath": impl_path,
                    "module": module,
                    "parentAbstract": "",
                })

        context = {
            "interface": interface,
            "shortName": short_name,
            "interface_methods": methods,
            "dispatch_type": dispatch_type,
            "implementations": implementations,
            "implCount": len(implementations),
            "needsBatching": len(implementations) > 30,
            "batchSize": 18,
        }

        context_path = os.path.join(phase2b_dir, "tmp", f"_prepare-context-{short_name}.json")
        _save_json(context_path, context)
        prepared += 1

    print(json.dumps({
        "status": "prepared",
        "totalPatterns": prepared + skipped,
        "prepared": prepared,
        "skippedUnverified": skipped,
    }, indent=2))


# ── Merge mode ─────────────────────────────────────────────────────

def do_merge(args):
    """Merge multi-batch subagent results into a single dispatch-summary file."""
    phase2b_dir = os.path.join(args.cache_dir, "phase2b")
    pattern_name = args.pattern_name
    results_path = args.results

    if not results_path:
        print("ERROR: --results required for merge mode", file=sys.stderr)
        sys.exit(1)

    all_results = []

    if os.path.isdir(results_path):
        # Only load batch result files matching the pattern name
        batch_prefix = f"_batch-result-{pattern_name}-"
        for fname in sorted(os.listdir(results_path)):
            if not fname.endswith('.json'):
                continue
            if batch_prefix and not fname.startswith(batch_prefix):
                continue
            data = _load_json(os.path.join(results_path, fname))
            if isinstance(data, list):
                all_results.extend(data)
            elif isinstance(data, dict) and "results" in data:
                all_results.extend(data["results"])
    elif os.path.isfile(results_path):
        data = _load_json(results_path)
        if isinstance(data, list):
            all_results = data
        elif isinstance(data, dict) and "results" in data:
            all_results = data["results"]

    if not all_results:
        print("ERROR: No results to merge", file=sys.stderr)
        sys.exit(1)

    # Dedup by class
    seen = set()
    deduped = []
    for r in all_results:
        cls = r.get("class", "")
        if cls not in seen:
            seen.add(cls)
            deduped.append(r)

    # Get interface and dispatchType from context or pattern-index
    interface = ""
    dispatch_type = "UNKNOWN"
    context_path = os.path.join(phase2b_dir, "tmp", f"_prepare-context-{pattern_name}.json")
    if os.path.exists(context_path):
        ctx = _load_json(context_path)
        interface = ctx["interface"]
        dispatch_type = ctx["dispatch_type"]
    else:
        pi_path = os.path.join(args.cache_dir, "phase1c", "pattern-index.json")
        if os.path.exists(pi_path):
            for p in _load_json(pi_path).get("patterns", []):
                if p["interface"].rsplit(".", 1)[-1] == pattern_name:
                    interface = p["interface"]
                    dispatch_type = p.get("type", "UNKNOWN")
                    break

    output = {
        "interface": interface,
        "dispatchType": dispatch_type,
        "results": deduped,
    }

    output_path = os.path.join(phase2b_dir, f"dispatch-summary-{pattern_name}.json")
    _save_json(output_path, output)

    print(json.dumps({
        "status": "merged",
        "patternName": pattern_name,
        "totalResults": len(all_results),
        "dedupedResults": len(deduped),
        "duplicatesRemoved": len(all_results) - len(deduped),
        "outputPath": output_path,
    }, indent=2))


# ── Validate mode ──────────────────────────────────────────────────

def do_validate(args):
    """Check dispatch-summary completeness against pattern-index."""
    pi_path = os.path.join(args.cache_dir, "phase1c", "pattern-index.json")
    if not os.path.exists(pi_path):
        print("ERROR: pattern-index.json not found", file=sys.stderr)
        sys.exit(1)
    patterns = _load_json(pi_path).get("patterns", [])
    phase2b_dir = os.path.join(args.cache_dir, "phase2b")

    verified = [p for p in patterns if p.get("_verified")]
    total = len(verified)
    missing_files = []
    incomplete = []
    ok = 0

    for pat in verified:
        interface = pat["interface"]
        short_name = interface.rsplit(".", 1)[-1]
        summary_path = os.path.join(phase2b_dir, f"dispatch-summary-{short_name}.json")

        if not os.path.exists(summary_path):
            missing_files.append({
                "interface": interface,
                "shortName": short_name,
                "implCount": pat.get("implementationCount", pat.get("implCount", 0)),
            })
            continue

        data = _load_json(summary_path)
        results = data.get("results", [])
        result_classes = {r.get("class", "") for r in results}

        # Check all implementations are covered (support both formats)
        if "implementations" in pat:
            expected_classes = {impl["class"] for impl in pat["implementations"]
                               if isinstance(impl, dict) and "class" in impl}
            total_impls = len(pat["implementations"])
        else:
            expected_classes = set()
            for impl_path in pat.get("implFiles", []):
                expected_classes.add(_class_from_path(impl_path))
            total_impls = len(pat.get("implFiles", []))
        missing_impls = [cls for cls in expected_classes if cls not in result_classes]

        issues = []
        if missing_impls:
            issues.append(f"Missing {len(missing_impls)}/{total_impls} implementations")
        if not data.get("interface"):
            issues.append("Missing interface field")
        if not data.get("dispatchType"):
            issues.append("Missing dispatchType field")

        if issues:
            incomplete.append({
                "interface": interface,
                "shortName": short_name,
                "issues": issues,
                "missingImplementations": missing_impls[:5],
            })
        else:
            ok += 1

    report = {
        "status": "validated",
        "totalDispatchPoints": total,
        "ok": ok,
        "missingFiles": len(missing_files),
        "incompleteFiles": len(incomplete),
        "details": {
            "missing": missing_files,
            "incomplete": incomplete,
        },
    }

    report_path = os.path.join(phase2b_dir, "tmp", "_validate-report.json")
    _save_json(report_path, report)

    print(json.dumps({
        "status": "validated",
        "total": total,
        "ok": ok,
        "missingFiles": len(missing_files),
        "incompleteFiles": len(incomplete),
        "reportPath": report_path,
    }, indent=2))


# ── Main ───────────────────────────────────────────────────────────

MODES = {
    "prepare": do_prepare,
    "merge": do_merge,
    "validate": do_validate,
}


def main():
    args = parse_args()
    args.cache_dir = os.path.abspath(args.cache_dir)
    if hasattr(args, 'project_dir') and args.project_dir:
        args.project_dir = os.path.abspath(args.project_dir)
    fn = MODES.get(args.mode)
    if fn:
        fn(args)
    else:
        print(f"Unknown mode: {args.mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
