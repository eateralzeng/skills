"""Phase 1: Entry Point Scanner for flow-trace-java

Rule-driven scanner. Reads entry-rules.json for discovery patterns,
then scans a Java project for Controller/RMB/Job entry points.

Output: phase1a/entries.json
"""
import json, os, re, argparse, subprocess, fnmatch
from _rmb_topic import RMBTOPIC_RE, METHOD_RE, resolve_const  # 共享 RMB topic 提取（单一事实，与 phase2a 对称）


def parse_args():
    p = argparse.ArgumentParser(description="Phase 1: Entry Point Scanner")
    p.add_argument("project_dir", help="Java project root directory")
    p.add_argument("cache_dir", help="Cache root directory (.trace-cache/)")
    p.add_argument("--rules", default=None,
                   help="Path to entry-rules.json (default: <script_dir>/../rules/entry-rules.json)")
    return p.parse_args()


# ── Rule Loading ────────────────────────────────────────────────────

def load_rules(rules_path):
    with open(rules_path) as f:
        return json.load(f)


def _default_rules_path():
    return os.path.join(os.path.dirname(__file__), '..', 'rules', 'entry-rules.json')


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ── Noise Filtering ────────────────────────────────────────────────

def build_noise_filter(noise_config):
    """Build a noise file checker from config.

    Applies filename-level noise exclusion only. Directory-level
    exclusion (test/, thirdparty/) is handled by grep/find flags.
    """
    suffixes = []
    for g in noise_config.get('globExclusions', []):
        suffix = g.split('/')[-1]  # e.g. "**/*DTO.java" -> "*DTO.java"
        # Skip directory-level patterns like "**" (from **/test/**)
        if suffix == '**' or not any(c in suffix for c in ('*', '?', '.')):
            continue
        if any(c in suffix for c in ('*', '?')):
            suffixes.append(suffix)
    name_noise = noise_config.get('classNameNoise', [])

    def is_noise(fpath):
        fname = os.path.basename(fpath)
        for suffix in suffixes:
            if fnmatch.fnmatch(fname, suffix):
                return True
        return any(n in fname for n in name_noise)

    return is_noise


# ── Constant Collection ────────────────────────────────────────────

def collect_constants(project_dir):
    constants = {}
    for root, dirs, files in os.walk(project_dir):
        if '/test/' in root or '/.git/' in root:
            continue
        for fname in files:
            if not fname.endswith('.java'):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath) as f:
                    content = f.read()
            except Exception:
                continue
            for m in re.finditer(
                r'(?:public\s+)?static\s+final\s+String\s+(\w+)\s*=\s*"([^"]*)"', content
            ):
                constants[m.group(1)] = m.group(2)
    return constants


# resolve_const 已移至共享模块 _rmb_topic（单一事实，见顶部 import）


# ── nodeId Construction ─────────────────────────────────────────────

def build_node_id(fpath, method_name):
    """Build nodeId from file path and method name.

    Format: 模块名:包名.类名:方法名
    e.g. cbrc-bs-jrp:com.webank.cbrc.jrp.handler.teller.SomeHandler:doJob
    """
    parts = fpath.replace('\\', '/').split('/')
    # Module: first segment (e.g. "cbrc-bs-jrp")
    module = parts[0] if parts else ''
    # Package: from after "src/main/java/" to before filename
    marker = 'src/main/java/'
    marker_idx = fpath.find(marker)
    if marker_idx >= 0:
        pkg_path = fpath[marker_idx + len(marker):]
        pkg_path = pkg_path.rsplit('/', 1)[0]  # remove filename
        pkg = pkg_path.replace('/', '.')
    else:
        pkg = ''
    cls = os.path.basename(fpath).replace('.java', '')
    full_class = f'{pkg}.{cls}' if pkg else cls
    return f'{module}:{full_class}:{method_name}'


# ── File Discovery ─────────────────────────────────────────────────

def _grep_files(project_dir, pattern):
    """Use grep to find files matching a pattern."""
    result = subprocess.run(
        ['grep', '-rn', '--include=*.java', '-l', '-E', pattern, '.'],
        capture_output=True, text=True, cwd=project_dir
    )
    files = []
    for f in result.stdout.strip().split('\n'):
        if not f or '/test/' in f:
            continue
        # Normalize ./ prefix
        if f.startswith('./'):
            f = f[2:]
        files.append(f)
    return files


def _glob_files(project_dir, glob_pattern):
    """Use find to locate files by name pattern."""
    name_pattern = glob_pattern.split('/')[-1]
    result = subprocess.run(
        ['find', '.', '-path', '*/src/main/java/*', '-name', name_pattern,
         '-not', '-path', '*/test/*'],
        capture_output=True, text=True, cwd=project_dir
    )
    files = []
    for f in result.stdout.strip().split('\n'):
        if not f:
            continue
        if f.startswith('./'):
            f = f[2:]
        files.append(f)
    return files


def discover_files(project_dir, discovery_config):
    """Discover candidate files from a discovery config (single or list)."""
    if isinstance(discovery_config, dict):
        discovery_config = [discovery_config]

    all_files = set()
    for rule in discovery_config:
        grep_pat = rule.get('grepPattern')
        glob_pat = rule.get('glob')
        if grep_pat:
            all_files.update(_grep_files(project_dir, grep_pat))
        elif glob_pat:
            all_files.update(_glob_files(project_dir, glob_pat))

    return sorted(all_files)


# ── Entry Extraction ───────────────────────────────────────────────

MAPPING_RE = re.compile(
    r'@(PostMapping|GetMapping|PutMapping|DeleteMapping|RequestMapping)\(["\']([^"\']*)["\']\)'
)

# RMBTOPIC_RE / METHOD_RE 已移至共享模块 _rmb_topic（单一事实，见顶部 import）

JOB_METHOD_RE = re.compile(
    r'(?:protected|public)\s+(?:(?:synchronized|final|static)\s+)*\w+\s+(\w+)\s*\('
)


def _extract_controller_entries(files, project_dir, type_config, counters):
    """Extract controller entries: one per HTTP mapping method."""
    entries = []
    markers = type_config.get('annotationMarkers', [])
    mapping_annots = type_config.get('mappingAnnotations', [])

    for fpath in files:
        full_path = os.path.join(project_dir, fpath)
        try:
            with open(full_path) as f:
                content = f.read()
        except Exception:
            continue

        if not any(m in content for m in markers):
            continue

        cls = os.path.basename(fpath).replace('.java', '')

        for m in MAPPING_RE.finditer(content):
            mapping_type, path = m.group(1), m.group(2)
            if mapping_type.replace('Mapping', 'Mapping') not in [a.replace('@', '') for a in mapping_annots]:
                # Still extract — the mapping annotation set is for documentation
                pass
            rest = content[m.end():m.end() + 500]
            mm = METHOD_RE.search(rest)
            if not mm:
                continue
            counters['total'] += 1
            method_name = mm.group(1)
            entries.append({
                'id': f"controller-{counters['total']:03d}",
                'type': 'controller',
                'className': cls,
                'methodName': method_name,
                'filePath': fpath,
                'httpMapping': f'{mapping_type}({path})',
                'rmbTopic': None,
                'nodeId': build_node_id(fpath, method_name),
            })
    return entries


def _extract_rmb_entries(files, project_dir, type_config, constants, counters):
    """Extract RMB handler entries: one per @RmbTopic method."""
    entries = []
    seen_node_ids = set()
    markers = type_config.get('annotationMarkers', [])

    for fpath in files:
        full_path = os.path.join(project_dir, fpath)
        try:
            with open(full_path) as f:
                content = f.read()
        except Exception:
            continue

        if not any(m in content for m in markers):
            continue

        cls = os.path.basename(fpath).replace('.java', '')

        for m in RMBTOPIC_RE.finditer(content):
            params = m.group(1)

            # Topic
            topic_lit = re.search(r'topic\s*=\s*"([^"]*)"', params)
            topic_ref = re.search(r'topic\s*=\s*([A-Za-z_][\w.]*)', params)
            if topic_lit:
                topic = topic_lit.group(1)
            elif topic_ref:
                topic = resolve_const(topic_ref.group(1), constants)
            else:
                topic = ''

            # TopicMode
            mode_m = re.search(r'topicMode\s*=\s*(?:TopicMode\.)?(\w+)', params)
            topic_mode = mode_m.group(1) if mode_m else ''

            # TransCode
            trans_m = re.search(r'transCode\s*=\s*([A-Za-z_][\w.]*)', params)
            trans_code = resolve_const(trans_m.group(1), constants) if trans_m else None

            # Method
            rest = content[m.end():m.end() + 500]
            mm = METHOD_RE.search(rest)
            method_name = mm.group(1) if mm else 'execute'

            node_id = build_node_id(fpath, method_name)
            if node_id in seen_node_ids:
                continue
            seen_node_ids.add(node_id)

            counters['total'] += 1
            entries.append({
                'id': f"rmb-{counters['total']:03d}",
                'type': 'rmb',
                'className': cls,
                'methodName': method_name,
                'filePath': fpath,
                'httpMapping': None,
                'rmbTopic': topic,
                'rmbTopicMode': topic_mode,
                'transCode': trans_code,
                'nodeId': node_id,
            })
    return entries


def _extract_extends(content):
    """Extract the direct parent class name from source content."""
    m = re.search(r'\bextends\s+([A-Za-z_]\w*)', content)
    return m.group(1) if m else None


def _build_class_index(project_dir):
    """Build className -> filePath index with one find call."""
    result = subprocess.run(
        ['find', '.', '-path', '*/src/main/java/*', '-name', '*.java',
         '-not', '-path', '*/test/*'],
        capture_output=True, text=True, cwd=project_dir
    )
    index = {}
    for f in result.stdout.strip().split('\n'):
        if not f:
            continue
        if f.startswith('./'):
            f = f[2:]
        cls = os.path.basename(f).replace('.java', '')
        index[cls] = f
    return index


def _find_class_source(project_dir, class_name, class_index):
    """Read source file for a class name using pre-built index."""
    fpath = class_index.get(class_name)
    if not fpath:
        return None
    try:
        with open(os.path.join(project_dir, fpath)) as fp:
            return fp.read()
    except Exception:
        return None


def _extract_method_annotation(content):
    """Extract first entry method name from method-level annotations."""
    methods = _extract_all_method_annotations(content)
    return methods[0] if methods else None


def _extract_all_method_annotations(content):
    """Extract ALL entry method names from method-level annotations (@Scheduled, @XxlJob)."""
    results = []
    for annot_re in (r'@Scheduled\s*\([^)]*\)', r'@XxlJob\s*\([^)]*\)'):
        for m in re.finditer(annot_re, content):
            rest = content[m.end():]
            mm = JOB_METHOD_RE.search(rest[:500])
            if mm:
                results.append(mm.group(1))
    return results


def _resolve_inheritance_chain(content, inheritance_map, project_dir, class_index):
    """Resolve entry method by traversing inheritance chain against inheritanceMethodMap."""
    current = _extract_extends(content)
    visited = set()
    depth = 0
    while current and current not in visited and depth < 5:
        visited.add(current)
        depth += 1
        if current in inheritance_map:
            return inheritance_map[current]
        parent_src = _find_class_source(project_dir, current, class_index)
        if not parent_src:
            break
        current = _extract_extends(parent_src)
    return None


def _resolve_job_method(content, type_config, project_dir, annotation_level='infer', class_index=None):
    """Resolve the entry method name for a Job class.

    annotation_level determines the strategy:
    - "method": extract from annotation position only
    - "class": inheritance chain traversal only
    - "infer": try method first, then inheritance chain
    """
    inheritance_map = type_config.get('inheritanceMethodMap', {})

    if annotation_level == 'method':
        return _extract_method_annotation(content)
    elif annotation_level == 'class':
        return _resolve_inheritance_chain(content, inheritance_map, project_dir, class_index)
    else:
        result = _extract_method_annotation(content)
        if result:
            return result
        return _resolve_inheritance_chain(content, inheritance_map, project_dir, class_index)


def _determine_job_level(content, type_config):
    """Determine annotationLevel from source content."""
    discovery = type_config.get('discovery', [])
    method_grep = [r['grepPattern'] for r in discovery
                   if r.get('annotationLevel') == 'method' and 'grepPattern' in r]
    class_grep = [r['grepPattern'] for r in discovery
                  if r.get('annotationLevel') == 'class' and 'grepPattern' in r]
    for pat in method_grep:
        if re.search(pat, content):
            return 'method'
    for pat in class_grep:
        if re.search(pat, content):
            return 'class'
    return 'infer'


def _extract_job_entries(files, project_dir, type_config, counters, seen):
    """Extract Job entries: one per annotated method (method level) or one per class (class/infer)."""
    entries = []
    unresolved = []
    seen_node_ids = set()
    class_index = _build_class_index(project_dir)

    for fpath in files:
        full_path = os.path.join(project_dir, fpath)
        try:
            with open(full_path) as f:
                content = f.read()
        except Exception:
            continue

        if re.search(r'\babstract\s+class\b', content):
            continue

        cls = os.path.basename(fpath).replace('.java', '')
        if cls in seen:
            continue
        seen.add(cls)

        level = _determine_job_level(content, type_config)

        if level == 'method':
            for method_name in _extract_all_method_annotations(content):
                node_id = build_node_id(fpath, method_name)
                if node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node_id)
                counters['total'] += 1
                entries.append({
                    'id': f"job-{counters['total']:03d}",
                    'type': 'job',
                    'className': cls,
                    'methodName': method_name,
                    'filePath': fpath,
                    'httpMapping': None,
                    'rmbTopic': None,
                    'nodeId': node_id,
                })
        else:
            method_name = _resolve_job_method(content, type_config, project_dir, level, class_index)
            if method_name:
                node_id = build_node_id(fpath, method_name)
                if node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node_id)
                counters['total'] += 1
                entries.append({
                    'id': f"job-{counters['total']:03d}",
                    'type': 'job',
                    'className': cls,
                    'methodName': method_name,
                    'filePath': fpath,
                    'httpMapping': None,
                    'rmbTopic': None,
                    'nodeId': node_id,
                })
            else:
                extends = _extract_extends(content)
                if extends:
                    parent_src = _find_class_source(project_dir, extends, class_index)
                    if parent_src:
                        reason = 'inheritance_chain_miss'
                        hint = f'父类 {extends} 不在 inheritanceMethodMap 中，链上未找到已知框架基类'
                    else:
                        reason = 'parent_source_not_found'
                        hint = f'父类 {extends} 源码不在项目中（可能在 JAR 中），无法继续遍历'
                else:
                    reason = 'no_annotation_no_inheritance'
                    hint = '无方法级注解，无继承关系'
                unresolved.append({
                    'className': cls,
                    'filePath': fpath,
                    'extends': extends,
                    'reason': reason,
                    'hint': hint,
                })

    return entries, unresolved


# ── Main ────────────────────────────────────────────────────────────

def main():
    args = parse_args()
    project_dir = os.path.abspath(args.project_dir)
    cache_dir = os.path.abspath(args.cache_dir)
    rules_path = os.path.abspath(args.rules) if args.rules else os.path.abspath(_default_rules_path())

    print("Phase 1: Entry Point Scanner (flow-trace-java)")
    print("=" * 50)

    print(f"\n[Init] Loading rules from {rules_path}")
    rules = load_rules(rules_path)
    is_noise = build_noise_filter(rules.get('noise', {}))

    print("\n[Step 1] Collecting static final String constants...")
    constants = collect_constants(project_dir)
    print(f"  Found {len(constants)} constants")
    # 落盘常量表，供 phase2a merge 解析发送端 @RmbTopic 常量引用（VERIFY-01）
    constants_path = os.path.join(cache_dir, 'phase1a', 'constants.json')
    _save_json(constants_path, constants)
    print(f"  Constants table: {constants_path}")

    all_entries = []
    counters = {'total': 0}
    job_seen = set()

    for type_config in rules.get('entryTypes', []):
        entry_type = type_config['type']
        discovery = type_config.get('discovery', [])

        print(f"\n[Step] Scanning {entry_type} entries...")
        raw_files = discover_files(project_dir, discovery)
        files = [f for f in raw_files if not is_noise(f)]
        print(f"  Candidate files: {len(files)}")

        if entry_type == 'controller':
            entries = _extract_controller_entries(files, project_dir, type_config, counters)
        elif entry_type == 'rmb':
            entries = _extract_rmb_entries(files, project_dir, type_config, constants, counters)
        elif entry_type == 'job':
            entries, job_unresolved = _extract_job_entries(files, project_dir, type_config, counters, job_seen)
            if job_unresolved:
                unresolved_path = os.path.join(cache_dir, 'phase1a', 'unresolved-jobs.json')
                _save_json(unresolved_path, job_unresolved)
                print(f"  WARNING: {len(job_unresolved)} 个 Job 入口无法自动解析")
                print(f"  详情见 {unresolved_path}")
        else:
            print(f"  Unknown type: {entry_type}, skipping")
            continue

        print(f"  Found {len(entries)} {entry_type} entries")
        all_entries.extend(entries)

    summary = {}
    for e in all_entries:
        summary[e['type']] = summary.get(e['type'], 0) + 1
    summary['total'] = len(all_entries)

    output = {
        'version': '2.0',
        'generator': 'flow-trace-java',
        'entries': all_entries,
        'summary': summary,
    }

    out_path = os.path.join(cache_dir, 'phase1a', 'entries.json')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nPhase 1 Complete!")
    for t in ('controller', 'rmb', 'job'):
        print(f"  {t.capitalize()}: {summary.get(t, 0)}")
    print(f"  Total: {summary['total']}")
    print(f"  Output: {out_path}")


if __name__ == '__main__':
    main()
