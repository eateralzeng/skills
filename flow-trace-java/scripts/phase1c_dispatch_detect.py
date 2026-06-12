"""Phase 1c: Dispatch Point Detection for flow-trace-java

Scans Java source code to identify polymorphic dispatch points:
interfaces/abstract classes with multiple concrete implementations.

Modes:
  detect          — implements + extends scan, output pattern-index.json (default)
  verify-prepare  — prepare LLM verification context from pattern-index.json
  verify-apply    — apply LLM verification results, update pattern-index.json

All filter rules are loaded from rules/dispatch-rules.md (config-driven).

Output: phase1c/pattern-index.json
"""
import json, os, re, argparse, subprocess, glob

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)


def parse_args():
    p = argparse.ArgumentParser(description="Phase 1c: Dispatch Point Detection")
    p.add_argument("--project-dir", help="Java project root directory")
    p.add_argument("--cache-dir", required=True, help="Cache root directory (.trace-cache/)")
    p.add_argument("--rules", default=os.path.join(SKILL_DIR, "rules", "dispatch-rules.md"),
                   help="Path to dispatch-rules.md")
    p.add_argument("--mode", choices=["detect", "verify-prepare", "verify-apply"],
                   default="detect", help="Operation mode (default: detect)")
    p.add_argument("--results", help="Path to LLM verification results JSON (verify-apply mode)")
    return p.parse_args()


# ── Rule loading ─────────────────────────────────────────────────────

def load_rules(rules_path):
    """Parse dispatch-rules.md into a dict of sections → list of values."""
    rules = {}
    current_section = None
    in_comment = False
    with open(rules_path) as f:
        for line in f:
            stripped = line.strip()
            # Skip HTML comments
            if "<!--" in stripped:
                in_comment = True
            if in_comment:
                if "-->" in stripped:
                    in_comment = False
                continue
            if stripped.startswith("## "):
                current_section = stripped[3:].strip()
                rules[current_section] = []
            elif stripped.startswith("- ") and current_section:
                rules[current_section].append(stripped[2:].strip())
    return rules


class DispatchConfig:
    """Filter configuration loaded from dispatch-rules.md."""

    def __init__(self, rules):
        self.noise_interfaces = set(rules.get("noise-interface", []))
        self.noise_interface_prefixes = tuple(rules.get("noise-interface-prefix", []))
        self.noise_interface_suffixes = tuple(rules.get("noise-interface-suffix", []))
        self.exclude_packages = tuple(rules.get("exclude-package", []))
        self.exclude_annotations = [a.lstrip("@") for a in rules.get("exclude-annotation", [])]
        self.exclude_directories = rules.get("exclude-directory", [])
        self.min_implementations = int(rules.get("min-implementations", ["2"])[0])

    def is_noise_interface(self, fqn):
        short = fqn.split(".")[-1]
        if short in self.noise_interfaces:
            return True
        if any(fqn.startswith(p) for p in self.noise_interface_prefixes):
            return True
        if any(fqn.startswith(p) for p in self.exclude_packages):
            return True
        if any(short.endswith(s) for s in self.noise_interface_suffixes):
            if any(fqn.startswith(p) for p in ("org.springframework.", "javax.", "jakarta.")):
                return True
        return False

    def is_excluded_annotation(self, source_line):
        return any(f"@{a}" in source_line for a in self.exclude_annotations)

    def is_excluded_directory(self, file_path):
        return any(f"/{d}/" in file_path for d in self.exclude_directories)


# ── Source scanning helpers ──────────────────────────────────────────

def _grep(project_dir, pattern, include="*.java", timeout=60):
    """Run grep and return matching lines."""
    try:
        r = subprocess.run(
            ["grep", "-rn", "--include", include, "-E", pattern, project_dir],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout
    except subprocess.TimeoutExpired:
        print(f"  [WARN] grep timeout for pattern: {pattern}")
        return ""


def _find_java_files(project_dir, class_name):
    """Find .java file for a class name."""
    matches = glob.glob(
        os.path.join(project_dir, "**", "src", "main", "java", "**", f"{class_name}.java"),
        recursive=True
    )
    return matches[0] if matches else None


def _get_package_from_file(file_path):
    """Extract package name from Java file."""
    try:
        with open(file_path) as f:
            for line in f:
                m = re.match(r"package\s+([\w.]+)\s*;", line)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return ""


# ── Implementation scanning ─────────────────────────────────────────

def _parse_implements_clause(line):
    """Parse 'implements A, B, C' clause, return list of interface names."""
    m = re.search(r"implements\s+([\w\s,.*]+?)(?:\s*\{|\s*extends|\s*$)", line)
    if not m:
        return []
    raw = m.group(1)
    parts = [p.strip().split(".")[-1] for p in raw.split(",")]
    return [p for p in parts if p and p[0].isupper()]


EXTENDS_RE = re.compile(r"extends\s+(\w+)")


def scan_implementations(project_dir, config):
    """Scan implements/extends, build interface -> implementing classes mapping."""
    interface_map = {}  # interface_short_name -> [(fqn_class, file_path, is_abstract)]

    output = _grep(project_dir, r"implements\s+\w+")
    for line in output.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        file_path, _, source = parts[0], parts[1], parts[2]

        if config.is_excluded_directory(file_path):
            continue
        if config.is_excluded_annotation(source):
            continue

        interfaces = _parse_implements_clause(source)
        class_match = re.search(r"(?:abstract\s+)?class\s+(\w+)", source)
        if not class_match:
            continue
        class_name = class_match.group(1)

        pkg = _get_package_from_file(file_path)
        fqn_class = f"{pkg}.{class_name}" if pkg else class_name

        is_abs = "abstract class" in source

        for iface_short in interfaces:
            if iface_short not in interface_map:
                interface_map[iface_short] = []
            interface_map[iface_short].append((fqn_class, file_path, is_abs))

    return interface_map


def resolve_concrete_classes(project_dir, fqn_class, file_path, is_abstract,
                            exclude_dirs, visited=None):
    """Recursively resolve to concrete classes. Returns list of (fqn, file_path)."""
    if visited is None:
        visited = set()
    if fqn_class in visited:
        return []
    visited.add(fqn_class)

    if not is_abstract:
        return [(fqn_class, file_path)]

    short_name = fqn_class.split(".")[-1]
    output = _grep(project_dir, rf"extends\s+{short_name}\b")

    results = []
    for line in output.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        sub_path, _, source = parts[0], parts[1], parts[2]
        if any(f"/{d}/" in sub_path for d in exclude_dirs):
            continue

        class_match = re.search(r"(?:abstract\s+)?class\s+(\w+)", source)
        if not class_match:
            continue
        sub_class = class_match.group(1)

        pkg = _get_package_from_file(sub_path)
        sub_fqn = f"{pkg}.{sub_class}" if pkg else sub_class
        sub_is_abs = "abstract class" in source

        results.extend(resolve_concrete_classes(
            project_dir, sub_fqn, sub_path, sub_is_abs, exclude_dirs, visited))

    return results


# ── Extends dispatch scanning ────────────────────────────────────────

def scan_extends_dispatch(project_dir, config, known_interface_names):
    """Scan extends abstract class dispatch points not covered by implements scan."""
    output = _grep(project_dir, r"extends\s+\w+")
    parent_children = {}  # parent_short → [(child_fqn, child_path, is_abstract)]

    for line in output.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        file_path, _, source = parts[0], parts[1], parts[2]

        if config.is_excluded_directory(file_path):
            continue
        if config.is_excluded_annotation(source):
            continue

        ext_match = EXTENDS_RE.search(source)
        if not ext_match:
            continue
        parent_short = ext_match.group(1)

        class_match = re.search(r"(?:abstract\s+)?class\s+(\w+)", source)
        if not class_match:
            continue
        child_name = class_match.group(1)

        pkg = _get_package_from_file(file_path)
        child_fqn = f"{pkg}.{child_name}" if pkg else child_name
        is_abs = "abstract class" in source

        if parent_short not in parent_children:
            parent_children[parent_short] = []
        parent_children[parent_short].append((child_fqn, file_path, is_abs))

    dispatch_map = {}  # abstract_class_short → [(concrete_fqn, concrete_path)]

    for parent_short, children in parent_children.items():
        if len(children) < config.min_implementations:
            continue

        if parent_short in known_interface_names:
            continue

        # Verify parent is an abstract class (not interface, not noise)
        parent_file = _find_java_files(project_dir, parent_short)
        if not parent_file:
            continue
        try:
            with open(parent_file) as f:
                content = f.read()
        except Exception:
            continue

        if not re.search(r"abstract\s+class\s+" + re.escape(parent_short), content):
            continue

        pkg = _get_package_from_file(parent_file)
        parent_fqn = f"{pkg}.{parent_short}" if pkg else parent_short
        if config.is_noise_interface(parent_fqn):
            continue

        # Skip abstract classes with no abstract methods (pure utility base class)
        if not re.search(r"\babstract\s+\S+\s+\w+\s*\(", content):
            continue

        # Resolve to concrete classes
        all_concrete = []
        for child_fqn, child_path, is_abs in children:
            concretes = resolve_concrete_classes(
                project_dir, child_fqn, child_path, is_abs,
                config.exclude_directories)
            all_concrete.extend(concretes)

        seen = set()
        unique = []
        for fqn, fpath in all_concrete:
            if fqn not in seen:
                seen.add(fqn)
                unique.append((fqn, fpath))

        if len(unique) >= config.min_implementations:
            dispatch_map[parent_short] = (parent_fqn, parent_file, content, unique)

    return dispatch_map


# ── Context class detection ──────────────────────────────────────────

def find_context_classes(project_dir, interface_name, config):
    """Find classes that inject/use this interface (field declarations)."""
    short = interface_name.split(".")[-1]
    output = _grep(project_dir, rf"(private|protected|List|Map)\s+.*\b{short}\b")

    contexts = []
    context_classes = []  # Classes with "Context" in name get priority
    for line in output.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        file_path, _, source = parts[0], parts[1], parts[2]
        if config.is_excluded_directory(file_path):
            continue
        # Skip class declaration lines (implements/extends)
        if "implements " in source or "extends " in source.split("//")[0]:
            if re.search(r"(?:abstract\s+)?class\s+\w+", source):
                continue
        # Skip the interface file itself
        if f"/{short}.java" in file_path:
            continue

        class_name = os.path.basename(file_path).replace(".java", "")
        # Skip abstract classes — they are not dispatch contexts
        if class_name.startswith("Abstract"):
            continue
        pkg = _get_package_from_file(file_path)
        fqn = f"{pkg}.{class_name}" if pkg else class_name

        entry = (fqn, file_path, source.strip())
        if "Context" in class_name:
            context_classes.append(entry)
        else:
            contexts.append(entry)

    return context_classes if context_classes else contexts[:3]


def detect_dispatch_type(context_file_content):
    """Detect dispatch type from context class source."""
    has_stream = bool(re.search(r"\.stream\(\)", context_file_content))
    has_filter = bool(re.search(r"\.filter\(|\bfilter\(", context_file_content))
    if has_stream and has_filter:
        return "STREAM_DISPATCH"
    if re.search(r"\.get\(\s*\w+\s*\)", context_file_content):
        return "MAP_DISPATCH"
    if re.search(r"switch\s*\(|else\s+if\s*\(", context_file_content):
        return "SWITCH_DISPATCH"
    return "UNKNOWN"


def _is_data_interface(methods):
    """Check if all methods are getters/setters (DTO/VO data interface)."""
    if not methods:
        return False
    for m in methods:
        if m.startswith(("get", "set", "is", "with")):
            continue
        return False
    return True


def _extract_interface_methods(iface_file):
    """Extract method names from interface file, skipping default/static bodies."""
    if not iface_file or not os.path.isfile(iface_file):
        return []
    try:
        with open(iface_file) as f:
            content = f.read()
        # Strip default/static method bodies via brace counting
        decl_lines = []
        brace_depth = 0
        in_body = False
        for line in content.splitlines():
            stripped = line.strip()
            if brace_depth == 0 and re.match(r"(default|static)\s+", stripped):
                before_brace = stripped.split("{")[0] if "{" in stripped else stripped
                decl_lines.append(before_brace)
                if "{" in stripped:
                    brace_depth += stripped.count("{") - stripped.count("}")
                    in_body = brace_depth > 0
                continue
            if in_body or brace_depth > 0:
                brace_depth += stripped.count("{") - stripped.count("}")
                if brace_depth <= 0:
                    in_body = False
                continue
            decl_lines.append(stripped)
        cleaned = "\n".join(decl_lines)
        methods = re.findall(
            r"(?:public\s+|default\s+)?[\w<>\[\],\s]+\s+(\w+)\s*\(",
            cleaned
        )
        return [m for m in methods if m not in
                ("equals", "hashCode", "toString", "getClass")]
    except Exception:
        return []


# ── Main flow ────────────────────────────────────────────────────────

def do_detect(args):
    project_dir = args.project_dir
    cache_dir = args.cache_dir

    # Load rules
    config = DispatchConfig(load_rules(args.rules))
    print(f"[Phase 1c] Loaded rules from {args.rules}")

    print("[Phase 1c] Scanning implementations...")
    interface_map = scan_implementations(project_dir, config)

    print(f"[Phase 1c] Found {len(interface_map)} interfaces with implementations")

    patterns = []

    for iface_short, impl_list in sorted(interface_map.items()):
        # Find interface source file
        iface_files = glob.glob(
            os.path.join(project_dir, "**", "src", "main", "java", "**", f"{iface_short}.java"),
            recursive=True
        )
        iface_file = None
        for f in iface_files:
            if "/src/test/" not in f:
                try:
                    with open(f) as fh:
                        content = fh.read()
                    if re.search(rf"(?:public\s+)?interface\s+{iface_short}", content):
                        iface_file = f
                        break
                except Exception:
                    continue

        if not iface_file:
            continue

        pkg = _get_package_from_file(iface_file)
        iface_fqn = f"{pkg}.{iface_short}" if pkg else iface_short

        if config.is_noise_interface(iface_fqn):
            continue

        # Resolve to concrete classes
        all_concrete = []
        abstract_chain = []
        exclude_dirs = config.exclude_directories

        for fqn, fpath, is_abs in impl_list:
            if is_abs:
                abs_short = fqn.split(".")[-1]
                if abs_short not in abstract_chain:
                    abstract_chain.append(abs_short)
            concretes = resolve_concrete_classes(
                project_dir, fqn, fpath, is_abs, exclude_dirs)
            all_concrete.extend(concretes)

        seen = set()
        unique_concrete = []
        for fqn, fpath in all_concrete:
            if fqn not in seen:
                seen.add(fqn)
                unique_concrete.append((fqn, fpath))

        if len(unique_concrete) < config.min_implementations:
            continue

        # Extract interface methods
        iface_methods = _extract_interface_methods(iface_file)
        if _is_data_interface(iface_methods):
            continue

        # Find context class
        contexts = find_context_classes(project_dir, iface_fqn, config)
        context_class = None
        context_method = None
        dispatch_type = "UNKNOWN"

        if contexts:
            ctx_fqn, ctx_path, ctx_line = contexts[0]
            context_class = ctx_fqn.split(".")[-1]
            try:
                with open(ctx_path) as f:
                    dispatch_type = detect_dispatch_type(f.read())
            except Exception:
                pass

            # Find the method that calls the interface
            try:
                with open(ctx_path) as f:
                    content = f.read()
                method_matches = re.findall(
                    r"(?:public|private|protected)\s+\w+\s+(\w+)\s*\([^)]*\)\s*\{[^}]*" +
                    re.escape(iface_short) + r"[^}]*\}",
                    content, re.DOTALL
                )
                if method_matches:
                    context_method = method_matches[0]
            except Exception:
                pass

        # Build implementations list with parentAbstract info
        implementations = []
        for fqn, fpath in unique_concrete:
            impl_entry = {
                "class": fqn,
                "filePath": os.path.relpath(fpath, project_dir),
                "module": _extract_module(fpath, project_dir),
            }
            impl_short = fqn.split(".")[-1]
            impl_file = _find_java_files(project_dir, impl_short)
            if impl_file:
                try:
                    with open(impl_file) as f:
                        first_lines = f.read(2000)
                    ext = EXTENDS_RE.search(first_lines)
                    if ext:
                        parent = ext.group(1)
                        if parent != iface_short:
                            impl_entry["parentAbstract"] = parent
                except Exception:
                    pass
            implementations.append(impl_entry)

        pattern = {
            "type": dispatch_type,
            "interface": iface_fqn,
            "interfaceMethods": iface_methods,
        }
        if context_class:
            pattern["contextClass"] = context_class
        if context_method:
            pattern["contextMethod"] = context_method
        if abstract_chain:
            pattern["abstractChain"] = abstract_chain
        pattern["implementations"] = implementations
        pattern["implementationCount"] = len(implementations)

        patterns.append(pattern)

    patterns.sort(key=lambda p: p["implementationCount"], reverse=True)

    # ── Extends dispatch scan (pure abstract class, no interface) ──
    print("[Phase 1c] Scanning extends dispatch points...")
    known_names = set(p["interface"].split(".")[-1] for p in patterns)
    # Also include abstract chains and parent abstracts from implements scan
    for p in patterns:
        for ac in p.get("abstractChain", []):
            known_names.add(ac)
        for impl in p.get("implementations", []):
            pa = impl.get("parentAbstract")
            if pa:
                known_names.add(pa)
    extends_map = scan_extends_dispatch(project_dir, config, known_names)

    extends_count = 0
    for abs_short, (abs_fqn, abs_file, abs_content, concrete_list) in sorted(extends_map.items()):
        # Extract methods from abstract class
        abs_methods = _extract_class_abstract_methods(abs_content, abs_short)
        if _is_data_interface(abs_methods):
            continue

        # Find context class
        contexts = find_context_classes(project_dir, abs_fqn, config)
        context_class = None
        context_method = None
        dispatch_type = "UNKNOWN"

        if contexts:
            ctx_fqn, ctx_path, ctx_line = contexts[0]
            context_class = ctx_fqn.split(".")[-1]
            try:
                with open(ctx_path) as f:
                    dispatch_type = detect_dispatch_type(f.read())
            except Exception:
                pass

        # Build implementations
        implementations = []
        for fqn, fpath in concrete_list:
            impl_entry = {
                "class": fqn,
                "filePath": os.path.relpath(fpath, project_dir),
                "module": _extract_module(fpath, project_dir),
            }
            impl_short = fqn.split(".")[-1]
            impl_file = _find_java_files(project_dir, impl_short)
            if impl_file:
                try:
                    with open(impl_file) as f:
                        first_lines = f.read(2000)
                    ext = EXTENDS_RE.search(first_lines)
                    if ext and ext.group(1) != abs_short:
                        impl_entry["parentAbstract"] = ext.group(1)
                except Exception:
                    pass
            implementations.append(impl_entry)

        pattern = {
            "type": dispatch_type,
            "interface": abs_fqn,
            "interfaceMethods": abs_methods,
            "dispatchSource": "extends",
        }
        if context_class:
            pattern["contextClass"] = context_class
        if context_method:
            pattern["contextMethod"] = context_method
        pattern["implementations"] = implementations
        pattern["implementationCount"] = len(implementations)

        patterns.append(pattern)
        extends_count += 1

    patterns.sort(key=lambda p: p["implementationCount"], reverse=True)

    if extends_count > 0:
        print(f"[Phase 1c] Found {extends_count} extends-only dispatch points")

    # Write output
    output_dir = os.path.join(cache_dir, "phase1c")
    os.makedirs(output_dir, exist_ok=True)

    result = {
        "version": "2.0",
        "generator": "flow-trace-java",
        "totalPatterns": len(patterns),
        "patterns": patterns,
    }

    output_path = os.path.join(output_dir, "pattern-index.json")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"[Phase 1c] Found {len(patterns)} dispatch points")
    for p in patterns:
        iface = p["interface"].split(".")[-1]
        print(f"  - {iface}: {p['implementationCount']} implementations ({p['type']})")
    print(f"[Phase 1c] Output: {output_path}")

    return result


def _extract_module(file_path, project_dir):
    """Extract module name from file path."""
    rel = os.path.relpath(file_path, project_dir)
    parts = rel.split(os.sep)
    if parts and parts[0] not in ("src",):
        return parts[0]
    return ""


def _extract_class_abstract_methods(content, class_name):
    """Extract abstract method names from an abstract class."""
    methods = re.findall(
        r"(?:public|protected)?\s*abstract\s+[\w<>\[\],\s]+\s+(\w+)\s*\(",
        content
    )
    return [m for m in methods if m not in
            ("equals", "hashCode", "toString", "getClass")]


# ── Verify prepare / apply ───────────────────────────────────────────

VERIFY_BATCH_SIZE = 8


def _find_interface_file(project_dir, interface_fqn):
    """Find source file for an interface or abstract class."""
    short = interface_fqn.split(".")[-1]
    return _find_java_files(project_dir, short)


def do_verify_prepare(args):
    cache_dir = args.cache_dir
    project_dir = args.project_dir

    index_path = os.path.join(cache_dir, "phase1c", "pattern-index.json")
    with open(index_path) as f:
        index = json.load(f)

    batches = []
    current_batch = []

    for pattern in index["patterns"]:
        iface_fqn = pattern["interface"]
        iface_short = iface_fqn.split(".")[-1]

        # Find interface source file
        iface_file = None
        if project_dir:
            iface_file = _find_interface_file(project_dir, iface_fqn)
        # Fallback: try from first implementation's path
        if not iface_file and pattern.get("implementations"):
            impl_path = pattern["implementations"][0].get("filePath", "")
            if impl_path:
                parts = impl_path.split(os.sep)
                for i, part in enumerate(parts):
                    if part == "java":
                        candidate = os.path.join(
                            project_dir, *parts[:i+1],
                            *iface_fqn.split(".")[:-1],
                            f"{iface_short}.java"
                        ) if project_dir else None
                        if candidate and os.path.isfile(candidate):
                            iface_file = candidate
                            break

        entry = {
            "interface": iface_fqn,
            "interfaceFilePath": os.path.relpath(iface_file, project_dir) if (iface_file and project_dir) else None,
            "interfaceMethods": pattern.get("interfaceMethods", []),
            "implementationCount": pattern.get("implementationCount", 0),
            "type": pattern.get("type", "UNKNOWN"),
            "contextClass": pattern.get("contextClass"),
        }

        # Sample implementations (up to 3, prefer those with parentAbstract)
        impls = pattern.get("implementations", [])
        with_parent = [i for i in impls if i.get("parentAbstract")]
        without_parent = [i for i in impls if not i.get("parentAbstract")]
        samples = (with_parent + without_parent)[:3]
        entry["sampleImplementations"] = [
            {
                "class": s["class"],
                "filePath": s.get("filePath"),
                "parentAbstract": s.get("parentAbstract"),
            }
            for s in samples
        ]

        current_batch.append(entry)
        if len(current_batch) >= VERIFY_BATCH_SIZE:
            batches.append({
                "batchIndex": len(batches),
                "projectDir": project_dir,
                "patterns": current_batch,
            })
            current_batch = []

    if current_batch:
        batches.append({
            "batchIndex": len(batches),
            "projectDir": project_dir,
            "patterns": current_batch,
        })

    output = {
        "totalPatterns": len(index["patterns"]),
        "batchSize": VERIFY_BATCH_SIZE,
        "batches": batches,
    }

    output_path = os.path.join(cache_dir, "phase1c", "_verify-context.json")
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"[Phase 1c verify-prepare] {len(index['patterns'])} patterns → {len(batches)} batches")
    print(f"[Phase 1c verify-prepare] Output: {output_path}")
    return output


def do_verify_apply(args):
    cache_dir = args.cache_dir
    results_path = args.results

    if not results_path:
        print("[Phase 1c verify-apply] ERROR: --results required")
        return

    index_path = os.path.join(cache_dir, "phase1c", "pattern-index.json")
    with open(index_path) as f:
        index = json.load(f)

    with open(results_path) as f:
        verify_results = json.load(f)

    # Build lookup: interface FQN → verification result
    # Also build short-name fallback (LLM may change package path)
    verify_map = {}
    verify_short_map = {}
    for r in verify_results.get("results", []):
        verify_map[r["interface"]] = r
        short = r["interface"].split(".")[-1]
        verify_short_map[short] = r

    kept = []
    removed = []

    for pattern in index["patterns"]:
        iface = pattern["interface"]
        vr = verify_map.get(iface)
        # Fallback: match by short class name if FQN doesn't match
        if not vr:
            short = iface.split(".")[-1]
            vr = verify_short_map.get(short)
        if vr and not vr.get("verified", True):
            removed.append({
                "interface": iface,
                "reason": vr.get("reason", ""),
                "confidence": vr.get("confidence", "UNKNOWN"),
            })
        else:
            if vr:
                pattern["_verified"] = True
                pattern["_confidence"] = vr.get("confidence", "UNKNOWN")
            kept.append(pattern)

    # Write updated pattern-index
    result = {
        "version": "2.0",
        "generator": "flow-trace-java",
        "totalPatterns": len(kept),
        "patterns": kept,
    }

    with open(index_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Write report
    report = {
        "totalOriginal": len(index["patterns"]),
        "totalKept": len(kept),
        "totalRemoved": len(removed),
        "removed": removed,
    }
    report_path = os.path.join(cache_dir, "phase1c", "_verify-report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    print(f"[Phase 1c verify-apply] Kept {len(kept)}, removed {len(removed)}")
    for r in removed:
        print(f"  ✗ {r['interface'].split('.')[-1]}: {r['reason']}")
    print(f"[Phase 1c verify-apply] Output: {index_path}")
    print(f"[Phase 1c verify-apply] Report: {report_path}")


MODES = {
    "detect": do_detect,
    "verify-prepare": do_verify_prepare,
    "verify-apply": do_verify_apply,
}

if __name__ == "__main__":
    args = parse_args()
    if args.mode == "detect" and not args.project_dir:
        print("ERROR: --project-dir required for detect mode")
        exit(1)
    MODES[args.mode](args)
